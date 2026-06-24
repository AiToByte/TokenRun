"""TokenRun API — FastBackend for the Cockpit command tower."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from core.models import Runfile

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TokenRun API",
    version="0.1.0",
    description="Industrial-grade AI task execution engine — Cockpit API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory state (production would use Redis/DB)
# ---------------------------------------------------------------------------

_active_missions: Dict[str, Dict[str, Any]] = {}
_ws_clients: List[WebSocket] = {}
_mission_events: Dict[str, asyncio.Event] = {}  # mission_id → approval event

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class MissionCreate(BaseModel):
    runfile_path: str = "runfiles/test_mission.yaml"
    sample_only: bool = False
    priority: str = "normal"  # "high" | "normal" | "low"


class MissionStatus(BaseModel):
    mission_id: str
    status: str  # "pending" | "sampling" | "awaiting_approval" | "running" | "completed" | "failed"
    phase: str
    progress: float
    cost_usd: float
    success_count: int
    total_count: int


class ApprovalRequest(BaseModel):
    action: str  # "approve" | "revise" | "abort"
    new_prompt: Optional[str] = None


class ReviseRequest(BaseModel):
    new_prompt: str
    change_log: str = ""


class SkillInfo(BaseModel):
    skill_id: str
    name: str
    created_at: str


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> Dict[str, str]:
    """Health check."""
    return {"status": "ok", "version": "0.1.0"}


@app.get("/missions", response_model=List[MissionStatus])
async def list_missions() -> List[MissionStatus]:
    """List all missions."""
    return [
        MissionStatus(
            mission_id=mid,
            status=m.get("status", "unknown"),
            phase=m.get("phase", ""),
            progress=m.get("progress", 0.0),
            cost_usd=m.get("cost_usd", 0.0),
            success_count=m.get("success_count", 0),
            total_count=m.get("total_count", 0),
        )
        for mid, m in _active_missions.items()
    ]


@app.post("/missions", response_model=MissionStatus)
async def create_mission(req: MissionCreate) -> MissionStatus:
    """Create and start a new mission."""
    if not Path(req.runfile_path).exists():
        raise HTTPException(404, f"Runfile not found: {req.runfile_path}")

    mission_id = f"mission-{uuid.uuid4().hex[:8]}"
    _active_missions[mission_id] = {
        "status": "pending",
        "phase": "INIT",
        "progress": 0.0,
        "cost_usd": 0.0,
        "success_count": 0,
        "total_count": 0,
        "runfile_path": req.runfile_path,
        "sample_only": req.sample_only,
    }
    _mission_events[mission_id] = asyncio.Event()  # approval signal

    # Broadcast status
    await _broadcast({
        "type": "STATUS_UPDATE",
        "mission_id": mission_id,
        "phase": "INIT",
    })

    # Start mission in background
    asyncio.create_task(_run_mission_bg(mission_id, req))

    return MissionStatus(
        mission_id=mission_id,
        status="pending",
        phase="INIT",
        progress=0.0,
        cost_usd=0.0,
        success_count=0,
        total_count=0,
    )


@app.get("/missions/{mission_id}", response_model=MissionStatus)
async def get_mission(mission_id: str) -> MissionStatus:
    """Get mission status."""
    m = _active_missions.get(mission_id)
    if not m:
        raise HTTPException(404, "Mission not found")
    return MissionStatus(
        mission_id=mission_id,
        status=m.get("status", "unknown"),
        phase=m.get("phase", ""),
        progress=m.get("progress", 0.0),
        cost_usd=m.get("cost_usd", 0.0),
        success_count=m.get("success_count", 0),
        total_count=m.get("total_count", 0),
    )


@app.post("/missions/{mission_id}/approve")
async def approve_mission(mission_id: str, req: ApprovalRequest) -> Dict[str, str]:
    """Approve, revise, or abort a mission."""
    m = _active_missions.get(mission_id)
    if not m:
        raise HTTPException(404, "Mission not found")

    if req.action == "approve":
        m["status"] = "running"
        m["phase"] = "FULL_PRODUCTION"
        # Signal the background task to continue
        event = _mission_events.get(mission_id)
        if event:
            event.set()
        await _broadcast({"type": "STATUS_UPDATE", "mission_id": mission_id, "phase": "APPROVED"})
        return {"status": "approved"}

    elif req.action == "abort":
        m["status"] = "failed"
        m["phase"] = "ABORTED"
        await _broadcast({"type": "STATUS_UPDATE", "mission_id": mission_id, "phase": "ABORTED"})
        return {"status": "aborted"}

    elif req.action == "revise":
        m["status"] = "pending"
        m["phase"] = "REVISING"
        return {"status": "revision_requested"}

    raise HTTPException(400, f"Unknown action: {req.action}")


@app.post("/missions/{mission_id}/revise")
async def revise_mission(mission_id: str, req: ReviseRequest) -> Dict[str, str]:
    """Edit & Resample: modify prompt and trigger re-sampling."""
    m = _active_missions.get(mission_id)
    if not m:
        raise HTTPException(404, "Mission not found")

    m["status"] = "pending"
    m["phase"] = "RESAMPLING"
    m["prompt_revision"] = {
        "new_prompt": req.new_prompt,
        "change_log": req.change_log,
    }
    await _broadcast({
        "type": "PROMPT_REVISED",
        "mission_id": mission_id,
        "new_prompt": req.new_prompt,
    })
    return {"status": "revision_applied", "phase": "RESAMPLING"}


@app.get("/missions/{mission_id}/traces")
async def get_traces(mission_id: str) -> List[Dict[str, Any]]:
    """Get execution traces for a mission."""
    m = _active_missions.get(mission_id)
    if not m:
        raise HTTPException(404, "Mission not found")
    return m.get("traces", [])


@app.get("/skills", response_model=List[SkillInfo])
async def list_skills() -> List[SkillInfo]:
    """List all solidified skills."""
    vault = Path("vault")
    if not vault.exists():
        return []
    skills = []
    for f in vault.glob("*.trs"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            skills.append(SkillInfo(
                skill_id=data.get("skill_id", f.stem),
                name=data.get("name", "Unknown"),
                created_at=data.get("created_at", ""),
            ))
        except (json.JSONDecodeError, OSError) as exc:
            import warnings
            warnings.warn(f"Failed to read skill file {f}: {exc}")
    return skills


@app.post("/skills/{skill_id}/run")
async def run_skill(skill_id: str) -> MissionStatus:
    """Run a solidified skill — load locked params and start a new mission."""
    from core.models import Runfile
    from dotenv import load_dotenv
    load_dotenv()

    # Find the skill file
    vault = Path("vault")
    skill_file = vault / f"{skill_id}.trs"
    if not skill_file.exists():
        # Also check skills/library
        skill_file = Path("skills/library") / f"{skill_id}.trs"
    if not skill_file.exists():
        raise HTTPException(404, f"Skill not found: {skill_id}")

    skill_data = json.loads(skill_file.read_text(encoding="utf-8"))

    # Build Runfile from skill
    from core.models import LoopConfig, TaskNode, ValidationRule
    exit_criteria = []
    for rule_data in skill_data.get("validation_rules", []):
        exit_criteria.append(ValidationRule(**rule_data))

    node = TaskNode(
        id=skill_id,
        name=skill_data.get("name", skill_id),
        actor_prompt_template=skill_data.get("optimized_prompt", ""),
        loop_config=LoopConfig(max_attempts=3, exit_criteria=exit_criteria),
    )
    runfile = Runfile(name=f"Skill: {skill_data.get('name', skill_id)}", workflow=[node])

    # Create mission
    mission_id = f"skill-{uuid.uuid4().hex[:8]}"
    _active_missions[mission_id] = {
        "status": "pending",
        "phase": "INIT",
        "progress": 0.0,
        "cost_usd": 0.0,
        "success_count": 0,
        "total_count": 0,
        "skill_id": skill_id,
    }

    # Start in background
    asyncio.create_task(_run_skill_bg(mission_id, runfile))

    return MissionStatus(
        mission_id=mission_id,
        status="pending",
        phase="INIT",
        progress=0.0,
        cost_usd=0.0,
        success_count=0,
        total_count=0,
    )


async def _run_skill_bg(mission_id: str, runfile: Runfile) -> None:
    """Run a skill-based mission in the background."""
    m = _active_missions.get(mission_id)
    if not m:
        return

    try:
        m["status"] = "running"
        m["phase"] = "RUNNING"
        await _broadcast({"type": "STATUS_UPDATE", "mission_id": mission_id, "phase": "RUNNING"})

        from core.app import TokenRunApp
        from main import build_providers

        actor_provider, critic_provider = build_providers(runfile)
        try:
            app = TokenRunApp(runfile, actor_provider, critic_provider)
            result = await app.run_mission(auto_approve=True)

            m["status"] = "completed"
            m["phase"] = "DONE"
            m["progress"] = 1.0
            m["result"] = result
            m["cost_usd"] = result.get("ledger_summary", {}).get("total_cost", "$0.0000")
            m["success_count"] = result.get("success_count", 0)
            m["total_count"] = result.get("total_count", 0)
            await _broadcast({"type": "STATUS_UPDATE", "mission_id": mission_id, "phase": "COMPLETED"})
        finally:
            await actor_provider.close()
            await critic_provider.close()

    except Exception as exc:
        m["status"] = "failed"
        m["phase"] = "ERROR"
        await _broadcast({"type": "ERROR", "mission_id": mission_id, "error": str(exc)})


@app.get("/missions/{mission_id}/lineage")
async def get_lineage(mission_id: str) -> List[Dict[str, Any]]:
    """Get the prompt version lineage for a mission."""
    m = _active_missions.get(mission_id)
    if not m:
        raise HTTPException(404, "Mission not found")
    # Return stored lineage info if available
    return m.get("lineage", [])


@app.post("/missions/{mission_id}/export")
async def export_fine_tune(
    mission_id: str,
    format: str = "openai",
    min_score: float = 0.8,
) -> Dict[str, str]:
    """Export mission traces as a fine-tuning dataset."""
    m = _active_missions.get(mission_id)
    if not m:
        raise HTTPException(404, "Mission not found")

    result = m.get("result", {})
    traces = []
    if result:
        # Build traces from the mission results
        for r in result.get("results", []):
            traces.append({
                "status": r.get("status"),
                "history": r.get("history", []),
                "final_output": r.get("final_output", ""),
            })

    if not traces:
        raise HTTPException(400, "No traces available for export")

    from core.solidifier import SkillSolidifier
    solidifier = SkillSolidifier()
    try:
        file_path = solidifier.export_fine_tune(
            traces, format=format, min_score=min_score
        )
        return {"file_path": file_path, "format": format, "count": len(traces)}
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Real-time event stream for the Cockpit UI."""
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            # Keep alive; client can send ping/pong
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.remove(ws)


async def _broadcast(message: Dict[str, Any]) -> None:
    """Send a message to all connected WebSocket clients."""
    dead: List[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


# ---------------------------------------------------------------------------
# SSE (Server-Sent Events)
# ---------------------------------------------------------------------------

@app.get("/missions/{mission_id}/events")
async def mission_events(mission_id: str) -> StreamingResponse:
    """SSE endpoint for real-time mission progress streaming."""

    async def event_generator():
        m = _active_missions.get(mission_id)
        if not m:
            yield f"data: {json.dumps({'type': 'ERROR', 'error': 'Mission not found'})}\n\n"
            return

        last_phase = ""
        while True:
            m = _active_missions.get(mission_id)
            if not m:
                yield f"data: {json.dumps({'type': 'DONE'})}\n\n"
                break

            current_phase = m.get("phase", "")
            if current_phase != last_phase:
                yield f"data: {json.dumps({'type': 'STATUS_UPDATE', 'mission_id': mission_id, 'phase': current_phase, 'progress': m.get('progress', 0), 'cost_usd': m.get('cost_usd', 0)})}\n\n"
                last_phase = current_phase

            if m.get("status") in ("completed", "failed"):
                yield f"data: {json.dumps({'type': 'DONE', 'status': m['status']})}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ---------------------------------------------------------------------------
# Background mission runner
# ---------------------------------------------------------------------------

async def _run_mission_bg(mission_id: str, req: MissionCreate) -> None:
    """Run a mission in the background using TokenRunApp."""
    m = _active_missions.get(mission_id)
    if not m:
        return

    try:
        m["status"] = "running"
        m["phase"] = "SAMPLING"
        m["progress"] = 0.0
        await _broadcast({"type": "STATUS_UPDATE", "mission_id": mission_id, "phase": "SAMPLING"})

        # Build real components
        from core.app import TokenRunApp
        from core.models import Runfile
        import yaml
        from dotenv import load_dotenv

        load_dotenv()

        with open(req.runfile_path, "r", encoding="utf-8") as f:
            runfile = Runfile(**yaml.safe_load(f))

        from main import build_providers
        actor_provider, critic_provider = build_providers(runfile)

        try:
            app = TokenRunApp(runfile, actor_provider, critic_provider)

            m["phase"] = "SAMPLING"
            m["progress"] = 0.1
            await _broadcast({"type": "STATUS_UPDATE", "mission_id": mission_id, "phase": "SAMPLING", "progress": 0.1})

            # Approval callback: wait for API approve_mission to signal
            approval_event = _mission_events.get(mission_id)

            async def approval_gate(_report):
                m["phase"] = "AWAITING_APPROVAL"
                m["status"] = "awaiting_approval"
                await _broadcast({
                    "type": "APPROVAL_REQUIRED",
                    "mission_id": mission_id,
                    "phase": "AWAITING_APPROVAL",
                    "report": _report,
                })
                # Wait for the API endpoint to call event.set()
                if approval_event:
                    await approval_event.wait()
                return True

            # Run with approval gate
            if req.sample_only:
                result = await app.run_mission(sample_only=True, auto_approve=True)
            else:
                result = await app.run_mission(
                    auto_approve=False,
                    approval_callback=approval_gate,
                )

            m["status"] = "completed"
            m["phase"] = "DONE"
            m["progress"] = 1.0
            m["result"] = result
            m["cost_usd"] = result.get("ledger_summary", {}).get("total_cost", "$0.0000")
            m["success_count"] = result.get("success_count", 0)
            m["total_count"] = result.get("total_count", 0)
            await _broadcast({"type": "STATUS_UPDATE", "mission_id": mission_id, "phase": "COMPLETED", "progress": 1.0})

        finally:
            await actor_provider.close()
            await critic_provider.close()

    except Exception as exc:
        m["status"] = "failed"
        m["phase"] = "ERROR"
        await _broadcast({
            "type": "ERROR",
            "mission_id": mission_id,
            "error": str(exc),
        })
