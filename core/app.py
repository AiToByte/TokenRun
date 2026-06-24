"""
TokenRun Application — main controller class.

Orchestrates all components into a unified mission execution pipeline.
This is the "main control console" described in the integration design.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.actor import TaskActor
from core.critic import TaskCritic
from core.ledger import TokenLedger
from core.models import Runfile
from core.orchestrator import TROrchestrator
from core.persistence import TaskPersistence
from core.prompt_lineage import PromptLineageManager
from core.runner import ActorCriticLoop
from core.sampling_manager import SamplingManager
from core.solidifier import SkillSolidifier
from core.telemetry import TelemetryManager
from gateway.file_gateway import FileGateway
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMProvider

__all__ = ["TokenRunApp"]


class TokenRunApp:
    """Main controller for TokenRun missions.

    Wires together all components and manages the full lifecycle:
    sampling → approval → production → solidification.

    Parameters
    ----------
    runfile:
        The parsed task blueprint.
    actor_provider:
        LLM provider for the expensive model.
    critic_provider:
        LLM provider for the cheap model.
    """

    def __init__(
        self,
        runfile: Runfile,
        actor_provider: LLMProvider,
        critic_provider: LLMProvider,
    ) -> None:
        self.runfile = runfile
        self.actor_provider = actor_provider
        self.critic_provider = critic_provider

        # Core components
        self.ledger = TokenLedger(budget_usd=runfile.governance.max_usd)
        self.persistence = TaskPersistence(db_path="logs/tokenrun_traces.db")
        self.redactor = PrivacyRedactor(rules=runfile.security.masking_rules)
        self.telemetry = TelemetryManager()
        self.lineage = PromptLineageManager()
        self.solidifier = SkillSolidifier(vault_path="vault")
        self.sampling_manager = SamplingManager()

        # Engine
        self.actor = TaskActor(actor_provider)
        self.critic = TaskCritic(critic_provider)
        self.engine = ActorCriticLoop(
            actor=self.actor,
            critic=self.critic,
            ledger=self.ledger,
            persistence=self.persistence,
            redactor=self.redactor,
        )

        # Orchestrator
        self.orchestrator = TROrchestrator(
            runfile=runfile,
            loop_engine=self.engine,
            ledger=self.ledger,
            concurrency=3,
        )

    # ------------------------------------------------------------------
    # Resource sensing
    # ------------------------------------------------------------------

    async def sense_resources(self) -> List[str]:
        """Auto-detect and load data from Runfile resources.

        Supports local:// and mcp:// protocols. Falls back to demo data if no
        resources are configured.
        """
        if not self.runfile.context:
            return self._demo_data()

        for res in self.runfile.context:
            if res.type.value == "local_file":
                path_str = res.uri.replace("local://", "")
                gw = FileGateway(path_str)
                items = [f["content"] for f in gw.stream_files() if f.get("content")]
                if items:
                    self.telemetry.emit(
                        "RESOURCE_LOADED",
                        "system",
                        {"source": res.uri, "count": len(items)},
                    )
                    return items
                self.telemetry.emit(
                    "WARNING",
                    "system",
                    {"message": f"目录 {path_str} 中无可读取的文本文件"},
                )

            elif res.type.value == "mcp_tool":
                # MCP Tool: call external MCP server to get data
                # Resource.uri = mcp://server:port
                # Resource.description = tool name
                # Resource.id = optional JSON arguments
                try:
                    from gateway.mcp_client import MCPClient

                    server_url = res.uri.replace("mcp://", "http://")
                    tool_name = res.description or "list_data"
                    tool_args = {}
                    if res.id and res.id.startswith("{"):
                        tool_args = json.loads(res.id)

                    async with MCPClient(server_url) as client:
                        result = await client.call_tool(tool_name, tool_args)
                        content = result.get("content", [])
                        items = [
                            c.get("text", "")
                            for c in content
                            if c.get("type") == "text"
                        ]
                        if items:
                            self.telemetry.emit(
                                "RESOURCE_LOADED",
                                "system",
                                {"source": res.uri, "count": len(items)},
                            )
                            return items
                except Exception as exc:
                    self.telemetry.emit(
                        "WARNING",
                        "system",
                        {"message": f"MCP 工具调用失败 ({res.uri}): {exc}"},
                    )

        return self._demo_data()

    # ------------------------------------------------------------------
    # Mission lifecycle
    # ------------------------------------------------------------------

    async def run_mission(
        self,
        sample_only: bool = False,
        auto_approve: bool = False,
        approval_callback: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute a full mission lifecycle.

        Parameters
        ----------
        sample_only:
            Only run sampling phase.
        auto_approve:
            Skip approval gate entirely.
        approval_callback:
            Optional async callable invoked between sampling and production.
            If provided, replaces the default ``input()`` blocking call.
            Should return ``True`` to approve or ``False`` to abort.

        Returns a summary dict with results, traces, and skill info.
        """
        self.telemetry.emit_status("system", "INIT")

        # Load data
        data = await self.sense_resources()

        # Sampling phase
        self.telemetry.emit_status("system", "SAMPLING")
        sample_results = await self.orchestrator.run_sampling_gate(data)

        # Fingerprint locking
        if self.runfile.workflow:
            sample_output = ""
            for r in sample_results:
                if r.get("status") == "success":
                    sample_output = str(r.get("final_output", ""))
                    break

            node = self.runfile.workflow[0]
            fp = ActorCriticLoop.compute_fingerprint(
                model_id=self.actor_provider.model_name,
                prompt_template=node.actor_prompt_template,
                parameters={"temperature": 0.1},
                sample_output=sample_output,
            )
            self.runfile.fingerprint = fp
            self.telemetry.emit(
                "FINGERPRINT_LOCKED",
                "system",
                {
                    "model": fp.model_id,
                    "prompt_hash": fp.prompt_hash,
                },
            )

        if sample_only:
            return {"phase": "sampling", "results": sample_results}

        # Approval gate
        if self.runfile.sampling.auto_pause and not auto_approve:
            sampling_ratio = self.runfile.sampling.value
            report = await self.sampling_manager.generate_report(
                sample_results,
                total_data_count=len(data),
                sampling_ratio=sampling_ratio,
                current_cost_usd=self.ledger.report.total_cost_usd,
            )
            self.telemetry.emit_sample_report("system", report)

            # Wait for approval
            self.telemetry.emit_status("system", "AWAITING_APPROVAL")
            if approval_callback:
                approved = await approval_callback(report)
                if not approved:
                    return {"phase": "aborted", "results": sample_results}
            else:
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: input("  输入 'yes' 确认，其他内容取消: ")
                )
                if response.strip().lower() not in ("yes", "y", ""):
                    return {"phase": "aborted", "results": sample_results}
            self.sampling_manager.approve()

        # Full production
        self.telemetry.emit_status("system", "FULL_PRODUCTION")
        full_results = await self.orchestrator.run_mass_production(data)
        success = sum(1 for r in full_results if r.get("status") == "success")

        # Skill solidification
        skill_path = ""
        if self.runfile.workflow:
            traces = [
                {"status": r.get("status"), "history": r.get("history", [])}
                for r in full_results
            ]

            # Auto cost optimization: record pass_rate on current prompt version
            node = self.runfile.workflow[0]
            current = self.lineage.get_current(node)
            if current:
                self.lineage.record_stats(
                    node,
                    current.version_id,
                    {
                        "pass_rate": round(success / len(full_results), 4)
                        if full_results
                        else 0,
                        "total_cost": self.ledger.report.total_cost_usd,
                    },
                )

            skill_path = self.solidifier.distill(
                task_name=self.runfile.name,
                traces=traces,
                prompt_template=node.actor_prompt_template,
                model_config={"model": self.actor_provider.model_name},
                validation_rules=[
                    r.model_dump() for r in node.loop_config.exit_criteria
                ],
            )

            # Auto distillation: export fine-tune data if success rate > 90%
            success_rate = success / len(full_results) if full_results else 0
            if success_rate > 0.9 and len(full_results) >= 5:
                try:
                    ft_path = self.solidifier.export_fine_tune(
                        traces, format="openai", min_score=0.8
                    )
                    print(
                        f"📊 [自动蒸馏] 成功率 {success_rate:.0%} > 90%，已导出训练数据: {ft_path}"
                    )
                    self.telemetry.emit(
                        "AUTO_DISTILLATION",
                        "system",
                        {
                            "file_path": ft_path,
                            "success_rate": round(success_rate, 4),
                            "item_count": len(full_results),
                        },
                    )
                except Exception as exc:
                    print(f"⚠️ [自动蒸馏] 导出失败: {exc}")

        # Cleanup
        self.redactor.clear_vault()

        self.telemetry.emit_status("system", "COMPLETED")

        return {
            "phase": "completed",
            "results": full_results,
            "success_count": success,
            "total_count": len(full_results),
            "skill_path": skill_path,
            "ledger_summary": self.ledger.get_summary(),
            "roi_report": self.ledger.get_roi_report(
                data_count=len(data),
                success_count=success,
                skill_id=Path(skill_path).stem if skill_path else "",
            ),
        }

    # ------------------------------------------------------------------
    # Skill reuse
    # ------------------------------------------------------------------

    def run_from_skill(
        self, skill_id: str, data: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Load a solidified skill and prepare a mission from its locked parameters.

        Returns a summary dict with the skill configuration ready for execution.
        Does NOT execute — call ``run_mission()`` on the returned app, or use
        the returned config to create a new app.
        """
        skill = self.solidifier.load_skill(skill_id)

        # Build a Runfile from the skill's locked parameters
        from core.models import LoopConfig, TaskNode, ValidationRule

        exit_criteria = []
        for rule_data in skill.get("validation_rules", []):
            exit_criteria.append(ValidationRule(**rule_data))

        node = TaskNode(
            id=skill_id,
            name=skill.get("name", skill_id),
            actor_prompt_template=skill.get("optimized_prompt", ""),
            loop_config=LoopConfig(
                max_attempts=3,
                exit_criteria=exit_criteria,
            ),
        )

        runfile = Runfile(
            name=f"Skill Run: {skill.get('name', skill_id)}",
            workflow=[node],
        )

        return {
            "skill_id": skill_id,
            "skill": skill,
            "runfile": runfile,
            "data": data or self._demo_data(),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _demo_data() -> List[str]:
        """Fallback demo data for testing."""
        return [
            "人工智能（AI）正在深刻改变各行各业。从医疗诊断到自动驾驶，从金融风控到创意设计，"
            "AI 技术的应用场景不断扩展。然而，AI 的发展也面临着诸多挑战，包括数据隐私、算法偏见、"
            "能耗问题以及就业市场冲击。如何在推动技术进步的同时确保负责任的 AI 发展，"
            "是当前社会各界共同关注的核心议题。",
            "量子计算被认为是下一代计算技术的突破口。与传统计算机使用比特（0或1）不同，"
            "量子计算机利用量子比特的叠加态和纠缠特性，能够在特定问题上实现指数级加速。"
            "目前，谷歌、IBM、微软等科技巨头以及众多初创公司都在积极研发量子计算机。",
            "可持续发展已成为全球共识。面对气候变化、资源枯竭和生物多样性丧失等严峻挑战，"
            "各国政府和企业正在加速向绿色经济转型。可再生能源、循环经济、碳捕获技术"
            "以及绿色金融等领域的创新正在重塑全球经济格局。",
        ]
