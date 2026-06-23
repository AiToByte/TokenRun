"""
TokenRun Protocol Models (TRP)

Pydantic V2 definitions for the Runfile blueprint, execution traces,
and all intermediate data structures that flow through the system.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DeterminismLevel", "LoopStrategy", "ResourceType", "TaskStatus",
    "Resource", "SecurityConfig", "SamplingConfig", "Fingerprint",
    "ValidationRule", "LoopConfig", "PromptVersion", "TaskNode",
    "GovernanceConfig", "Runfile",
    "EvaluationResult", "ExecutionIteration", "TaskTrace",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DeterminismLevel(str, Enum):
    """How strictly the system enforces fingerprint consistency."""
    STRICT = "strict"
    FLEXIBLE = "flexible"


class LoopStrategy(str, Enum):
    """How the Actor-Critic loop decides to retry or stop."""
    FEEDBACK_DRIVEN = "feedback-driven"
    EXHAUSTIVE = "exhaustive"
    ONCE = "once"


class ResourceType(str, Enum):
    """Supported resource URI protocols."""
    LOCAL_FILE = "local_file"
    SQL_QUERY = "sql_query"
    S3_OBJECT = "s3_object"
    API_ENDPOINT = "api_endpoint"


class TaskStatus(str, Enum):
    """Lifecycle status of a task trace."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Resource & Security
# ---------------------------------------------------------------------------

class Resource(BaseModel):
    """A data source referenced by a Runfile."""
    id: str
    uri: str
    type: ResourceType
    description: Optional[str] = None


class SecurityConfig(BaseModel):
    """Privacy and sandbox settings for a Runfile."""
    masking_rules: List[str] = Field(
        default_factory=lambda: ["emails", "api_keys"]
    )
    local_sandbox: bool = True


# ---------------------------------------------------------------------------
# Sampling & Determinism
# ---------------------------------------------------------------------------

class SamplingConfig(BaseModel):
    """Controls the 1% sampling gate before full execution."""
    enabled: bool = True
    mode: str = "percentage"  # "percentage" | "count"
    value: float = 0.01       # 1% by default
    auto_pause: bool = True   # pause after sampling for human approval


class Fingerprint(BaseModel):
    """Locked execution environment captured after successful sampling."""
    model_id: str
    prompt_hash: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    snapshot: Optional[str] = None  # hash of the sample output


# ---------------------------------------------------------------------------
# Loop Engineering
# ---------------------------------------------------------------------------

class ValidationRule(BaseModel):
    """A single exit criterion for the Actor-Critic loop.

    Types:
        ``regex`` — programmatic: output must match the pattern.
        ``json_schema`` — programmatic: output must validate against the schema.
        ``llm_eval`` — LLM-based: the Critic evaluates via cheap model.
        ``code_eval`` — LLM-based: the Critic evaluates code quality.
    """
    type: str  # "regex" | "json_schema" | "llm_eval" | "code_eval"
    criteria: Any
    weight: float = 1.0


class LoopConfig(BaseModel):
    """Configures how a task node retries on failure."""
    strategy: LoopStrategy = LoopStrategy.FEEDBACK_DRIVEN
    max_attempts: int = 3
    exit_criteria: List[ValidationRule] = Field(default_factory=list)
    retry_delay: int = 1  # seconds
    score_weights: Dict[str, float] = Field(default_factory=dict)
    min_score: float = 0.85  # weighted score threshold to pass


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

class PromptVersion(BaseModel):
    """A single version in the prompt lineage tree."""
    version_id: str
    hash: str
    template: str
    parent_id: Optional[str] = None
    change_log: str = ""
    stats: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = 0.0


class TaskNode(BaseModel):
    """A single step in the Runfile workflow DAG."""
    id: str
    name: str
    depends_on: List[str] = Field(default_factory=list)
    actor_prompt_template: str
    loop_config: LoopConfig = Field(default_factory=LoopConfig)
    config: Dict[str, Any] = Field(default_factory=dict)
    prompt_registry: List[PromptVersion] = Field(default_factory=list)
    current_version_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Top-level Runfile
# ---------------------------------------------------------------------------

class GovernanceConfig(BaseModel):
    """Budget and safety constraints."""
    max_usd: float = 10.0
    max_loop_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Top-level Runfile
# ---------------------------------------------------------------------------

class Runfile(BaseModel):
    """
    The complete declarative task blueprint.

    A Runfile defines *what* to do (workflow), *how* to do it (loop config),
    *with what data* (resources), and *under what constraints* (security,
    sampling, governance).
    """
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    name: str = "Unnamed Task"
    metadata: Dict[str, str] = Field(default_factory=dict)
    context: List[Resource] = Field(default_factory=list)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)
    workflow: List[TaskNode] = Field(default_factory=list)
    fingerprint: Optional[Fingerprint] = None
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)


# ---------------------------------------------------------------------------
# Execution Trace
# ---------------------------------------------------------------------------

class EvaluationResult(BaseModel):
    """Output from the Critic after evaluating an Actor's output."""
    passed: bool = False
    score: float = 0.0
    scores: Dict[str, float] = Field(default_factory=dict)
    critique: Optional[str] = None
    suggestions: List[str] = Field(default_factory=list)
    audit_cost: Optional[int] = None  # tokens consumed by the audit itself


class ExecutionIteration(BaseModel):
    """A single Actor-Critic attempt within a loop."""
    iteration_index: int
    input_payload: str
    output_content: str
    evaluation: EvaluationResult
    tokens_consumed: Dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0
    timestamp: str = ""
    prompt_version_id: Optional[str] = None


class TaskTrace(BaseModel):
    """Full execution history for a single data item through a task node."""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    iterations: List[ExecutionIteration] = Field(default_factory=list)
    final_output: Optional[str] = None
