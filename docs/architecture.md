# TokenRun 技术架构文档

---

## 架构总览

TokenRun 采用**流水线架构**，将 AI Token 的不确定性转化为确定性的高质量产出。

```
┌─────────────────────────────────────────────────────────────────┐
│                        TokenRun 架构                             │
│                                                                  │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │  Input    │→ │ Privacy  │→ │  Actor   │→ │  Critic  │     │
│  │  Layer    │   │ Redactor │   │ (贵模型) │   │ (便宜模型)│     │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘     │
│       ↓                              ↓              ↓            │
│  ┌──────────┐                 ┌──────────┐   ┌──────────┐     │
│  │  Source   │                 │ Output   │   │ EvalJudge│     │
│  │  Gateway  │                 │   Sink   │   │ 多维评估  │     │
│  └──────────┘                 └──────────┘   └──────────┘     │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Core Engine                             │  │
│  │  Runner → Orchestrator → Ledger → Persistence            │  │
│  │  QualityGate → DriftDetector → SelfHealer                │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    API Layer (FastAPI)                      │  │
│  │  REST + WebSocket + SSE → Cockpit UI (Next.js)           │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 核心模块

### 1. 协议层 (`core/models.py`)

**职责**：定义所有数据结构的 Pydantic V2 模型。

| 模型 | 说明 |
|------|------|
| `Runfile` | 顶层任务蓝图，`extra="forbid"` 严格校验 |
| `TaskNode` | 工作流节点，支持 DAG 依赖 |
| `LoopConfig` | 循环策略配置（策略、重试、评分权重） |
| `ValidationRule` | 验证规则（regex/json_schema/code_eval/llm_eval） |
| `EvaluationResult` | Critic 评估结果（分数、批评、建议） |
| `TaskTrace` | 完整执行轨迹 |
| `OutputSinkConfig` | 输出持久化配置 |

**设计原则**：所有模型使用 `extra="forbid"`，拒绝未知字段。

### 2. 执行引擎 (`core/runner.py`)

**职责**：驱动 Actor-Critic 循环。

```
run(node, input_data)
    ↓
[隐私脱敏] → [指纹校验] → [持久化检查]
    ↓
[循环开始]
    ├─ [动态模型路由] → Actor 生成
    ├─ [程序化规则] → regex/json_schema/code_eval
    ├─ [EvalJudge 或 TaskCritic] → 多维度评估
    ├─ [共识审计] → 多模型投票（可选）
    ├─ [加权评分] → 综合得分
    └─ [持久化保存]
    ↓
[通过 → 返回] / [失败 → 反馈注入 → 重试]
```

**关键方法**：
- `run()` — 主循环入口
- `_resolve_tier_provider()` — 动态模型升级
- `_run_programmatic_rules()` — 无 LLM 的程序化验证
- `_run_consensus()` — 并行共识投票
- `_compute_weighted_score()` — 加权评分计算

### 3. 编排器 (`core/orchestrator.py`)

**职责**：DAG 调度、并发控制、质量熔断。

```
run_mass_production(data_stream)
    ↓
[指纹校验] → [前缀排序缓存优化]
    ↓
_process_dag(data_stream)
    ↓
[拓扑排序] → [按依赖顺序执行节点]
    ↓
_process_batch(batch)
    ├─ [TaskQueue 路由]（可选）
    └─ [asyncio.gather 并发]
    ↓
_bounded_execute(data)
    ├─ [暂停检查] → [质量熔断检查]
    ├─ [治理限制检查] → [信号量控制]
    ├─ [Runner 执行]
    ├─ [漂移检测] → [自愈记录]
    └─ [遥测广播]
    ↓
[OutputSink 自动持久化]（可选）
```

**并发控制**：
- `asyncio.Semaphore` 限制并发数
- `asyncio.Lock` 保护共享状态
- 可选 `TaskQueue` 优先级调度

### 4. 质量护栏

#### QualityGate (`core/quality_gate.py`)

滑动窗口质量熔断器：

```python
gate = QualityGate(threshold=0.6, window_size=5, recovery_window=3)
gate.record_score(0.3)  # 记录分数
gate.is_halted()        # 检查是否熔断
```

- 连续 N 个低分 → 自动熔断
- 支持恢复窗口（连续 M 个好分后自动解除）

#### EvalJudge (`core/eval_judge.py`)

多维度质量评估器：

```python
judge = EvalJudge(dimensions=[
    EvalDimension("safety", weight=0.4, evaluator=safety_evaluator),
    EvalDimension("completeness", weight=0.3, evaluator=completeness_evaluator),
    EvalDimension("coherence", weight=0.3, evaluator=coherence_evaluator),
])
result = await judge.evaluate(input_data, output)
```

**内置评估器**：
- `safety_evaluator` — 注入检测、PII 泄漏
- `completeness_evaluator` — 关键词覆盖率
- `coherence_evaluator` — 逻辑连贯性
- `code_quality_evaluator` — 代码质量（AST）
- `correctness_evaluator` — LLM 正确性评估

#### DriftDetector (`core/drift_detector.py`)

语义漂移检测：

- **Hash-based**：SHA-256 输出哈希比对
- **Semantic**：Embedding 余弦相似度监控
- 可配置动作：`halt`（停止）、`warn`（告警）、`resample`（重采样）

### 5. 韧性层 (`core/resilience.py`)

**CircuitBreaker** — 断路器：

```
CLOSED ──[失败达阈值]→ OPEN ──[超时]→ HALF_OPEN ──[成功]→ CLOSED
                                ↑                           │
                                └────────[失败]─────────────┘
```

**Bulkhead** — 舱壁隔离：

```python
bulkhead = Bulkhead(max_concurrent=10, max_queue=50)
result = await bulkhead.execute(task_func, data)
```

**RetryPolicy** — 重试策略：

```python
policy = RetryPolicy(max_retries=3, base_delay=1.0, max_delay=30.0)
result = await policy.execute(risky_operation)
```

### 6. 隐私网关 (`gateway/privacy.py`)

**PrivacyRedactor** — 可逆 PII 脱敏：

```
原文: "联系 alice@example.com，电话 13800138000"
脱敏: "联系 [[TR_EMAIL_1]]，电话 [[TR_PHONE_2]]"
还原: "联系 alice@example.com，电话 13800138000"
```

**PersistentRedactor** — 持久化脱敏（崩溃恢复）：

- Vault 映射实时写入 SQLite
- 崩溃后 `restore_from_db()` 精确还原
- 增量持久化，O(1) 写入

### 7. 资产化

#### SkillSolidifier (`core/solidifier.py`)

```
任务完成 → 提取最优 Prompt + 黄金样本 → .trs 文件
```

#### OutputSink (`core/output_sink.py`)

| Sink | 用途 |
|------|------|
| `FileSink` | JSONL 文件输出 |
| `DuckDBSink` | 结构化数据库 |
| `VectorSink` | 向量数据库（Chroma） |

#### Auto-Distillation

成功率 > 90% 且样本达标时，自动导出微调数据集：
- OpenAI JSONL 格式
- Alpaca 格式
- ShareGPT 格式

---

## 数据流

```
用户 Runfile
    ↓
TokenRunApp.run_mission()
    ↓
[1% 采样] → 人工审批
    ↓
[全量生产]
    ├─ 数据输入 → FileGateway/S3Gateway/SQLGateway
    ├─ 隐私脱敏 → PersistentRedactor
    ├─ Actor-Critic 循环 → Runner
    ├─ 质量监控 → QualityGate + DriftDetector
    ├─ 自愈优化 → SelfHealer
    └─ 输出持久化 → OutputSink
    ↓
[技能固化] → .trs 文件
[知识蒸馏] → 微调数据集
```

---

## 并发模型

```
                    ┌─────────────────┐
                    │  Orchestrator   │
                    │  (async main)   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ↓              ↓              ↓
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Task 1   │  │ Task 2   │  │ Task 3   │
        │ (coroutine)│ │ (coroutine)│ │ (coroutine)│
        └──────────┘  └──────────┘  └──────────┘
              │              │              │
              └──────────────┼──────────────┘
                             ↓
                    ┌─────────────────┐
                    │   Semaphore     │
                    │  (并发限制)      │
                    └─────────────────┘
```

**线程安全**：
- `TokenLedger` — `threading.Lock`
- `TaskPersistence` — `threading.Lock`
- `Orchestrator` — `asyncio.Lock`
- `CircuitBreaker` — `asyncio.Lock`
- `Bulkhead` — `asyncio.Lock`

---

## 安全架构

### 沙箱执行 (`core/sandbox.py`)

```
code_eval 规则
    ↓
[AST 静态分析]
    ├─ 检测 Import/ImportFrom 节点
    ├─ 禁用 exec/eval/compile/__import__
    └─ 阻断 os/subprocess/socket
    ↓
[安全前言注入]
    ├─ 禁用危险 builtins
    └─ 重写 open() 阻止写入
    ↓
[子进程执行]
    ├─ 受限 PATH 环境变量
    └─ 超时控制
```

### 路径安全

- `FileGateway` — `is_relative_to()` 路径校验
- `api/main.py` — `resolve()` + `is_relative_to()` 双重检查
- `skill_id` — `Path.name` 剥离 + `resolve()` 验证

---

## 部署架构

```
┌─────────────────────────────────────────┐
│              Docker Compose              │
│                                          │
│  ┌──────────────┐  ┌──────────────┐     │
│  │   Backend    │  │   Frontend   │     │
│  │  (FastAPI)   │  │  (Next.js)   │     │
│  │   :8000      │  │   :3000      │     │
│  └──────────────┘  └──────────────┘     │
│         ↑                │               │
│         └────────────────┘               │
│           REST + WS + SSE                │
└─────────────────────────────────────────┘
```

### 环境变量

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | 共享 API Key | — |
| `ACTOR_API_KEY` | Actor 专用 Key | 继承 OPENAI_API_KEY |
| `ACTOR_BASE_URL` | Actor API 地址 | `https://api.openai.com/v1` |
| `ACTOR_MODEL` | Actor 模型名 | `gpt-4o` |
| `CRITIC_API_KEY` | Critic 专用 Key | 继承 ACTOR_API_KEY |
| `CRITIC_BASE_URL` | Critic API 地址 | 继承 ACTOR_BASE_URL |
| `CRITIC_MODEL` | Critic 模型名 | `gpt-4o-mini` |
