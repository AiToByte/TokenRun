"""
Actor-Critic Loop Engine — the heart of TokenRun's Loop Engineering.

Drives the "execute → evaluate → refine" cycle until the Critic
passes the output or the maximum number of attempts is exhausted.
Each iteration records a full trace for persistence and analysis.

Supports three loop strategies:
    FEEDBACK_DRIVEN — retry with Critic feedback until pass or max_attempts.
    EXHAUSTIVE — run all attempts, pick the highest-scoring result.
    ONCE — single attempt, no retry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional

from core.actor import TaskActor
from core.critic import TaskCritic
from core.ledger import TokenLedger
from core.models import (
    EvaluationResult,
    ExecutionIteration,
    Fingerprint,
    LoopStrategy,
    TaskNode,
    TaskStatus,
    TaskTrace,
    ValidationRule,
)
from core.persistence import TaskPersistence
from gateway.privacy import PrivacyRedactor

__all__ = ["ActorCriticLoop"]


class ActorCriticLoop:
    """Execute a single data item through the Actor-Critic cycle.

    Parameters
    ----------
    actor:
        The expensive-model executor.
    critic:
        The cheap-model auditor.
    ledger:
        Token cost tracker (optional; if ``None`` no accounting is done).
    persistence:
        Trace storage for checkpoint/resume (optional).
    redactor:
        Privacy redactor for PII masking (optional).
    """

    def __init__(
        self,
        actor: TaskActor,
        critic: TaskCritic,
        ledger: Optional[TokenLedger] = None,
        persistence: Optional[TaskPersistence] = None,
        redactor: Optional[PrivacyRedactor] = None,
        model_providers: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.actor = actor
        self.critic = critic
        self.ledger = ledger
        self.persistence = persistence
        self.redactor = redactor
        # model_providers: {"gpt-4o-mini": LLMProvider, "gpt-4o": LLMProvider, ...}
        self._model_providers = model_providers or {}

    async def run(self, node: TaskNode, input_data: str) -> Dict[str, Any]:
        """Run the Actor-Critic loop for *input_data* on *node*.

        Returns
        -------
        dict
            ``{"status": "success"|"exhausted", "final_output": str,
            "history": [...], "trace": TaskTrace}``
        """
        attempts = 0
        feedback = ""
        iterations: List[Dict[str, Any]] = []
        trace = TaskTrace(task_id=node.id, status=TaskStatus.RUNNING)
        strategy = node.loop_config.strategy
        max_attempts = (
            1 if strategy == LoopStrategy.ONCE else node.loop_config.max_attempts
        )
        final_output = ""  # safe default for exhausted path

        # --- Privacy: mask sensitive data before sending to LLM ---
        safe_input = input_data
        if self.redactor:
            safe_input = self.redactor.mask(input_data)

        # --- Persistence: check for already-completed work ---
        input_hash = hashlib.sha256(input_data.encode()).hexdigest()[:16]
        unit_id = f"{node.id}:{input_hash}"
        if self.persistence:
            prev_status = self.persistence.get_status(unit_id)
            if prev_status == "completed":
                return {
                    "status": "success",
                    "final_output": "(cached)",
                    "history": [],
                    "trace": trace,
                }

        # --- Split rules into programmatic vs LLM-eval ---
        prog_rules, llm_rules = self._split_rules(node.loop_config.exit_criteria)

        # --- EXHAUSTIVE mode: collect all results, pick best at end ---
        best_result: Optional[Dict[str, Any]] = None
        best_score = -1.0

        while attempts < max_attempts:
            attempts += 1
            start = time.time()

            # --- Dynamic model routing: escalate to higher tier if needed ---
            tier_provider = self._resolve_tier_provider(node, attempts)
            actor_to_use = self.actor
            if tier_provider:
                # Create a temporary actor with the tier provider (no shared mutation)
                actor_to_use = TaskActor(provider=tier_provider)

            # --- Actor phase (uses masked data) ---
            actor_resp = await actor_to_use.generate(
                template_str=node.actor_prompt_template,
                data=safe_input,
                feedback=feedback,
            )

            # Record actor token consumption
            if self.ledger:
                self.ledger.record_usage(
                    model_name=actor_resp.model_name,
                    prompt_tokens=actor_resp.prompt_tokens,
                    completion_tokens=actor_resp.completion_tokens,
                    role="actor",
                )

            # --- Privacy: unmask LLM output to restore real values ---
            final_output = actor_resp.content
            if self.redactor:
                final_output = self.redactor.unmask(actor_resp.content)

            # --- Programmatic validation (no LLM cost) ---
            prog_passed, prog_scores = self._run_programmatic_rules(
                prog_rules, final_output
            )

            # --- Critic phase (only for llm_eval rules) ---
            eval_result: EvaluationResult
            if llm_rules:
                # Use node-specific critic if configured (local model strategy)
                critic_to_use = self.critic
                if node.loop_config.critic_model:
                    from gateway.provider import LLMProvider as LP

                    local_provider = LP(
                        api_key=self.critic.provider._api_key,
                        base_url=node.loop_config.critic_base_url
                        or self.critic.provider.base_url,
                        model_name=node.loop_config.critic_model,
                    )
                    critic_to_use = TaskCritic(provider=local_provider)

                eval_result = await critic_to_use.evaluate(
                    task_name=node.name,
                    input_data=safe_input,
                    output_content=final_output,
                    rules=llm_rules,
                )
                # Merge programmatic scores into LLM scores
                eval_result.scores.update(prog_scores)

                # --- Consensus validation: multi-model voting ---
                consensus_models = node.loop_config.consensus_models
                if consensus_models:
                    eval_result = await self._run_consensus(
                        eval_result,
                        consensus_models,
                        node,
                        safe_input,
                        final_output,
                        llm_rules,
                    )
            else:
                # No LLM rules — build result from programmatic checks only
                eval_result = EvaluationResult(
                    passed=prog_passed,
                    score=1.0 if prog_passed else 0.0,
                    scores=prog_scores,
                    critique=None if prog_passed else "程序化校验未通过",
                    audit_cost=0,
                )

            # Record critic token consumption
            if (
                self.ledger
                and eval_result.audit_cost is not None
                and eval_result.audit_cost > 0
            ):
                self.ledger.record_usage(
                    model_name=self.critic.provider.model_name,
                    prompt_tokens=0,
                    completion_tokens=eval_result.audit_cost,
                    role="critic",
                )

            # --- Weighted score calculation ---
            weighted_score = self._compute_weighted_score(
                eval_result.scores, node.loop_config.score_weights
            )
            if weighted_score > 0.0:
                eval_result.score = weighted_score
            min_score = node.loop_config.min_score
            if eval_result.scores and weighted_score < min_score:
                eval_result.passed = False

            latency_ms = int((time.time() - start) * 1000)

            # Build iteration record
            iteration = ExecutionIteration(
                iteration_index=attempts,
                input_payload=input_data,
                output_content=final_output,
                evaluation=eval_result,
                tokens_consumed={
                    "actor_prompt": actor_resp.prompt_tokens,
                    "actor_completion": actor_resp.completion_tokens,
                    "critic_audit": eval_result.audit_cost or 0,
                },
                latency_ms=latency_ms,
                timestamp=str(time.time()),
                prompt_version_id=node.current_version_id,
            )
            trace.iterations.append(iteration)

            iter_dict = {
                "iteration": attempts,
                "output": final_output,
                "passed": eval_result.passed,
                "score": eval_result.score,
                "scores": eval_result.scores,
                "critique": eval_result.critique,
            }
            iterations.append(iter_dict)

            # --- Persistence: save after each iteration ---
            if self.persistence:
                self.persistence.save_trace(
                    unit_id=unit_id,
                    input_hash=input_hash,
                    status="completed" if eval_result.passed else "running",
                    trace={"iterations": iterations},
                    output=final_output if eval_result.passed else "",
                )

            # --- EXHAUSTIVE: track best result, don't early-exit ---
            if strategy == LoopStrategy.EXHAUSTIVE:
                if weighted_score > best_score:
                    best_score = weighted_score
                    best_result = {
                        "output": final_output,
                        "eval": eval_result,
                    }
                # Continue to max_attempts regardless of passed
                if attempts < max_attempts:
                    feedback = eval_result.critique or ""
                    if node.loop_config.retry_delay > 0:
                        await asyncio.sleep(node.loop_config.retry_delay)
                continue

            # --- FEEDBACK_DRIVEN / ONCE: exit on pass ---
            if eval_result.passed:
                trace.status = TaskStatus.COMPLETED
                trace.final_output = final_output
                return {
                    "status": "success",
                    "final_output": final_output,
                    "history": iterations,
                    "trace": trace,
                }

            # Prepare feedback for next iteration
            feedback = eval_result.critique or ""

            # --- Retry delay ---
            if attempts < max_attempts and node.loop_config.retry_delay > 0:
                await asyncio.sleep(node.loop_config.retry_delay)

        # --- EXHAUSTIVE: return the best result ---
        if strategy == LoopStrategy.EXHAUSTIVE and best_result is not None:
            best_out = best_result["output"]
            best_eval: EvaluationResult = best_result["eval"]
            trace.status = (
                TaskStatus.COMPLETED if best_eval.passed else TaskStatus.FAILED
            )
            trace.final_output = best_out
            return {
                "status": "success" if best_eval.passed else "exhausted",
                "final_output": best_out,
                "history": iterations,
                "trace": trace,
            }

        # Exhausted all attempts
        trace.status = TaskStatus.FAILED
        if self.persistence:
            self.persistence.save_trace(
                unit_id=unit_id,
                input_hash=input_hash,
                status="failed",
                trace={"iterations": iterations},
            )
        return {
            "status": "exhausted",
            "final_output": final_output,
            "history": iterations,
            "trace": trace,
        }

    # ------------------------------------------------------------------
    # Dynamic model routing
    # ------------------------------------------------------------------

    def _resolve_tier_provider(self, node: TaskNode, attempt: int) -> Optional[Any]:
        """Determine which model provider to use based on tier escalation.

        Returns the provider to use, or None if no escalation is needed.
        """
        tiers = node.model_tiers
        if not tiers or not self._model_providers:
            return None

        # Find the appropriate tier based on cumulative failed attempts
        cumulative_escalations = 0
        for tier in tiers:
            cumulative_escalations += tier.escalate_after
            if attempt <= cumulative_escalations:
                provider = self._model_providers.get(tier.model)
                if provider:
                    return provider

        # Use the last tier if all escalation thresholds exceeded
        last_tier = tiers[-1]
        return self._model_providers.get(last_tier.model)

    # ------------------------------------------------------------------
    # Consensus validation
    # ------------------------------------------------------------------

    async def _run_consensus(
        self,
        primary_result: EvaluationResult,
        consensus_models: List[str],
        node: TaskNode,
        input_data: str,
        output: str,
        rules: List[ValidationRule],
    ) -> EvaluationResult:
        """Run consensus validation with multiple models in parallel.

        Calls additional critics concurrently and takes majority vote on
        ``passed``.  The primary result's scores are merged with consensus
        scores.
        """
        threshold = node.loop_config.consensus_threshold
        votes = [primary_result.passed]
        all_scores = [primary_result.scores]

        async def _call_consensus(model_name: str) -> Optional[EvaluationResult]:
            try:
                from gateway.provider import LLMProvider

                provider = LLMProvider(
                    api_key=self.critic.provider._api_key,
                    base_url=self.critic.provider.base_url,
                    model_name=model_name,
                )
                consensus_critic = TaskCritic(provider=provider)
                result = await consensus_critic.evaluate(
                    task_name=node.name,
                    input_data=input_data,
                    output_content=output,
                    rules=rules,
                )
                await provider.close()
                return result
            except Exception:
                return None

        # Parallel call to all consensus models
        results = await asyncio.gather(
            *[_call_consensus(m) for m in consensus_models],
            return_exceptions=False,
        )

        for result in results:
            if result is not None:
                votes.append(result.passed)
                all_scores.append(result.scores)

        # Majority vote
        pass_count = sum(1 for v in votes if v)
        consensus_passed = (pass_count / len(votes)) >= threshold

        # Merge scores from all models
        merged_scores: Dict[str, float] = {}
        for scores in all_scores:
            for k, v in scores.items():
                if k in merged_scores:
                    merged_scores[k] = max(merged_scores[k], v)
                else:
                    merged_scores[k] = v

        primary_result.passed = consensus_passed
        primary_result.scores = merged_scores
        if not consensus_passed:
            primary_result.critique = (
                f"共识审计未通过 ({pass_count}/{len(votes)} 模型同意)"
            )
        return primary_result

    # ------------------------------------------------------------------
    # Programmatic validation
    # ------------------------------------------------------------------

    @staticmethod
    def _split_rules(
        rules: List[ValidationRule],
    ) -> tuple[List[ValidationRule], List[ValidationRule]]:
        """Split rules into programmatic (regex/json_schema/code_eval) and LLM-eval."""
        prog: List[ValidationRule] = []
        llm: List[ValidationRule] = []
        for r in rules:
            if r.type in ("regex", "json_schema", "code_eval"):
                prog.append(r)
            else:
                llm.append(r)
        return prog, llm

    @staticmethod
    def _run_programmatic_rules(
        rules: List[ValidationRule], output: str
    ) -> tuple[bool, Dict[str, float]]:
        """Run regex/json_schema checks without LLM calls.

        Returns (all_passed, scores_dict).
        """
        if not rules:
            return True, {}

        scores: Dict[str, float] = {}
        all_passed = True

        for rule in rules:
            if rule.type == "regex":
                pattern = str(rule.criteria)
                matched = bool(re.search(pattern, output))
                scores[f"regex:{pattern[:30]}"] = 1.0 if matched else 0.0
                if not matched:
                    all_passed = False

            elif rule.type == "json_schema":
                try:
                    parsed = json.loads(output)
                    if isinstance(rule.criteria, dict):
                        required = rule.criteria.get("required", [])
                        missing = [f for f in required if f not in parsed]
                        passed = len(missing) == 0
                        scores["json_schema"] = 1.0 if passed else 0.0
                        if not passed:
                            all_passed = False
                    else:
                        scores["json_schema"] = 1.0
                except (json.JSONDecodeError, TypeError):
                    scores["json_schema"] = 0.0
                    all_passed = False

            elif rule.type == "code_eval":
                code_passed, score = ActorCriticLoop._run_code_eval(
                    str(rule.criteria), output
                )
                scores["code_eval"] = score
                if not code_passed:
                    all_passed = False

        return all_passed, scores

    @staticmethod
    def _run_code_eval(test_code: str, output: str) -> tuple[bool, float]:
        """Execute test code in a security-hardened sandbox.

        The test code can reference ``output`` as a string variable
        containing the Actor's output.  Returns (passed, score).
        """
        from core.sandbox import SandboxExecutor

        sandbox = SandboxExecutor(
            timeout=10, allow_network=False, allow_file_write=False
        )
        result = sandbox.execute_python(test_code, variables={"output": output})
        return result.get("passed", False), result.get("score", 0.0)

    @staticmethod
    def _compute_weighted_score(
        scores: Dict[str, float],
        weights: Dict[str, float],
    ) -> float:
        """Compute weighted average score from dimension scores.

        If no weights are configured, returns the simple average.
        """
        if not scores:
            return 0.0

        if weights:
            total_weight = 0.0
            weighted_sum = 0.0
            for dim, score in scores.items():
                w = weights.get(dim, 1.0)
                weighted_sum += score * w
                total_weight += w
            return weighted_sum / total_weight if total_weight > 0 else 0.0

        return sum(scores.values()) / len(scores)

    # ------------------------------------------------------------------
    # Fingerprint utilities
    # ------------------------------------------------------------------

    @staticmethod
    def compute_fingerprint(
        model_id: str,
        prompt_template: str,
        parameters: Dict[str, Any],
        sample_output: str = "",
    ) -> Fingerprint:
        """Compute an execution fingerprint from current configuration.

        Includes model_id, prompt hash, temperature, and seed for
        deterministic verification.
        """
        prompt_hash = hashlib.sha256(prompt_template.encode()).hexdigest()[:16]
        # Include temperature and seed for finer granularity
        fp_params = {
            "temperature": parameters.get("temperature", 0.1),
            "seed": parameters.get("seed"),
            "top_p": parameters.get("top_p"),
        }
        snapshot = None
        if sample_output:
            snapshot = hashlib.sha256(sample_output.encode()).hexdigest()[:16]
        return Fingerprint(
            model_id=model_id,
            prompt_hash=prompt_hash,
            parameters=fp_params,
            snapshot=snapshot,
        )

    @staticmethod
    def verify_fingerprint(
        locked: Fingerprint,
        model_id: str,
        prompt_template: str,
        parameters: Dict[str, Any],
    ) -> bool:
        """Check if current config matches the locked fingerprint.

        Returns ``True`` if consistent, ``False`` if drift detected.
        """
        current = ActorCriticLoop.compute_fingerprint(
            model_id, prompt_template, parameters
        )
        return (
            current.model_id == locked.model_id
            and current.prompt_hash == locked.prompt_hash
            and current.parameters.get("temperature")
            == locked.parameters.get("temperature")
            and current.parameters.get("seed") == locked.parameters.get("seed")
        )
