"""
Skill Solidifier — distill execution traces into reusable .trs skill packages.

After a task completes, the solidifier analyses all traces to find the
best-performing prompt version, extracts golden samples, and writes a
self-contained skill file that can be reloaded for future runs.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["SkillSolidifier"]


class SkillSolidifier:
    """Extract optimal configuration from traces and export as ``.trs``.

    Parameters
    ----------
    vault_path:
        Directory where skill files are stored.
    """

    def __init__(self, vault_path: str = "vault") -> None:
        self.vault_path = Path(vault_path)

    def distill(
        self,
        task_name: str,
        traces: List[Dict[str, Any]],
        prompt_template: str,
        model_config: Optional[Dict[str, Any]] = None,
        validation_rules: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Analyse traces and produce a ``.trs`` skill file.

        Parameters
        ----------
        task_name:
            Human-readable name for the skill.
        traces:
            Raw trace dicts (each has ``status``, ``history``, etc.).
        prompt_template:
            The final prompt template that performed best.
        model_config:
            Model parameters to lock (model name, temperature, etc.).
        validation_rules:
            The exit criteria that were used.

        Returns
        -------
        str
            Path to the generated ``.trs`` file.
        """
        # Ensure vault directory exists
        self.vault_path.mkdir(parents=True, exist_ok=True)

        # 1. Compute skill ID from prompt content (SHA-256, truncated)
        prompt_hash = hashlib.sha256(prompt_template.encode()).hexdigest()[:12]
        skill_id = f"TR-SKILL-{prompt_hash}"

        # 2. Extract golden samples (successful traces with highest scores)
        successful = [t for t in traces if t.get("status") == "success"]
        golden_samples = self._extract_golden_samples(successful, count=5)

        # 3. Calculate performance stats
        stats = self._calculate_stats(traces)

        # 4. Build the skill payload
        payload = {
            "skill_id": skill_id,
            "name": task_name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "optimized_prompt": prompt_template,
            "model_config": model_config or {},
            "validation_rules": validation_rules or [],
            "golden_samples": golden_samples,
            "performance_stats": stats,
        }

        # 5. Write to disk
        skill_file = self.vault_path / f"{skill_id}.trs"
        try:
            skill_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise RuntimeError(f"技能文件写入失败: {exc}") from exc
        return str(skill_file)

    def load_skill(self, skill_id: str) -> Dict[str, Any]:
        """Load a previously solidified skill by ID."""
        skill_file = self.vault_path / f"{skill_id}.trs"
        if not skill_file.exists():
            raise FileNotFoundError(f"技能不存在: {skill_id}")
        return json.loads(skill_file.read_text(encoding="utf-8"))

    def list_skills(self) -> List[str]:
        """Return all available skill IDs."""
        return [f.stem for f in self.vault_path.glob("*.trs")]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _extract_golden_samples(
        self, successful: List[Dict[str, Any]], count: int = 5
    ) -> List[Dict[str, str]]:
        """Pick the top-*count* samples by final iteration score."""
        scored = []
        for t in successful:
            history = t.get("history", [])
            if history:
                last = history[-1]
                scored.append((last.get("score", 0.0), t))
        scored.sort(key=lambda x: x[0], reverse=True)

        samples = []
        for _, t in scored[:count]:
            history = t.get("history", [])
            if history:
                # Use first iteration's output as the input context
                # (the original input is not stored in history dicts)
                samples.append({
                    "input_preview": history[0].get("output", "")[:200],
                    "output": t.get("final_output", ""),
                    "score": history[-1].get("score", 0.0),
                })
        return samples

    def _calculate_stats(self, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregate performance metrics."""
        total = len(traces)
        if total == 0:
            return {"total": 0, "success_rate": 0.0, "average_retries": 0.0}

        successful = sum(1 for t in traces if t.get("status") == "success")
        total_iterations = sum(len(t.get("history", [])) for t in traces)

        return {
            "total": total,
            "success_rate": round(successful / total, 4),
            "average_retries": round(total_iterations / total, 2),
        }

    # ------------------------------------------------------------------
    # Knowledge Distillation Export
    # ------------------------------------------------------------------

    def export_fine_tune(
        self,
        traces: List[Dict[str, Any]],
        format: str = "openai",
        output_path: Optional[str] = None,
        min_score: float = 0.8,
    ) -> str:
        """Export successful traces as a fine-tuning dataset.

        Converts [Input] → [Final_Output] pairs into standard JSONL
        format suitable for fine-tuning language models.

        Parameters
        ----------
        traces:
            Raw trace dicts from a completed mission.
        format:
            Output format: ``"openai"`` (OpenAI JSONL), ``"alpaca"``,
            or ``"sharegpt"``.
        output_path:
            Where to write the JSONL file.  If None, uses vault/.
        min_score:
            Minimum score to include a trace (filters low-quality outputs).

        Returns
        -------
        str
            Path to the generated JSONL file.
        """
        successful = [
            t for t in traces
            if t.get("status") == "success"
            and t.get("history")
            and t["history"][-1].get("score", 0) >= min_score
        ]

        if not successful:
            raise ValueError(f"没有通过评分阈值 ({min_score}) 的成功记录可导出")

        lines = []
        for t in successful:
            history = t.get("history", [])
            if not history:
                continue

            # Extract input and output
            input_text = history[0].get("output", "")  # best proxy for input
            output_text = t.get("final_output", "")

            if format == "openai":
                lines.append(json.dumps({
                    "messages": [
                        {"role": "user", "content": input_text},
                        {"role": "assistant", "content": output_text},
                    ]
                }, ensure_ascii=False))

            elif format == "alpaca":
                lines.append(json.dumps({
                    "instruction": input_text,
                    "input": "",
                    "output": output_text,
                }, ensure_ascii=False))

            elif format == "sharegpt":
                lines.append(json.dumps({
                    "conversations": [
                        {"from": "human", "value": input_text},
                        {"from": "gpt", "value": output_text},
                    ]
                }, ensure_ascii=False))

            else:
                raise ValueError(f"不支持的导出格式: {format}")

        # Write to file
        out_dir = Path(output_path) if output_path else self.vault_path
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"finetune_{format}_{int(time.time())}.jsonl"
        out_file.write_text("\n".join(lines), encoding="utf-8")

        return str(out_file)
