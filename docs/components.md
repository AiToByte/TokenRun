# TokenRun 组件分析

---

## 模块依赖图

```
┌─────────────────────────────────────────────────────────────┐
│                         API Layer                            │
│                    api/main.py (FastAPI)                      │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                      App Controller                          │
│                      core/app.py                             │
└──┬──────────┬──────────┬──────────┬──────────┬──────────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
┌──────┐ ┌──────────┐ ┌──────┐ ┌──────┐ ┌──────────┐
│Runner│ │Orchestr. │ │Ledger│ │Solid.│ │ Sampling │
│      │ │          │ │      │ │      │ │ Manager  │
└──┬───┘ └────┬─────┘ └──────┘ └──────┘ └──────────┘
   │         │
   ▼         ▼
┌──────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│Actor │ │Critic    │ │EvalJudge │ │DriftDet. │
│      │ │          │ │          │ │          │
└──┬───┘ └────┬─────┘ └──────────┘ └──────────┘
   │         │
   ▼         ▼
┌──────────────────────────────────────────────┐
│              Gateway Layer                     │
│  Provider │ Privacy │ FileGateway │ Batch     │
└──────────────────────────────────────────────┘
```

---

## 核心模块详解

### `core/models.py` — 协议定义

**文件大小**: ~260 行
**依赖**: pydantic

定义了 TokenRun 的完整数据协议（TRP）：

| 模型 | 字段数 | 用途 |
|------|--------|------|
| `Runfile` | 10 | 顶层任务蓝图 |
| `TaskNode` | 10 | 工作流节点 |
| `LoopConfig` | 12 | 循环策略 |
| `ValidationRule` | 3 | 验证规则 |
| `EvaluationResult` | 6 | 评估结果 |
| `TaskTrace` | 4 | 执行轨迹 |
| `OutputSinkConfig` | 8 | 输出配置 |

**设计亮点**：所有模型 `extra="forbid"`，拒绝未知字段。

---

### `core/runner.py` — 执行引擎

**文件大小**: ~600 行
**依赖**: actor, critic, eval_judge, ledger, persistence, privacy

核心类 `ActorCriticLoop`：

| 方法 | 行数 | 职责 |
|------|------|------|
| `run()` | 180 | 主循环：执行→评估→重试 |
| `_resolve_tier_provider()` | 25 | 动态模型升级 |
| `_run_programmatic_rules()` | 50 | regex/json_schema/code_eval |
| `_run_consensus()` | 45 | 并行共识投票 |
| `_compute_weighted_score()` | 15 | 加权评分 |
| `_resolve_critic_provider()` | 30 | Ollama/自定义 Critic |
| `compute_fingerprint()` | 25 | 指纹计算 |

**关键设计**：
- EvalJudge 优先于 TaskCritic
- 共识模型并行执行（`asyncio.gather`）
- 每次迭代独立持久化

---

### `core/orchestrator.py` — 编排器

**文件大小**: ~650 行
**依赖**: runner, ledger, persistence, drift_detector, self_healer, task_queue, telemetry

核心类 `TROrchestrator`：

| 方法 | 行数 | 职责 |
|------|------|------|
| `run_sampling_gate()` | 20 | 1% 采样 |
| `run_mass_production()` | 40 | 全量生产入口 |
| `_process_dag()` | 50 | DAG 拓扑执行 |
| `_process_batch()` | 30 | 批量并发调度 |
| `_process_batch_via_queue()` | 30 | TaskQueue 优先级路由 |
| `_bounded_execute()` | 100 | 单任务执行（含熔断、漂移、自愈） |
| `_persist_to_sink()` | 30 | OutputSink 自动持久化 |
| `request_replay()` | 30 | 重放 + 时间线回滚 |
| `spot_check()` | 20 | 随机抽检 |

**并发控制**：
- `asyncio.Semaphore` — 并发限制
- `asyncio.Lock` — 共享状态保护
- 可选 `TaskQueue` — 优先级调度

---

### `core/eval_judge.py` — 多维评估

**文件大小**: ~500 行
**依赖**: asyncio, inspect

核心类 `EvalJudge`：

| 方法 | 职责 |
|------|------|
| `evaluate()` | 并行执行所有维度评估 |
| `register_dimension()` | 注册新评估维度 |
| `_compute_weighted_score()` | 加权评分 |
| `_run_dimension()` | 单维度执行（缓存签名） |

**内置评估器**（5 个）：

| 评估器 | 类型 | 检测内容 |
|--------|------|---------|
| `safety_evaluator` | 规则 | 注入、PII、危险代码 |
| `completeness_evaluator` | 规则 | 关键词覆盖率 |
| `coherence_evaluator` | 规则 | 逻辑连贯性 |
| `code_quality_evaluator` | 规则 | 语法、嵌套、异常处理 |
| `correctness_evaluator` | LLM | 正确性（需 Provider） |

---

### `core/resilience.py` — 韧性套件

**文件大小**: ~300 行
**依赖**: asyncio, time

三个独立组件：

| 组件 | 状态数 | 线程安全 |
|------|--------|---------|
| `CircuitBreaker` | 3 (CLOSED/OPEN/HALF_OPEN) | `asyncio.Lock` |
| `Bulkhead` | N/A | `asyncio.Lock` |
| `RetryPolicy` | N/A | 无状态 |

**CircuitBreaker 状态机**：
```
CLOSED ──[失败≥阈值]→ OPEN ──[超时]→ HALF_OPEN
  ↑                                       │
  └────────[成功≥half_open_max]───────────┘
```

---

### `gateway/privacy.py` — 隐私引擎

**文件大小**: ~350 行
**依赖**: re, sqlite3

两个类：

| 类 | 持久化 | 用途 |
|----|--------|------|
| `PrivacyRedactor` | 内存 | 基础脱敏 |
| `PersistentRedactor` | SQLite | 崩溃恢复 |

**PII 模式**（5 种）：

| 标签 | 模式 | 示例 |
|------|------|------|
| EMAIL | `[a-zA-Z0-9_.+-]+@...` | `alice@example.com` |
| PHONE | `(?:\+?86)?1[3-9]\d{9}` | `13800138000` |
| ID_CARD | `\b\d{17}[\dXx]\b` | `11010119900101001X` |
| IP_ADDR | `\b(?:\d{1,3}\.){3}\d{1,3}\b` | `192.168.1.1` |
| API_KEY | `sk-[a-zA-Z0-9]{20,}` | `sk-abc123...` |

---

### `core/sandbox.py` — 安全沙箱

**文件大小**: ~200 行
**依赖**: ast, subprocess, tempfile

安全机制：

| 层 | 技术 | 防御 |
|----|------|------|
| 静态分析 | AST | 阻断 import/exec/eval |
| 运行时 | builtins 覆写 | 禁用 __import__/open |
| 进程级 | 子进程 + 受限 PATH | 隔离执行环境 |
| 超时 | subprocess timeout | 防止死循环 |

---

### `core/quality_gate.py` — 质量熔断

**文件大小**: ~120 行
**依赖**: collections.deque

| 方法 | 职责 |
|------|------|
| `record_score()` | 记录分数 + 检查熔断 |
| `is_halted()` | 查询熔断状态 |
| `reset()` | 手动重置 |
| `get_report()` | 诊断报告 |

**特性**：
- 滑动窗口（deque）
- 自动恢复窗口
- 严格阈值（`<` 而非 `<=`）

---

### `core/output_sink.py` — 输出持久化

**文件大小**: ~230 行
**依赖**: json, duckdb (可选), chromadb (可选)

| Sink | 存储 | 用途 |
|------|------|------|
| `FileSink` | JSONL 文件 | 通用输出 |
| `DuckDBSink` | DuckDB | 结构化分析 |
| `VectorSink` | ChromaDB | RAG 向量化 |

工厂函数 `create_sink(config)` 从配置字典创建 Sink。

---

## 测试覆盖

| 测试文件 | 测试数 | 覆盖模块 |
|---------|--------|---------|
| `test_eval_judge.py` | 45 | EvalJudge + 内置评估器 |
| `test_resilience.py` | 30 | CircuitBreaker + Bulkhead + RetryPolicy |
| `test_context_cache.py` | 17 | ContextCache |
| `test_quality_gate.py` | 18 | QualityGate |
| `test_runner.py` | 16 | ActorCriticLoop |
| `test_orchestrator.py` | 10 | TROrchestrator |
| `test_privacy.py` | 17 | PrivacyRedactor |
| `test_sandbox.py` | 9 | SandboxExecutor |
| `test_api.py` | 12 | FastAPI 端点 |
| 其他 (22 文件) | 310 | 模型、持久化、网关等 |
| **总计** | **484** | |

---

## 代码质量指标

| 指标 | 值 |
|------|-----|
| 总代码行数 | ~8,000 (Python) |
| 测试行数 | ~4,000 |
| 测试/代码比 | 0.5 |
| 最大文件 | orchestrator.py (650 行) |
| 平均文件大小 | ~250 行 |
| 类型提示覆盖 | 100% |
| 文档字符串覆盖 | 100% |
