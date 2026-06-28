# TokenRun 组件参考手册

---

## 目录

1. [核心引擎 (core/)](#1-核心引擎)
2. [网关层 (gateway/)](#2-网关层)
3. [API 层 (api/)](#3-api-层)
4. [前端 (web/)](#4-前端)
5. [模块依赖图](#5-模块依赖图)

---

## 1. 核心引擎

### 1.1 models.py — 协议模型定义

**职责:** 定义 TokenRun 所有数据结构，使用 Pydantic V2 严格校验。

**关键设计:** 所有模型使用 `ConfigDict(extra="forbid")` — 解析时拒绝未知字段，防止配置错误。

**枚举类型:**

| 枚举 | 值 | 用途 |
|------|-----|------|
| `LoopStrategy` | `feedback-driven`, `exhaustive`, `once` | 循环策略 |
| `TaskStatus` | `pending`, `running`, `paused`, `completed`, `failed` | 任务状态 |
| `ResourceType` | `local_file`, `sql_query`, `s3_object`, `api_endpoint`, `mcp_tool` | 数据源类型 |
| `DeterminismLevel` | `strict`, `flexible` | 确定性级别 |

**核心模型:**

```python
class Runfile(BaseModel):
    """完整的声明式任务蓝图"""
    version: str = "1.0"
    name: str = "Unnamed Task"
    metadata: Dict[str, str] = {}
    context: List[Resource] = []           # 数据源引用
    security: SecurityConfig               # 隐私和沙箱设置
    sampling: SamplingConfig               # 采样门配置
    workflow: List[TaskNode] = []          # 工作流 DAG 节点
    fingerprint: Optional[Fingerprint]     # 指纹锁定
    governance: GovernanceConfig           # 预算约束
    output_sink: Optional[OutputSinkConfig] # 输出持久化

class TaskNode(BaseModel):
    """工作流 DAG 中的单个节点"""
    id: str
    name: str
    depends_on: List[str] = []             # 上游依赖
    actor_prompt_template: str = ""        # Jinja2 模板
    skill_ref: Optional[str] = None        # .trs 技能引用
    loop_config: LoopConfig                # 循环配置
    model_tiers: List[ModelTier] = []      # 动态模型路由
    prompt_registry: List[PromptVersion]   # Prompt 版本历史

class LoopConfig(BaseModel):
    """循环配置"""
    strategy: LoopStrategy = "feedback-driven"
    max_attempts: int = 3
    exit_criteria: List[ValidationRule]    # 退出条件
    score_weights: Dict[str, float]        # 评分权重
    min_score: float = 0.85                # 最低通过分数
    consensus_models: List[str]            # 共识模型列表
    critic_model: Optional[str]            # 节点级 Critic 覆盖

class ValidationRule(BaseModel):
    """单个退出条件"""
    type: str           # "regex" | "json_schema" | "llm_eval" | "code_eval"
    criteria: Any       # 具体规则内容
    weight: float = 1.0 # 权重

class EvaluationResult(BaseModel):
    """Critic 评估结果"""
    passed: bool = False
    score: float = 0.0
    scores: Dict[str, float] = {}          # 多维评分
    critique: Optional[str] = None         # 批评反馈
    suggestions: List[str] = []            # 改进建议
    audit_cost: Optional[int] = None       # 审计 Token 消耗

class TaskTrace(BaseModel):
    """单条数据的完整执行历史"""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    iterations: List[ExecutionIteration]   # 每次迭代记录
    final_output: Optional[str] = None
```

---

### 1.2 runner.py — Actor-Critic 循环引擎

**职责:** 驱动"执行 → 评估 → 改进"循环，直到 Critic 通过或达到最大尝试次数。

**类:** `ActorCriticLoop`

**构造参数:**

| 参数 | 类型 | 说明 |
|------|------|------|
| `actor` | `TaskActor` | 昂贵模型执行器 |
| `critic` | `TaskCritic` | 廉价模型审计器 |
| `ledger` | `Optional[TokenLedger]` | 成本追踪器 |
| `persistence` | `Optional[TaskPersistence]` | 持久化存储 |
| `redactor` | `Optional[PrivacyRedactor]` | 隐私脱敏器 |
| `model_providers` | `Optional[Dict[str, Any]]` | 模型提供商映射 |
| `eval_judge` | `Optional[EvalJudge]` | 多维评估器 |

**核心方法:**

```python
async def run(self, node: TaskNode, input_data: str) -> Dict[str, Any]:
    """执行单条数据的 Actor-Critic 循环

    返回:
        {
            "status": "success" | "exhausted",
            "final_output": str,
            "history": [iteration_dict, ...],
            "trace": TaskTrace
        }
    """
```

**执行流程:**
```
1. 隐私脱敏: safe_input = redactor.mask(input_data)
2. 持久化检查: 已完成? → 返回缓存结果
3. 分离规则: programmatic (regex/json_schema) vs LLM (llm_eval)
4. 循环:
   for attempt in range(max_attempts):
     a. 动态模型路由: 根据 attempt 选择 model tier
     b. Actor 生成: actor.generate(template, safe_input, feedback)
     c. 反脱敏: final_output = redactor.unmask(output)
     d. 程序化验证 (无 LLM 成本)
     e. LLM 评估 (Critic 或 EvalJudge)
     f. 共识验证 (如果配置了 consensus_models)
     g. 加权评分计算
     h. 持久化保存 (异步)
     i. 判断: passed? → 返回成功
5. EXHAUSTIVE 模式: 运行所有尝试，返回最高分结果
6. 耗尽: 返回 exhausted
```

**三种策略:**

| 策略 | 行为 |
|------|------|
| `FEEDBACK_DRIVEN` | 失败时注入 Critic 反馈重试，通过即停止 |
| `EXHAUSTIVE` | 运行所有 max_attempts 次，返回最高分结果 |
| `ONCE` | 单次尝试，无重试 |

**关键特性:**
- **动态模型路由:** 根据失败次数自动升级到更贵的模型
- **共识验证:** 多模型投票决定是否通过
- **指纹验证:** 检测 Prompt 漂移
- **程序化验证:** regex/json_schema/code_eval 无需 LLM 成本

---

### 1.3 orchestrator.py — DAG 调度器

**职责:** 管理从采样到全量生产的完整生命周期，支持 DAG 拓扑执行。

**类:** `TROrchestrator`

**核心方法:**

| 方法 | 说明 |
|------|------|
| `run_sampling_gate(data)` | 执行 1% 采样阶段 |
| `run_mass_production(data)` | 执行全量生产 |
| `pause()` | 暂停执行 (线程安全) |
| `resume(new_prompt)` | 恢复执行 (可修改 Prompt) |
| `request_replay(new_prompt, rollback)` | 请求重放 (支持时间线回滚) |
| `spot_check(results, sample_rate)` | 随机抽样人工复核 |

**DAG 执行:**
```
workflow:
  - id: "classifier"
    depends_on: []
  - id: "summarizer"
    depends_on: ["classifier"]
  - id: "validator"
    depends_on: ["summarizer"]

拓扑排序 (Kahn 算法):
  1. classifier (入度=0)
  2. summarizer (入度=0, 依赖已满足)
  3. validator (入度=0, 依赖已满足)

执行: 按拓扑顺序逐节点处理
  classifier 输出 → summarizer 输入 → validator 输入
```

**质量熔断:**
```python
# 滑动窗口监控
self._recent_scores: List[float]  # 最近 N 个任务的评分
self.quality_threshold = 0.6       # 阈值
self.quality_window = 5            # 窗口大小

# 当窗口内所有评分都低于阈值时熔断
if all(s < threshold for s in self._recent_scores):
    self._quality_halted = True
```

**漂移检测:**
```
每隔 N 个任务:
  1. 运行 golden samples (哈希或语义比较)
  2. 如果匹配率下降 → DriftAlert
  3. drift_action: "warn" | "halt" | "resample"
```

---

### 1.4 actor.py — Actor 执行器

**职责:** 使用昂贵模型生成输出，支持 Jinja2 模板渲染和反馈注入。

**类:** `TaskActor`

```python
class TaskActor:
    def __init__(self, provider: LLMProvider): ...

    async def generate(
        self,
        template_str: str,    # Jinja2 模板
        data: str,            # 输入数据
        feedback: str = "",   # 上一轮 Critic 反馈
    ) -> LLMResponse:
        # 1. Jinja2 SandboxedEnvironment 渲染
        # 2. 注入 feedback (如果有)
        # 3. LLMProvider.request(messages, temperature=0.1)
```

**模板示例:**
```yaml
actor_prompt_template: |
  请将以下文本分类为类别。
  输出 JSON: {"category": "...", "confidence": 0.0-1.0}

  {% if feedback %}
  上次输出有问题，请根据以下反馈改进:
  {{ feedback }}
  {% endif %}

  文本: {{ data }}
```

---

### 1.5 critic.py — Critic 审计器

**职责:** 使用廉价模型审计 Actor 输出，返回结构化 JSON 评分。

**类:** `TaskCritic`

```python
class TaskCritic:
    def __init__(self, provider: LLMProvider): ...

    async def evaluate(
        self,
        task_name: str,
        input_data: str,
        output_content: str,
        rules: List[ValidationRule],
    ) -> EvaluationResult:
        # 1. 构建评估 Prompt (包含规则和评分维度)
        # 2. LLMProvider.request(response_format={"type": "json_object"})
        # 3. 解析 JSON 输出为 EvaluationResult
```

**Critic 输出格式:**
```json
{
    "passed": true,
    "score": 0.92,
    "scores": {
        "accuracy": 0.95,
        "completeness": 0.88,
        "format": 0.93
    },
    "critique": null,
    "suggestions": ["可以增加更多细节"]
}
```

---

### 1.6 eval_judge.py — 多维质量评估器

**职责:** 使用多个评估维度并发评估输出质量。

**类:** `EvalJudge`

**5 个内置评估器:**

| 评估器 | 类型 | 检查内容 |
|--------|------|----------|
| `safety_evaluator` | 规则 | 注入模式、PII 泄漏、有害内容 |
| `code_quality_evaluator` | 规则 | 语法检查、嵌套深度、错误处理 |
| `completeness_evaluator` | 规则 | 关键词覆盖率、长度比例 |
| `coherence_evaluator` | 规则 | 重复行、句子长度变化、矛盾标记 |
| `correctness_evaluator` | LLM | 使用 LLM 评估正确性 (可回退到启发式) |

**使用方式:**
```python
from core.eval_judge import EvalJudge, EvalDimension, safety_evaluator, completeness_evaluator

judge = EvalJudge(
    dimensions=[
        EvalDimension(name="safety", weight=2.0, evaluator=safety_evaluator),
        EvalDimension(name="completeness", weight=1.0, evaluator=completeness_evaluator),
    ],
    threshold=0.7,
)

result = await judge.evaluate(input_data="...", output="...")
# result.passed, result.weighted_score, result.scores, result.summary
```

---

### 1.7 ledger.py — Token 预算管理

**职责:** 追踪每次 LLM 调用的 Token 消耗，预算超限时立即熔断。

**类:** `TokenLedger`

```python
class TokenLedger:
    def __init__(self, budget_usd: float): ...

    def record_usage(self, model_name, prompt_tokens, completion_tokens, role): ...
    def get_summary(self) -> str: ...
    def get_roi_report(self, data_count, success_count, skill_id) -> str: ...

    @property
    def is_fused(self) -> bool:  # 是否已熔断
```

**定价策略:**
- 已知模型: 使用内置价格表 (GPT-4o, GPT-4o-mini, DeepSeek, etc.)
- 未知模型: 使用保守回退价格 (按 GPT-4o-mini 价格)

**熔断机制:**
```
每次 record_usage():
  total_cost += (prompt_tokens * prompt_price + completion_tokens * completion_price)
  if total_cost >= budget_usd:
      raise BudgetExceededError(f"预算已耗尽: ${total_cost:.4f} >= ${budget_usd:.2f}")
```

---

### 1.8 persistence.py — SQLite 持久化

**职责:** 原子化存储每次迭代的执行 trace，支持检查点/恢复。

**类:** `TaskPersistence`

**数据库表:**
```sql
CREATE TABLE task_traces (
    id TEXT PRIMARY KEY,           -- "{node_id}:{input_hash}"
    input_hash TEXT,               -- SHA-256 前16位
    status TEXT,                   -- pending/running/completed/failed
    trace_data TEXT,               -- JSON: {"iterations": [...]}
    final_output TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

**异步包装器 (非阻塞事件循环):**
```python
async def async_save_trace(self, unit_id, input_hash, status, trace, output):
    await asyncio.to_thread(self.save_trace, unit_id, input_hash, status, trace, output)

async def async_get_status(self, unit_id) -> Optional[str]:
    return await asyncio.to_thread(self.get_status, unit_id)
```

---

### 1.9 sandbox.py — 安全代码执行

**职责:** 为 `code_eval` 规则提供安全的代码执行环境。

**类:** `SandboxExecutor`

**安全层级:**

| 层级 | 机制 | 说明 |
|------|------|------|
| 1 | AST 静态检查 | 阻断危险模块和属性访问 |
| 2 | 安全前导码 | 禁用 __import__, exec, eval, gc |
| 3 | open() 覆写 | 阻断所有写模式 (r+, w+, a+, +) |
| 4 | UUID 哨兵 | 防止输出伪造 |
| 5 | 子进程隔离 | 受限环境变量 + 超时 |
| 6 | 临时文件清理 | try/finally 确保清理 |

---

### 1.10 resilience.py — 容错模式

**3 个组件:**

**CircuitBreaker (熔断器):**
```
状态机: CLOSED → OPEN → HALF_OPEN → CLOSED

CLOSED: 正常处理，计数失败
  连续失败 >= failure_threshold → OPEN

OPEN: 拒绝所有调用，抛出 CircuitOpenError
  等待 recovery_timeout → HALF_OPEN

HALF_OPEN: 允许 half_open_max 个探测调用
  成功 → CLOSED
  失败 → OPEN
```

**Bulkhead (舱壁):**
```
限制并发调用数和队列大小
  active < max_concurrent → 执行
  active >= max_concurrent, queue < max_queue → 入队等待
  queue >= max_queue → 抛出 BulkheadFullError
```

**RetryPolicy (重试策略):**
```
指数退避: delay = min(base_delay * 2^attempt, max_delay)
可重试状态码: {429, 500, 502, 503, 504}
尊重 Retry-After 头
回调: on_retry(attempt, delay, exception)
```

---

### 1.11 solidifier.py — 技能固化

**职责:** 从执行 trace 中提取最优 Prompt 和黄金样本，打包为 .trs 技能文件。

**类:** `SkillSolidifier`

**.trs 文件格式:**
```json
{
    "skill_id": "TR-SKILL-a1b2c3d4e5f6",
    "name": "Finance_Refinery",
    "optimized_prompt": "请分类以下交易...",
    "model_config": {"model": "gpt-4o"},
    "validation_rules": [...],
    "golden_samples": [
        {"input": "...", "output": "...", "score": 0.95},
        ...
    ],
    "stats": {
        "success_rate": 0.92,
        "average_retries": 1.3,
        "total_processed": 100
    },
    "created_at": "2026-06-28T12:00:00"
}
```

**Fine-tune 数据导出:**
```python
solidifier.export_fine_tune(traces, format="openai", min_score=0.8)
# format: "openai" | "alpaca" | "sharegpt"
```

---

### 1.12 其他核心模块

| 模块 | 类 | 职责 |
|------|-----|------|
| `prompt_lineage.py` | `PromptLineageManager` | Prompt 版本控制 (parent_id 跟踪, pass_rate 统计) |
| `drift_detector.py` | `DriftDetector` | 哈希+语义漂移检测，触发 DriftAlert |
| `self_healer.py` | `SelfHealer` | 分析重复失败模式，建议 Prompt 优化 |
| `context_cache.py` | `ContextCache` | LRU 缓存 + TTL，前缀排序优化缓存命中 |
| `task_queue.py` | `TaskQueue` | 有界优先级队列 (maxsize=max_concurrent*10) |
| `cost_scheduler.py` | `CostScheduler` | LOW 优先级任务路由到 Batch API (50% 成本) |
| `sampling_manager.py` | `SamplingManager` | 采样报告生成，审批门控制 |
| `telemetry.py` | `TelemetryManager` | 事件广播 (回调处理器) |
| `output_sink.py` | `FileSink/DuckDBSink/VectorSink` | 输出持久化到文件/数据库/向量库 |
| `mcp_server.py` | `TokenRunMCPServer` | MCP 协议服务器 (4 个工具) |
| `app.py` | `TokenRunApp` | 主控制器，组装所有组件 |

---

## 2. 网关层

### 2.1 provider.py — LLM 客户端

**职责:** 异步 HTTP 客户端，支持 OpenAI-compatible API。

**类:** `LLMProvider`

**特性:**
- 指数退避重试 (可重试状态: 429, 500, 502, 503, 504)
- 尊重 Retry-After 头
- SSRF 防护 (_is_private_host 检查)
- 支持 chat completions 和 embeddings
- 可选 CircuitBreaker 集成

```python
provider = LLMProvider(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model_name="gpt-4o",
    timeout=60.0,
    max_retries=3,
)

# Chat completion
response = await provider.request(
    messages=[{"role": "user", "content": "Hello"}],
    temperature=0.1,
    response_format={"type": "json_object"},
)

# Embedding
vector = await provider.embed(text="Hello world", model="text-embedding-3-small")
```

### 2.2 privacy.py — 隐私脱敏

**职责:** 可逆 PII 脱敏，使用占位符替换。

**类:** `PrivacyRedactor` / `PersistentRedactor`

**支持的 PII 类型:**

| 类型 | 正则模式 | 示例 |
|------|----------|------|
| EMAIL | `[a-zA-Z0-9_.+\-]+@...` | alice@example.com → [[TR_EMAIL_1]] |
| PHONE | `(?:\+?86)?1[3-9]\d{9}` | 13800138000 → [[TR_PHONE_1]] |
| ID_CARD | `\b\d{17}[\dXx]\b` | 11010119900101001X → [[TR_ID_CARD_1]] |
| IP_ADDR | `\b(?:\d{1,3}\.){3}\d{1,3}\b` | 192.168.1.1 → [[TR_IP_ADDR_1]] |
| API_KEY | `sk-[a-zA-Z0-9]{20,}` | sk-abc123... → [[TR_API_KEY_1]] |

**PersistentRedactor 额外功能:**
- vault 映射持久化到 SQLite `privacy_vault` 表
- 崩溃恢复: `restore_from_db(task_id)`
- 任务级清理: `clear_task_vault(task_id)`

### 2.3 file_gateway.py — 文件网关

**职责:** 流式读取本地目录中的文件。

```python
gw = FileGateway("/path/to/data")
for file_info in gw.stream_files("**/*.txt"):
    print(file_info["content"])

# 写入结果 (路径遍历防护)
gw.save_result("output.txt", "content", suffix=".refined", output_dir="/output")
```

### 2.4 batch_provider.py — Batch API

**职责:** OpenAI Batch API 集成 (50% 成本，24 小时窗口)。

```python
async with BatchProvider(api_key="sk-...") as provider:
    batch_id = await provider.submit_batch(requests, completion_window="24h")
    job = await provider.wait_for_completion(batch_id, on_progress=callback)
    results = await provider.retrieve_results(job)
```

### 2.5 其他网关

| 网关 | 职责 |
|------|------|
| `s3_gateway.py` | S3 兼容存储读取 (有 close 方法) |
| `sql_gateway.py` | SQL 数据库查询 (有 close 方法) |
| `duckdb_gateway.py` | DuckDB 数据库查询 |
| `mcp_client.py` | MCP 协议客户端 (JSON-RPC 2.0) |
| `video_gateway.py` | 视频帧提取 |
| `audio_gateway.py` | 音频转录 (Whisper) |

---

## 3. API 层

### 3.1 main.py — FastAPI 后端

**端点:** 14 个 REST + 1 个 WebSocket + 1 个 SSE

详见 [api-reference.md](./api-reference.md)

---

## 4. 前端

### 4.1 页面结构

| 页面 | 路由 | 功能 |
|------|------|------|
| Dashboard | `/` | ROI 图表、任务统计、技能概览 |
| Missions | `/missions` | 任务创建、审批、时间旅行调试、Prompt 编辑器 |
| Skills | `/skills` | 技能列表、一键重跑 |

### 4.2 技术栈

- Next.js 14 (App Router)
- TailwindCSS
- TypeScript
- Lucide icons
- 自定义 WebSocket hook (`use-telemetry.ts`)

---

## 5. 模块依赖图

```
api/main.py
  └── core/app.py (TokenRunApp)
        ├── core/orchestrator.py (TROrchestrator)
        │     ├── core/runner.py (ActorCriticLoop)
        │     │     ├── core/actor.py (TaskActor)
        │     │     │     └── gateway/provider.py (LLMProvider)
        │     │     ├── core/critic.py (TaskCritic)
        │     │     │     └── gateway/provider.py (LLMProvider)
        │     │     ├── core/eval_judge.py (EvalJudge)
        │     │     │     └── 5 built-in evaluators
        │     │     ├── gateway/privacy.py (PrivacyRedactor)
        │     │     └── core/persistence.py (TaskPersistence)
        │     ├── core/quality_gate.py (QualityGate)
        │     ├── core/drift_detector.py (DriftDetector)
        │     ├── core/self_healer.py (SelfHealer)
        │     ├── core/resilience.py (CircuitBreaker, Bulkhead)
        │     ├── core/context_cache.py (ContextCache)
        │     ├── core/task_queue.py (TaskQueue)
        │     └── core/telemetry.py (TelemetryManager)
        ├── gateway/privacy.py (PrivacyRedactor)
        ├── gateway/file_gateway.py (FileGateway)
        ├── gateway/mcp_client.py (MCPClient)
        ├── core/ledger.py (TokenLedger)
        ├── core/persistence.py (TaskPersistence)
        ├── core/solidifier.py (SkillSolidifier)
        ├── core/prompt_lineage.py (PromptLineageManager)
        └── core/sampling_manager.py (SamplingManager)
```
