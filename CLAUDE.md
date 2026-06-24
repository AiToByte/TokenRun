# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TokenRun** is an industrial-grade AI task execution framework that converts AI tokens into deterministic, high-quality outputs. The core mechanism is **Loop Engineering**: an Actor-Critic loop where an expensive model (Actor) generates output, a cheap model (Critic) audits quality, and the system iterates until quality criteria are met.

## Architecture

The system follows a pipeline architecture with these core components:

### Core Engine (`core/`)

- **`models.py`** — Pydantic V2 protocol models (Runfile, TaskNode, Trace, EvaluationResult, PromptVersion). Runfile uses `extra="forbid"` for strict validation.
- **`runner.py`** — `ActorCriticLoop` engine supporting 3 strategies (FEEDBACK_DRIVEN, EXHAUSTIVE, ONCE), programmatic validation (regex/json_schema/code_eval), multi-dimensional scoring, privacy masking, persistence checkpointing.
- **`orchestrator.py`** — `TROrchestrator` with async concurrency (`asyncio.Semaphore`), topological DAG execution, sampling gate, fingerprint verification, pause/resume, drift detection integration.
- **`critic.py`** — `TaskCritic` using cheap LLM with structured JSON output for multi-dimensional scoring.
- **`actor.py`** — `TaskActor` with Jinja2 template rendering and feedback injection.
- **`ledger.py`** — `TokenLedger` with budget circuit breaker (`BudgetExceededError`), ROI reporting, conservative fallback pricing for unknown models.
- **`persistence.py`** — `TaskPersistence` SQLite storage with `threading.Lock` for concurrent writes, idempotent `INSERT OR REPLACE`.
- **`sampling_manager.py`** — `SamplingManager` with ROI estimation (cost_per_sample, estimated_total_cost, success_rate).
- **`solidifier.py`** — `SkillSolidifier` extracts optimal prompts and golden samples into `.trs` skill packages.
- **`prompt_lineage.py`** — `PromptLineageManager` for version-controlled prompt evolution (parent tracking, stats).
- **`drift_detector.py`** — `DriftDetector` runs golden samples at configurable intervals, raises `DriftAlert` on mismatch.
- **`telemetry.py`** — `TelemetryManager` event broadcasting with callback handlers.

### Gateway (`gateway/`)

- **`provider.py`** — `LLMProvider` async client for OpenAI-compatible APIs with exponential backoff, Retry-After support, `LLMProviderError` custom exception.
- **`privacy.py`** — `PrivacyRedactor` reversible PII masking (EMAIL, PHONE, ID_CARD, IP_ADDR, API_KEY) with `[[TR_{LABEL}_{N}]]` placeholders. O(1) reverse lookup via `_reverse_vault` dict.
- **`file_gateway.py`** — `FileGateway` streaming file reader with glob patterns.
- **`batch_provider.py`** — `BatchProvider` for OpenAI Batch API (50% cost, 24h window).
- **`s3_gateway.py`** — `S3Gateway` for S3-compatible storage (requires `boto3`).
- **`sql_gateway.py`** — `SQLGateway` for SQL databases (requires `sqlalchemy`).

### API (`api/`)

- **`main.py`** — FastAPI backend with REST endpoints (`/missions`, `/skills`, `/health`) and WebSocket (`/ws`) for real-time events. CORS enabled.

### Frontend (`web/`)

- Next.js 14 + TailwindCSS Cockpit UI with Dashboard, Missions (create/approve/abort), Skills, and WebSocket telemetry hook.

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, Pydantic V2, httpx, Jinja2, Rich
- **Storage**: SQLite (traces), DuckDB (future), Redis (optional future)
- **Frontend**: Next.js 14 + TailwindCSS + Lucide icons
- **Deployment**: Docker + docker-compose

## Key Design Decisions

- **Determinism over creativity**: Default `temperature=0.1`; fingerprint locking prevents silent prompt drift.
- **Cheap model audits expensive model**: Critic uses structured JSON output for reliable parsing.
- **Reversible masking**: Privacy redaction uses placeholder mapping stored only in memory; destroyed after task completion.
- **Financial safety as first-class**: Every LLM call passes through the Ledger; budget exceeded → immediate circuit break.
- **Programmatic validation saves tokens**: regex/json_schema/code_eval rules are validated without LLM calls; only `llm_eval` uses the Critic.

## Commands

```bash
# Install
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# CLI mode
python main.py                          # default test_mission
python main.py runfiles/custom.yaml     # custom Runfile
python main.py --sample-only            # sampling phase only

# API mode
uvicorn api.main:app --reload           # localhost:8000

# Frontend
cd web && npm install && npm run dev    # localhost:3000

# Docker
docker-compose up                       # backend:8000 + frontend:3000
```

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | Shared API key (fallback for both) | — |
| `ACTOR_API_KEY` | Actor model API key | inherits `OPENAI_API_KEY` |
| `ACTOR_BASE_URL` | Actor API endpoint | `https://api.openai.com/v1` |
| `ACTOR_MODEL` | Actor model name | `gpt-4o` |
| `CRITIC_API_KEY` | Critic model API key | inherits `ACTOR_API_KEY` |
| `CRITIC_BASE_URL` | Critic API endpoint | inherits `ACTOR_BASE_URL` |
| `CRITIC_MODEL` | Critic model name | `gpt-4o-mini` |

## Testing

- **Framework**: pytest + pytest-asyncio
- **Pattern**: `unittest.mock.AsyncMock` for LLM calls, `MagicMock(spec=...)` for type safety
- **Coverage**: 137 tests across 9 test files covering core engine, privacy, ledger, runner, API, batch provider, edge cases, and new features
- **Run single test**: `python -m pytest tests/test_runner.py::TestActorCriticLoop::test_first_attempt_passes -v`

## Design Documents

All design documents are in `docs/`:

| File | Content |
|------|---------|
| `docs/TokenRun落地方案-总览.md` | Master roadmap: tech stack, phases, directory structure, risk mitigation |
| `docs/第一阶段-核心协议与元数据定义.md` | Pydantic models for Runfile, Trace, EvaluationResult |
| `docs/第二阶段-Runner核心引擎与状态机控制.md` | TRRunner state machine, loop engineering, feedback injection |
| `docs/第三阶段-资源网关、隐私脱敏、增强型审计器.md` | Gateway, PrivacyRedactor, LLMCritic, TokenLedger |
| `docs/第四阶段-指挥塔-通讯协议与可视化交互设计.md` | WebSocket/SSE protocol, Cockpit UI layout, human-in-the-loop |
| `docs/第五阶段-版本控制谱系与技能固化机制.md` | Prompt versioning, SkillVault, .trs format |
| `docs/工程建设-step1.md` through `step6.md` | Incremental implementation guides with code snippets |
| `docs/全系统集成方案与最小可行性案例.md` | Full integration pseudocode and MVP scenario (personal finance audit) |
