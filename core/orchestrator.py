"""
Orchestrator — async task scheduler with concurrency control, DAG support,
pause/resume, and drift detection.

Manages the lifecycle from 1% sampling through full production,
enforcing the sampling gate and budget constraints at every step.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Dict, List, Optional

from core.drift_detector import DriftAlert, DriftDetector
from core.ledger import BudgetExceededError, TokenLedger
from core.models import Runfile, TaskNode, TaskTrace
from core.prompt_lineage import PromptLineageManager
from core.runner import ActorCriticLoop

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
    """

    def __init__(
        self,
        runfile: Runfile,
        loop_engine: ActorCriticLoop,
        ledger: TokenLedger,
        concurrency: int = 5,
        drift_detector: Optional[DriftDetector] = None,
    ) -> None:
        self.runfile = runfile
        self.engine = loop_engine
        self.ledger = ledger
        self._semaphore = asyncio.Semaphore(concurrency)
        self.results: List[TaskTrace] = []
        self.lineage = PromptLineageManager()
        self.drift_detector = drift_detector
        self._total_iterations = 0  # for max_loop_count enforcement

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
                model_id=self.runfile.fingerprint.model_id if self.runfile.fingerprint else "",
                prompt_template=new_prompt,
                parameters=self.runfile.fingerprint.parameters if self.runfile.fingerprint else {},
            )

        self._is_paused = False
        self._pause_event.set()
        print("▶️ [编排器] 执行已恢复。")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_sampling_gate(
        self, data_stream: List[str]
    ) -> List[Dict[str, Any]]:
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

        self.results = []
        print(f"\U0001f52c [采样阶段] 开始处理 {len(samples)} 个样本...")
        return await self._process_batch(samples)

    async def run_mass_production(
        self, data_stream: List[str]
    ) -> List[Dict[str, Any]]:
        """Execute the full production phase across all workflow nodes."""
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
                        print(f"  ⚠️ 节点 [{node.name}] 的上游依赖 [{dep_id}] 无输出，跳过。")
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
                    self.lineage.record_stats(node, current.version_id, {
                        "pass_rate": round(successful / len(results), 4),
                        "total_processed": len(results),
                    })

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
    # Internals
    # ------------------------------------------------------------------

    async def _process_batch(
        self,
        batch: List[str],
        node_override: Optional[TaskNode] = None,
    ) -> List[Dict[str, Any]]:
        tasks = [
            self._bounded_execute(item, node_override=node_override)
            for item in batch
        ]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _bounded_execute(
        self,
        data: str,
        node_override: Optional[TaskNode] = None,
    ) -> Dict[str, Any]:
        # --- Wait if paused ---
        await self._pause_event.wait()

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
                # Track total iterations for max_loop_count
                if result.get("history"):
                    self._total_iterations += len(result["history"])
                if result.get("trace"):
                    self.results.append(result["trace"])

                # --- Drift detection tick ---
                if self.drift_detector and self.drift_detector.tick():
                    try:
                        report = await self.drift_detector.run_check(
                            node.actor_prompt_template
                        )
                        if not report.get("drift_detected"):
                            print(f"  ✅ [漂移检测] 第 {report['check_number']} 次检查通过"
                                  f" (匹配率: {report['match_rate']:.1%})")
                    except DriftAlert as alert:
                        print(f"  {alert}")
                        # Drift detected — mark result but don't crash
                        result["drift_alert"] = str(alert)

                return result
            except BudgetExceededError:
                return {
                    "status": "budget_exceeded",
                    "final_output": None,
                    "history": [],
                }
