"""
TokenRun CLI — entry point for running missions from the command line.

Usage::

    python main.py                          # run default test_mission
    python main.py runfiles/custom.yaml     # run a specific Runfile
    python main.py --sample-only            # sampling phase only
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from core.actor import TaskActor
from core.critic import TaskCritic
from core.ledger import TokenLedger
from core.models import Runfile
from core.orchestrator import TROrchestrator
from core.persistence import TaskPersistence
from core.runner import ActorCriticLoop
from core.sampling_manager import SamplingManager
from core.solidifier import SkillSolidifier
from core.telemetry import TelemetryManager
from gateway.file_gateway import FileGateway
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMProvider

__all__ = ["cli"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_runfile(path: str) -> Runfile:
    """Parse a YAML Runfile into a validated Runfile model."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Runfile(**data)


def build_providers(_runfile: Runfile) -> tuple[LLMProvider, LLMProvider]:
    """Create Actor and Critic LLM providers from environment config."""
    actor_key = os.environ.get("ACTOR_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    actor_url = os.environ.get("ACTOR_BASE_URL", "https://api.openai.com/v1")
    actor_model = os.environ.get("ACTOR_MODEL", "gpt-4o")

    critic_key = os.environ.get("CRITIC_API_KEY", actor_key)
    critic_url = os.environ.get("CRITIC_BASE_URL", actor_url)
    critic_model = os.environ.get("CRITIC_MODEL", "gpt-4o-mini")

    if not actor_key:
        raise ValueError(
            "未配置 API Key。请设置 OPENAI_API_KEY 或 ACTOR_API_KEY 环境变量。\n"
            "参考 .env.example 文件。"
        )

    actor_provider = LLMProvider(
        api_key=actor_key, base_url=actor_url, model_name=actor_model
    )
    critic_provider = LLMProvider(
        api_key=critic_key, base_url=critic_url, model_name=critic_model
    )
    return actor_provider, critic_provider


def load_data(runfile: Runfile) -> list[str]:
    """Load data from Runfile resources, or fall back to demo data."""
    if runfile.context:
        for res in runfile.context:
            if res.type.value == "local_file":
                path_str = res.uri.replace("local://", "")
                gw = FileGateway(path_str)
                items = [f["content"] for f in gw.stream_files() if f.get("content")]
                if items:
                    return items
                print(f"  ⚠️ 目录 {path_str} 中无可读取的文本文件。")

    # Fallback: demo data for testing
    return [
        "人工智能（AI）正在深刻改变各行各业。从医疗诊断到自动驾驶，从金融风控到创意设计，"
        "AI 技术的应用场景不断扩展。然而，AI 的发展也面临着诸多挑战，包括数据隐私、算法偏见、"
        "能耗问题以及就业市场冲击。如何在推动技术进步的同时确保负责任的 AI 发展，"
        "是当前社会各界共同关注的核心议题。专家认为，建立完善的 AI 治理框架、"
        "加强跨学科合作、提升公众 AI 素养将是关键所在。",
        "量子计算被认为是下一代计算技术的突破口。与传统计算机使用比特（0或1）不同，"
        "量子计算机利用量子比特的叠加态和纠缠特性，能够在特定问题上实现指数级加速。"
        "目前，谷歌、IBM、微软等科技巨头以及众多初创公司都在积极研发量子计算机。"
        "尽管距离通用量子计算机还有很长的路要走，但在药物发现、材料科学、"
        "密码学和优化问题等领域，量子计算已经展现出巨大的应用潜力。",
        "可持续发展已成为全球共识。面对气候变化、资源枯竭和生物多样性丧失等严峻挑战，"
        "各国政府和企业正在加速向绿色经济转型。可再生能源、循环经济、碳捕获技术"
        "以及绿色金融等领域的创新正在重塑全球经济格局。年轻一代消费者对环保产品"
        "和可持续品牌的偏好也在推动企业重新思考其商业模式和供应链策略。",
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_mission(runfile_path: str, sample_only: bool = False) -> None:
    """Execute a full TokenRun mission."""
    load_dotenv()

    print(f"\n{'=' * 60}")
    print("  TokenRun — 工业级 AI 任务执行引擎")
    print(f"{'=' * 60}\n")

    # 1. Load Runfile
    runfile = load_runfile(runfile_path)
    print(f"📋 蓝图: {runfile.name} (v{runfile.version})")
    print(f"   工作流节点: {len(runfile.workflow)}")
    print(f"   预算上限: ${runfile.governance.max_usd:.2f}")

    # 2. Build providers
    actor_provider, critic_provider = build_providers(runfile)

    try:
        # 3. Wire up all components
        ledger = TokenLedger(budget_usd=runfile.governance.max_usd)
        persistence = TaskPersistence(db_path="logs/tokenrun_traces.db")
        redactor = PrivacyRedactor(rules=runfile.security.masking_rules)

        actor = TaskActor(actor_provider)
        critic = TaskCritic(critic_provider)
        engine = ActorCriticLoop(
            actor=actor,
            critic=critic,
            ledger=ledger,
            persistence=persistence,
            redactor=redactor,
        )

        orchestrator = TROrchestrator(
            runfile=runfile,
            loop_engine=engine,
            ledger=ledger,
            concurrency=3,
        )

        # Telemetry: log events to console
        telemetry = TelemetryManager()
        telemetry.on_event(lambda e: None)  # placeholder for future WebSocket

        # 4. Load data
        sample_data = load_data(runfile)
        print(f"   数据量: {len(sample_data)} 条")

        # 5. Sampling phase
        print(f"\n{'─' * 60}")
        sample_results = await orchestrator.run_sampling_gate(sample_data)

        for i, result in enumerate(sample_results):
            status = result.get("status", "unknown")
            print(f"  样本 {i + 1}: {status}")
            if status == "success":
                preview = str(result.get("final_output", ""))[:80]
                print(f"    预览: {preview}...")

        # 6. Fingerprint locking (after successful sampling, all nodes)
        if runfile.workflow:
            # Use the first sample's output for snapshot hash
            sample_output = ""
            for r in sample_results:
                if r.get("status") == "success":
                    sample_output = str(r.get("final_output", ""))
                    break

            node = runfile.workflow[0]
            fp = ActorCriticLoop.compute_fingerprint(
                model_id=actor_provider.model_name,
                prompt_template=node.actor_prompt_template,
                parameters={"temperature": 0.1},
                sample_output=sample_output,
            )
            runfile.fingerprint = fp
            print(f"\n🔒 指纹已锁定: model={fp.model_id}, prompt_hash={fp.prompt_hash}")
            if fp.snapshot:
                print(f"   样本快照: {fp.snapshot}")

        if sample_only:
            print("\n⏸️ 采样完成（--sample-only 模式）。")
            print(f"  账本: {ledger.get_summary()}")
            return

        # 7. Sampling approval gate
        if runfile.sampling.auto_pause:
            sm = SamplingManager()
            sampling_ratio = runfile.sampling.value
            report = await sm.generate_report(
                sample_results,
                total_data_count=len(sample_data),
                sampling_ratio=sampling_ratio,
                current_cost_usd=ledger.report.total_cost_usd,
            )
            print("\n⏸️ 采样报告:")
            print(
                f"   成功率: {report['summary']['success_count']}/{report['summary']['sample_count']}"
            )
            print(f"   平均质量: {report['summary']['average_quality_score']}")
            econ = report.get("economics", {})
            if econ:
                print(f"   采样成本: ${econ.get('sampling_cost_usd', 0):.4f}")
                print(f"   预估总成本: ${econ.get('estimated_total_cost_usd', 0):.4f}")
                print(f"   预估成功数: {econ.get('estimated_success_count', 0)}")
            print("   等待审批... (按 Enter 继续全量执行)")
            await asyncio.get_running_loop().run_in_executor(None, input)
            sm.approve()

        # 8. Full production
        print(f"\n{'─' * 60}")
        full_results = await orchestrator.run_mass_production(sample_data)
        success = sum(1 for r in full_results if r.get("status") == "success")
        print(f"\n✅ 完成！成功: {success}/{len(full_results)}")
        print(f"  账本: {ledger.get_summary()}")

        # 9. Skill solidification (unified: read from persistence)
        if runfile.workflow:
            solidifier = SkillSolidifier(vault_path="vault")
            # Build traces from the orchestrator results
            traces = [
                {"status": r.get("status"), "history": r.get("history", [])}
                for r in full_results
            ]
            skill_path = solidifier.distill(
                task_name=runfile.name,
                traces=traces,
                prompt_template=runfile.workflow[0].actor_prompt_template,
                model_config={"model": actor_provider.model_name},
                validation_rules=[
                    r.model_dump()
                    for r in runfile.workflow[0].loop_config.exit_criteria
                ],
            )
            print(f"📦 技能已固化: {skill_path}")

        # 10. ROI report
        skill_id = Path(skill_path).stem if "skill_path" in locals() else ""
        print(
            ledger.get_roi_report(
                data_count=len(sample_data),
                success_count=success,
                skill_id=skill_id,
            )
        )

        # 11. Cleanup: clear privacy vault
        redactor.clear_vault()
        print("\n🧹 隐私映射表已清空。任务结束。")

    finally:
        # Always close HTTP clients
        await actor_provider.close()
        await critic_provider.close()


def cli() -> None:
    """CLI entry point."""
    args = sys.argv[1:]
    sample_only = "--sample-only" in args
    runfile_path = "runfiles/test_mission.yaml"

    for arg in args:
        if not arg.startswith("--"):
            runfile_path = arg
            break

    if not Path(runfile_path).exists():
        print(f"❌ 蓝图文件不存在: {runfile_path}")
        sys.exit(1)

    asyncio.run(run_mission(runfile_path, sample_only=sample_only))


if __name__ == "__main__":
    cli()
