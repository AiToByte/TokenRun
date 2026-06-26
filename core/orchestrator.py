"""
Orchestrator — async task scheduler with concurrency control, DAG support,
pause/resume, and drift detection.

Manages the lifecycle from 1% sampling through full production,
enforcing the sampling gate and budget constraints at every step.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.drift_detector import DriftAlert, DriftDetector
from core.ledger import BudgetExceededError, TokenLedger
from core.models import Runfile, TaskNode, TaskTrace
from core.prompt_lineage import PromptLineageManager
from core.runner import ActorCriticLoop
from core.self_healer import SelfHealer
from core.task_queue import Priority
from core.telemetry import TelemetryManager

__all__ = ["TROrchestrator"]


class TROrchestrator:
    """Coordinate parallel Actor-Critic execution across a data stream.

    Parameters
    ----------
    runfile:
        The parsed task blueprint.
    loop_engine:
        An :class:`ActorCriticLoop` instance.
    ledger:
        Token cost tracker.
    concurrency:
        Maximum number of concurrent API calls.
    drift_detector:
        Optional drift detector for periodic consistency checks.
    self_healer:
        Optional self-healer for automatic prompt optimization.
    """

    def __init__(
        self,
        runfile: Runfile,
        loop_engine: ActorCriticLoop,
        ledger: TokenLedger,
        concurrency: int = 5,
        drift_detector: Optional[DriftDetector] = None,
        self_healer: Optional[SelfHealer] = None,
        telemetry: Optional[TelemetryManager] = None,
        quality_threshold: float = 0.6,
        quality_window: int = 5,
        drift_action: str = "halt",  # "warn" | "halt" | "resample"
    ) -> None:
        self.runfile = runfile
        self.engine = loop_engine
        self.ledger = ledger
        self._semaphore = asyncio.Semaphore(concurrency)
        self.results: List[TaskTrace] = []
        self.lineage = PromptLineageManager()
        self.drift_detector = drift_detector
        self.self_healer = self_healer
        self.telemetry = telemetry
        self._total_iterations = 0  # for max_loop_count enforcement

        # --- Quality circuit breaker ---
        self.quality_threshold = quality_threshold
        self.quality_window = quality_window
        self._recent_scores: List[float] = []
        self._quality_halted = False
        self._state_lock = asyncio.Lock()  # protects shared mutable state

        # --- Drift action ---
        self.drift_action = drift_action

        # --- Replay signal ---
        self._replay_event = asyncio.Event()
        self._replay_prompt: Optional[str] = None

        # --- Pause/Resume state ---
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # not paused initially
        self._is_paused = False

        # Register initial prompt versions for all nodes
        for node in self.runfile.workflow:
            if not node.prompt_registry:
                self.lineage.register_initial(node, node.actor_prompt_template)

    # ------------------------------------------------------------------
    # Pause / Resume
    # ------------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        """True if execution is currently paused."""
        return self._is_paused

    def pause(self) -> None:
        """Pause execution.  New tasks will block until resumed."""
        self._is_paused = True
        self._pause_event.clear()
        print("⏸️ [编排器] 执行已暂停。")

    def resume(self, new_prompt: Optional[str] = None, change_log: str = "") -> None:
        """Resume execution, optionally with a modified prompt.

        Parameters
        ----------
        new_prompt:
            If provided, creates a new PromptVersion for workflow[0]
            and updates the template.
        change_log:
            Description of why the prompt was changed.
        """
        if new_prompt and self.runfile.workflow:
            node = self.runfile.workflow[0]
            version = self.lineage.create_version(
                node, new_prompt, change_log=change_log
            )
            print(f"📝 [编排器] Prompt 已更新: {version.version_id} — {change_log}")
            # Update fingerprint to reflect new prompt
            from core.runner import ActorCriticLoop as ACL

            self.runfile.fingerprint = ACL.compute_fingerprint(
                model_id=self.runfile.fingerprint.model_id
                if self.runfile.fingerprint
                else "",
                prompt_template=new_prompt,
                parameters=self.runfile.fingerprint.parameters
                if self.runfile.fingerprint
                else {},
            )

        self._is_paused = False
        self._pause_event.set()
        print("▶️ [编排器] 执行已恢复。")

    def request_replay(
        self,
        new_prompt: Optional[str] = None,
        rollback: bool = False,
        rollback_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Signal a replay request from the API layer.

        Parameters
        ----------
        new_prompt:
            If provided, the orchestrator will switch to this prompt
            for subsequent executions.
        rollback:
            If True, reset completed tasks to pending before replay.
            This enables "timeline rollback" — re-running tasks with
            new parameters as if they hadn't been executed yet.
        rollback_ids:
            Specific task IDs to reset. If None and rollback=True,
            resets all completed tasks for the current mission.

        Returns
        -------
        dict
            Contains ``reset_count`` (number of tasks reset to pending).
        """
        reset_count = 0
        if rollback and self.engine.persistence:
            if rollback_ids:
                reset_count = self.engine.persistence.reset_multiple(rollback_ids)
            else:
                completed = self.engine.persistence.get_completed_ids()
                if completed:
                    reset_count = self.engine.persistence.reset_multiple(completed)
            if reset_count > 0:
                print(f"⏪ [编排器] 已将 {reset_count} 个任务重置为待处理状态。")

        self._replay_prompt = new_prompt
        self._replay_event.set()
        print("🔄 [编排器] 重放信号已发送。")
        return {"reset_count": reset_count}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_sampling_gate(self, data_stream: List[str]) -> List[Dict[str, Any]]:
        """Execute the 1% sampling phase on the first workflow node."""
        cfg = self.runfile.sampling
        if not cfg.enabled:
            print("⏭️ [采样阶段] 采样已禁用，跳过。")
            return []

        if cfg.mode == "percentage":
            count = max(1, int(len(data_stream) * cfg.value))
        else:
            count = max(1, int(cfg.value))
        samples = data_stream[:count]

        async with self._state_lock:
            self.results = []
        print(f"\U0001f52c [采样阶段] 开始处理 {len(samples)} 个样本...")
        return await self._process_batch(samples)

    async def run_mass_production(
        self,
        data_stream: List[str],
        priority: Priority = Priority.NORMAL,
        cache_aware: bool = True,
    ) -> List[Dict[str, Any]]:
        """Execute the full production phase across all workflow nodes.

        Parameters
        ----------
        data_stream:
            Input data items to process.
        priority:
            Task priority. LOW priority tasks may be routed to Batch API
            for cost savings when a CostScheduler is configured.
        cache_aware:
            If True, sort data by prefix to maximize prompt cache hits.
        """
        if self.ledger.is_fused:
            print("❌ 账本已熔断，无法开启全量生产。")
            return []

        # --- Fingerprint verification ---
        fp = self.runfile.fingerprint
        if fp and self.runfile.workflow:
            node = self.runfile.workflow[0]
            if not ActorCriticLoop.verify_fingerprint(
                fp, fp.model_id, node.actor_prompt_template, fp.parameters
            ):
                print("🚨 [指纹校验] 检测到逻辑变动！请重新采样以确认质量。")
                return []
            print("✅ [指纹校验] 配置一致性确认。")

        # --- Context caching: sort by prefix for cache-friendly ordering ---
        if cache_aware and len(data_stream) > 10:
            data_stream = self._sort_for_cache(data_stream)
            print(f"📦 [缓存优化] 已按前缀排序 {len(data_stream)} 条数据")

        # --- Priority-based routing ---
        if priority == Priority.LOW:
            print("⏳ [低优先级] 任务将使用成本优化模式执行...")

        async with self._state_lock:
            self.results = []
        print(f"\U0001f3ed [生产阶段] 开始全量处理 {len(data_stream)} 条数据...")
        return await self._process_dag(data_stream)

    # ------------------------------------------------------------------
    # DAG execution
    # ------------------------------------------------------------------

    async def _process_dag(self, data_stream: List[str]) -> List[Dict[str, Any]]:
        """Execute all workflow nodes in topological order."""
        order = self._topological_sort(self.runfile.workflow)
        node_map = {n.id: n for n in self.runfile.workflow}

        data_by_node: Dict[str, List[str]] = {}
        final_results: List[Dict[str, Any]] = []

        for node_id in order:
            node = node_map[node_id]

            if node.depends_on:
                input_items: List[str] = []
                for dep_id in node.depends_on:
                    dep_outputs = data_by_node.get(dep_id, [])
                    if not dep_outputs:
                        print(
                            f"  ⚠️ 节点 [{node.name}] 的上游依赖 [{dep_id}] 无输出，跳过。"
                        )
                        continue
                    input_items.extend(dep_outputs)
                if not input_items:
                    print(f"  ⚠️ 节点 [{node.name}] 无可用输入，跳过。")
                    data_by_node[node_id] = []
                    continue
            else:
                input_items = list(data_stream)

            print(f"  \U0001f4e6 节点 [{node.name}] 处理 {len(input_items)} 条数据...")
            results = await self._process_batch(input_items, node_override=node)

            # Update prompt lineage stats
            if results:
                successful = sum(1 for r in results if r.get("status") == "success")
                current = self.lineage.get_current(node)
                if current:
                    self.lineage.record_stats(
                        node,
                        current.version_id,
                        {
                            "pass_rate": round(successful / len(results), 4),
                            "total_processed": len(results),
                        },
                    )

            outputs = []
            for r in results:
                if r.get("status") == "success":
                    outputs.append(str(r.get("final_output", "")))
            data_by_node[node_id] = outputs

            final_results = results

        return final_results

    @staticmethod
    def _topological_sort(nodes: List[TaskNode]) -> List[str]:
        """Return node IDs in dependency-first order (Kahn's algorithm)."""
        in_degree: Dict[str, int] = {n.id: 0 for n in nodes}
        graph: Dict[str, List[str]] = {n.id: [] for n in nodes}

        for n in nodes:
            for dep in n.depends_on:
                graph[dep].append(n.id)
                in_degree[n.id] += 1

        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        order: List[str] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for child in graph[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(nodes):
            raise ValueError("工作流存在循环依赖，无法执行。")

        return order

    # ------------------------------------------------------------------
    # Context caching & spot-check
    # ------------------------------------------------------------------

    @staticmethod
    def _sort_for_cache(data: List[str]) -> List[str]:
        """Sort data by first 50 chars to maximize prompt cache hits.

        Items with similar prefixes will be processed consecutively,
        allowing the LLM provider to reuse cached context.
        """
        return sorted(data, key=lambda x: x[:50])

    async def spot_check(
        self, results: List[Dict[str, Any]], sample_rate: float = 0.05
    ) -> List[Dict[str, Any]]:
        """Randomly sample results for human review.

        Parameters
        ----------
        results:
            Mission results to sample from.
        sample_rate:
            Fraction of results to sample (default 5%).

        Returns
        -------
        list[dict]
            Sampled results flagged for review.
        """
        import random

        count = max(1, int(len(results) * sample_rate))
        sampled = random.sample(results, min(count, len(results)))

        for item in sampled:
            item["spot_check"] = True

        if self.telemetry and sampled:
            self.telemetry.emit(
                "SPOT_CHECK",
                "system",
                {
                    "sampled_count": len(sampled),
                    "total_count": len(results),
                    "message": f"已抽取 {len(sampled)} 条结果进行人工复核",
                },
            )

        return sampled

    # ------------------------------------------------------------------
    # Skill resolution
    # ------------------------------------------------------------------

    def resolve_skill_ref(self, node: TaskNode) -> TaskNode:
        """If the node has a skill_ref, load the .trs and merge its config.

        Returns the node with ``actor_prompt_template`` and ``loop_config``
        populated from the skill file.
        """
        if not node.skill_ref:
            return node

        skill_path = Path(node.skill_ref)
        if not skill_path.exists():
            # Try vault/ and skills/library/
            for prefix in [Path("vault"), Path("skills/library")]:
                candidate = prefix / f"{node.skill_ref}.trs"
                if candidate.exists():
                    skill_path = candidate
                    break
            else:
                raise FileNotFoundError(f"技能文件不存在: {node.skill_ref}")

        skill_data = json.loads(skill_path.read_text(encoding="utf-8"))

        # Merge skill config into node
        if skill_data.get("optimized_prompt"):
            node.actor_prompt_template = skill_data["optimized_prompt"]

        if skill_data.get("validation_rules"):
            from core.models import ValidationRule

            node.loop_config.exit_criteria = [
                ValidationRule(**r) for r in skill_data["validation_rules"]
            ]

        return node

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _process_batch(
        self,
        batch: List[str],
        node_override: Optional[TaskNode] = None,
    ) -> List[Dict[str, Any]]:
        # --- Replay signal: apply once before dispatching all tasks ---
        if self._replay_event.is_set():
            async with self._state_lock:
                if self._replay_prompt and self.runfile.workflow:
                    node = node_override or self.runfile.workflow[0]
                    node.actor_prompt_template = self._replay_prompt
                    self._replay_prompt = None
                self._replay_event.clear()

        tasks = [
            self._bounded_execute(item, node_override=node_override) for item in batch
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _bounded_execute(
        self,
        data: str,
        node_override: Optional[TaskNode] = None,
    ) -> Dict[str, Any]:
        # --- Wait if paused ---
        await self._pause_event.wait()

        # --- Quality circuit breaker ---
        if self._quality_halted:
            return {
                "status": "quality_halted",
                "final_output": None,
                "history": [],
            }

        # --- max_loop_count enforcement ---
        max_loops = self.runfile.governance.max_loop_count
        if max_loops and self._total_iterations >= max_loops:
            return {
                "status": "governance_limit",
                "final_output": None,
                "history": [],
            }

        async with self._semaphore:
            node = node_override or self.runfile.workflow[0]
            try:
                result = await self.engine.run(node, data)
                # Track shared state under lock
                async with self._state_lock:
                    if result.get("history"):
                        self._total_iterations += len(result["history"])
                    if result.get("trace"):
                        self.results.append(result["trace"])

                # --- Quality circuit breaker: track consecutive low scores ---
                should_halt = False
                if result.get("history"):
                    last_score = result["history"][-1].get("score", 0.0)
                    async with self._state_lock:
                        self._recent_scores.append(last_score)
                        if len(self._recent_scores) > self.quality_window:
                            self._recent_scores.pop(0)
                        if len(self._recent_scores) >= self.quality_window and all(
                            s < self.quality_threshold for s in self._recent_scores
                        ):
                            self._quality_halted = True
                            should_halt = True

                    if should_halt:
                        msg = (
                            f"🚨 [质量熔断] 连续 {self.quality_window} 个任务评分低于 "
                            f"{self.quality_threshold}，流水线自动停止。"
                        )
                        print(msg)
                        if self.telemetry:
                            self.telemetry.emit(
                                "QUALITY_HALT",
                                "system",
                                {
                                    "message": msg,
                                    "recent_scores": self._recent_scores,
                                },
                            )
                        return {
                            "status": "quality_halted",
                            "final_output": result.get("final_output"),
                            "history": result.get("history", []),
                        }

                # --- Drift detection tick ---
                if self.drift_detector and self.drift_detector.tick():
                    try:
                        report = await self.drift_detector.run_check(
                            node.actor_prompt_template
                        )
                        if not report.get("drift_detected"):
                            print(
                                f"  ✅ [漂移检测] 第 {report['check_number']} 次检查通过"
                                f" (匹配率: {report['match_rate']:.1%})"
                            )
                    except DriftAlert as alert:
                        print(f"  {alert}")
                        result["drift_alert"] = str(alert)

                        # --- Drift auto-halt ---
                        if self.drift_action == "halt":
                            self._quality_halted = True
                            if self.telemetry:
                                self.telemetry.emit(
                                    "DRIFT_HALT",
                                    "system",
                                    {
                                        "message": str(alert),
                                    },
                                )
                            return {
                                "status": "drift_halted",
                                "final_output": result.get("final_output"),
                                "history": result.get("history", []),
                            }

                        # --- Drift auto-resample ---
                        if self.drift_action == "resample":
                            self.pause()
                            if self.telemetry:
                                self.telemetry.emit(
                                    "DRIFT_RESAMPLE",
                                    "system",
                                    {
                                        "message": f"漂移检测触发自动重采样: {alert}",
                                    },
                                )
                            print(
                                "🔄 [漂移检测] 自动重采样已触发，执行已暂停等待确认。"
                            )
                            return {
                                "status": "drift_resample",
                                "final_output": result.get("final_output"),
                                "history": result.get("history", []),
                            }

                # --- Self-healer: record critiques and check for healing ---
                if self.self_healer and result.get("history"):
                    for h in result["history"]:
                        critique = h.get("critique")
                        if critique:
                            self.self_healer.record_critique(critique)

                    # Check if healing is needed after recording critiques
                    suggestion = self.self_healer.check_healing_needed()
                    if suggestion and self.telemetry:
                        self.telemetry.emit(
                            "HEALING_SUGGESTION",
                            "system",
                            {
                                "pattern": suggestion.critique_pattern,
                                "frequency": suggestion.frequency,
                                "confidence": suggestion.confidence,
                                "message": f"检测到重复批评模式 ({suggestion.frequency}次): {suggestion.critique_pattern}。建议优化 Prompt。",
                            },
                        )

                # --- Telemetry: emit iteration event ---
                if self.telemetry and result.get("history"):
                    last = result["history"][-1]
                    self.telemetry.emit_step(
                        task_id=node.id,
                        node_id=node.id,
                        iteration=last.get("iteration", 0),
                        passed=last.get("passed", False),
                        score=last.get("score", 0.0),
                        output_preview=last.get("output", "")[:200],
                    )

                return result
            except BudgetExceededError:
                # Halt all remaining tasks immediately
                async with self._state_lock:
                    self._quality_halted = True
                return {
                    "status": "budget_exceeded",
                    "final_output": None,
                    "history": [],
                }
