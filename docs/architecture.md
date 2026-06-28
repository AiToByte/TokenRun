# TokenRun 技术架构文档

---

## 1. 系统概述

TokenRun 是一个工业级 AI 任务执行框架，通过 **Loop Engineering（循环工程）** 将不可靠的 AI 输出转化为确定性的高质量结果。核心机制是 Actor-Critic 反馈循环：昂贵模型（Actor）生成输出，廉价模型（Critic）审计质量，系统迭代直到满足质量标准。

### 1.1 设计哲学

| 原则 | 实现方式 |
|------|----------|
| **确定性优先** | 默认 `temperature=0.1`；指纹锁定（model_id + prompt_hash + params）防止 Prompt 漂移 |
| **廉价模型审计昂贵模型** | Critic 使用结构化 JSON 输出，可靠解析；支持多模型共识投票 |
| **可逆脱敏** | 隐私替换使用 `[[TR_{LABEL}_{N}]]` 占位符映射，仅存内存，任务结束后销毁 |
| **财务安全第一** | 每次 LLM 调用经过 Ledger 记账；预算超限立即熔断（`BudgetExceededError`） |
| **程序化验证节省 Token** | `regex`/`json_schema`/`code_eval` 规则无需 LLM 调用；仅 `llm_eval` 使用 Critic |
| **不可变数据** | 所有 Pydantic 模型使用 `extra="forbid"`；优先创建新对象而非修改已有对象 |

### 1.2 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 后端语言 | Python | 3.10+ |
| Web 框架 | FastAPI | 0.100+ |
| 数据校验 | Pydantic V2 | 2.0+ |
| HTTP 客户端 | httpx (async) | 0.25+ |
| 模板引擎 | Jinja2 | 3.1+ |
| 终端美化 | Rich | 13.0+ |
| 存储 | SQLite | (traces) |
| 前端框架 | Next.js | 14 |
| CSS | TailwindCSS | — |
| 测试框架 | pytest + pytest-asyncio | 7.0+ |
| Linting | ruff | — |
| 容器化 | Docker + docker-compose | — |

---

## 2. 系统架构图

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          TokenRun 系统架构                               │
│                                                                         │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │  CLI     │    │  API     │    │ Frontend │    │ MCP      │          │
│  │ main.py  │    │ FastAPI  │    │ Next.js  │    │ Server   │          │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘          │
│       └───────────────┼───────────────┼───────────────┘                │
│                       ▼                                               │
│              ┌─────────────────┐                                      │
│              │   TokenRunApp   │  ← 主控制器 (core/app.py)             │
│              └────────┬────────┘                                      │
│       ┌───────────────┼───────────────┬───────────────┐               │
│       ▼               ▼               ▼               ▼               │
│  ┌─────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐          │
│  │ Privacy │   │Orchestrator│   │  Ledger  │   │Telemetry │          │
│  │Redactor │   │  (DAG调度)  │   │ (预算熔断) │   │ (事件广播) │          │
│  └─────────┘   └─────┬─────┘   └──────────┘   └──────────┘          │
│                       │                                               │
│       ┌───────────────┼───────────────┐                               │
│       ▼               ▼               ▼                               │
│  ┌─────────┐   ┌───────────┐   ┌──────────┐                         │
│  │  Actor  │   │   Critic  │   │EvalJudge │                         │
│  │(昂贵模型) │   │ (廉价模型)  │   │(多维评估) │                         │
│  └────┬────┘   └─────┬─────┘   └──────────┘                         │
│       └───────────────┘                                               │
│                       ▼                                               │
│  ┌─────────────────────────────────┐                                 │
│  │         LLMProvider             │  ← 统一 LLM 客户端               │
│  │  (OpenAI-compatible, httpx)     │                                 │
│  └─────────────────────────────────┘                                 │
│       ┌───────────────┼───────────────┐                               │
│       ▼               ▼               ▼                               │
│  ┌─────────┐   ┌───────────┐   ┌──────────┐                         │
│  │ SQLite  │   │  Skill    │   │  Output  │                         │
│  │ Traces  │   │ Solidifier│   │   Sink   │                         │
│  └─────────┘   └───────────┘   └──────────┘                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    接入层 (Access Layer)                       │
│  CLI (main.py) | REST API (FastAPI) | WebSocket (/ws) | MCP │
├─────────────────────────────────────────────────────────────┤
│                    应用层 (Application Layer)                  │
│  TokenRunApp — sense_resources(), run_mission(), run_from_skill() │
├─────────────────────────────────────────────────────────────┤
│                    编排层 (Orchestration Layer)                │
│  TROrchestrator | TaskQueue | CostScheduler | SamplingManager│
├─────────────────────────────────────────────────────────────┤
│                    执行层 (Execution Layer)                    │
│  ActorCriticLoop | TaskActor | TaskCritic | EvalJudge        │
├─────────────────────────────────────────────────────────────┤
│                    网关层 (Gateway Layer)                      │
│  LLMProvider | PrivacyRedactor | FileGateway | BatchProvider │
├─────────────────────────────────────────────────────────────┤
│                    基础设施层 (Infrastructure Layer)            │
│  SQLite Persistence | Telemetry | Resilience | ContextCache  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心数据流

### 3.1 任务执行全流程

```
用户提交 Runfile (YAML 蓝图)
        │
        ▼
┌─ 1. 初始化 ──────────────────────────────────────────────┐
│  Parse Runfile → Pydantic V2 校验 (extra="forbid")        │
│  构建 LLMProvider (Actor + Critic)                         │
│  初始化所有组件 (Ledger, Persistence, Redactor, ...)       │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 2. 数据感知 ────────────────────────────────────────────┐
│  sense_resources() 扫描 Runfile.context                    │
│  • local:// → FileGateway 流式读取本地文件                  │
│  • mcp://  → MCPClient 调用外部 MCP 工具                   │
│  • 空     → 使用内置演示数据                                │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 3. 采样阶段 (1% Sampling Gate) ─────────────────────────┐
│  取前 N 条数据 (默认 1%)                                    │
│  对每条数据执行 Actor-Critic 循环                           │
│  收集采样结果 + 成本                                        │
│  生成采样报告 (成功率、平均质量、预估总成本)                 │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 4. 人工审批门 (Human-in-the-Loop) ──────────────────────┐
│  暂停执行，等待审批                                         │
│  展示: Runfile 逻辑 + 样本结果 + 经济分析                   │
│  选项: approve(批准) / revise(修改Prompt) / abort(中止)     │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 5. 指纹锁定 ────────────────────────────────────────────┐
│  compute_fingerprint(model_id, prompt_hash, params)        │
│  锁定: 模型ID + Prompt哈希 + temperature + seed            │
│  快照: 首个成功输出的哈希                                   │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 6. 全量生产 ────────────────────────────────────────────┐
│  TROrchestrator.run_mass_production()                      │
│  • 拓扑排序 DAG 节点 (Kahn 算法)                           │
│  • asyncio.Semaphore 并发控制                              │
│  • 前缀排序优化 Prompt 缓存命中率                           │
│  • 对每条数据执行 ActorCriticLoop.run()                    │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 7. Actor-Critic 循环 (每条数据) ────────────────────────┐
│  for attempt in range(max_attempts):                       │
│    ① 隐私脱敏: safe_input = redactor.mask(input_data)     │
│    ② Actor 生成: output = actor.generate(template, data)   │
│    ③ 反脱敏: final_output = redactor.unmask(output)        │
│    ④ 程序化验证: regex/json_schema/code_eval (无LLM成本)   │
│    ⑤ LLM 评估: critic.evaluate() 或 eval_judge.evaluate() │
│    ⑥ 加权评分: weighted_score = sum(score * weight)        │
│    ⑦ 持久化: 保存到 SQLite (async, 非阻塞事件循环)         │
│    ⑧ 判断: passed? → 返回成功 : 注入反馈 → 重试            │
│  策略: FEEDBACK_DRIVEN | EXHAUSTIVE | ONCE                 │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 8. 技能固化 ────────────────────────────────────────────┐
│  SkillSolidifier.distill()                                 │
│  • 提取 Top-5 黄金样本 (按评分排序)                         │
│  • 计算统计: success_rate, average_retries                  │
│  • 生成 skill_id = "TR-SKILL-" + SHA256(prompt)[:12]       │
│  • 写入 .trs JSON 文件到 vault/                            │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 9. 输出持久化 ──────────────────────────────────────────┐
│  OutputSink 写入配置的目标:                                 │
│  • file: JSONL 文件到 output/ 目录                         │
│  • duckdb: DuckDB 表                                      │
│  • vector: ChromaDB 向量集合                               │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌─ 10. 清理与报告 ─────────────────────────────────────────┐
│  • redactor.clear_vault() — 销毁隐私映射                   │
│  • ledger.get_roi_report() — ROI 报告                      │
│  • 自动蒸馏 (成功率 >90% 且 ≥5 条时导出训练数据)            │
└──────────────────────────────────────────────────────────┘
```

### 3.2 Actor-Critic 单条数据处理详细流

```
输入: input_data (原始文本, 含PII)
  │
  ├── PrivacyRedactor.mask(input_data)
  │   输出: safe_input = "联系邮箱 [[TR_EMAIL_1]], 电话 [[TR_PHONE_1]]"
  │
  ├── TaskActor.generate(template, safe_input, feedback)
  │   │
  │   ├── Jinja2 SandboxedEnvironment 渲染
  │   │   template = "请分类: {{ data }}"
  │   │   渲染后 = "请分类: 联系邮箱 [[TR_EMAIL_1]], 电话 [[TR_PHONE_1]]"
  │   │
  │   └── LLMProvider.request(messages, temperature=0.1)
  │       │
  │       ├── HTTP POST {base_url}/chat/completions
  │       │   Headers: Authorization: Bearer {api_key}
  │       │   Body: {model, messages, temperature, response_format}
  │       │
  │       └── 返回: LLMResponse {content, prompt_tokens, completion_tokens, model_name}
  │
  ├── PrivacyRedactor.unmask(output)
  │   输出: final_output = "分类: 金融, 置信度: 0.95"
  │
  ├── 程序化验证 (无LLM成本):
  │   ├── regex: re.search(pattern, final_output)
  │   ├── json_schema: json.loads(final_output) + required字段检查
  │   └── code_eval: SandboxExecutor.execute_python(test_code, variables={output: final_output})
  │
  ├── LLM评估 (使用Critic):
  │   ├── TaskCritic.evaluate() → EvaluationResult {passed, score, scores, critique}
  │   └── 或 EvalJudge.evaluate() → 多维评分 {accuracy, completeness, format, ...}
  │
  ├── 加权评分:
  │   weighted_score = Σ(score_i × weight_i) / Σ(weight_i)
  │   如果 weighted_score < min_score → passed = False
  │
  ├── 持久化 (异步, 非阻塞):
  │   await persistence.async_save_trace(unit_id, status, trace, output)
  │
  └── 决策:
      ├── passed=True → 返回 {status: "success", final_output, history, trace}
      └── passed=False → feedback = critique → 注入下一轮 Actor
```

---

## 4. 并发模型

### 4.1 asyncio 并发控制

```python
class TROrchestrator:
    def __init__(self, ...):
        # 并发限制: 最多 N 个并发 API 调用
        self._semaphore = asyncio.Semaphore(concurrency)

        # 共享状态保护
        self._state_lock = asyncio.Lock()  # 保护 results, _total_iterations, _recent_scores

        # 暂停/恢复控制 (线程安全)
        self._pause_event = asyncio.Event()

        # 重放信号
        self._replay_event = asyncio.Event()
```

**并发执行流程:**
```
data_stream = ["item1", "item2", ..., "itemN"]
    │
    ├── asyncio.gather(*[_bounded_execute(item) for item in data_stream])
    │       │
    │       ├── await self._pause_event.wait()      # 暂停检查
    │       ├── await self._semaphore.acquire()      # 并发限制
    │       ├── result = await self.engine.run(node, data)
    │       ├── async with self._state_lock:         # 安全更新共享状态
    │       │       self.results.append(trace)
    │       │       self._total_iterations += len(history)
    │       └── self._semaphore.release()
```

### 4.2 线程安全设计

| 组件 | 锁类型 | 保护对象 |
|------|--------|----------|
| `TaskPersistence` | `threading.Lock` | SQLite 写入 |
| `TokenLedger` | `threading.Lock` | `record_usage()` 计数 |
| `TROrchestrator` | `asyncio.Lock` | `results`, `_total_iterations`, `_recent_scores` |
| `TaskQueue` | `asyncio.Lock` | `_tasks` 字典 |

**异步 SQLite 包装:**
```python
class TaskPersistence:
    # 同步方法 (线程安全)
    def save_trace(self, unit_id, input_hash, status, trace, output): ...

    # 异步包装器 (非阻塞事件循环)
    async def async_save_trace(self, ...):
        await asyncio.to_thread(self.save_trace, ...)
```

---

## 5. 安全架构

### 5.1 沙箱执行 (code_eval 规则)

```
code_eval 执行流程:
    │
    ├── 1. AST 静态检查 (_check_blocked_imports)
    │   • 阻断模块: socket, http, urllib, subprocess, os, io, pathlib
    │   • 阻断属性: __import__, __builtins__, __subclasses__, __bases__
    │   • 阻断调用: getattr(..., "__dunder__")
    │
    ├── 2. 脚本构建
    │   • 安全前导码:
    │     - import builtins, types, gc
    │     - _builtins.__import__ = None (阻断动态导入)
    │     - _builtins.exec/eval/compile/globals/locals/vars = None
    │     - gc.disable() (防止 __subclasses__ 遍历)
    │     - _safe_open() 覆写: 阻断 r+, w+, a+, + 等写模式
    │   • UUID 哨兵: sentinel = uuid.uuid4().hex[:16]
    │     - print(sentinel + json.dumps({passed, score, output}))
    │     - 用户代码无法预测哨兵值，无法伪造结果
    │
    ├── 3. 子进程执行
    │   • subprocess.run([sys.executable, temp_path], timeout=N)
    │   • 受限环境变量: PATH 仅含 Python 目录
    │   • 临时文件 try/finally 确保清理
    │
    └── 4. 输出解析
        • 遍历 stdout 每一行，查找包含 sentinel 的行
        • 解析 sentinel 后的 JSON 作为结果
        • 未找到 sentinel → 报告注入尝试 (passed=False)
```

### 5.2 隐私保护

```
隐私数据生命周期:
    │
    ├── 输入脱敏: PrivacyRedactor.mask()
    │   正则匹配 → 生成 [[TR_{LABEL}_{N}]] 占位符
    │   正向映射: _vault[placeholder] = original_value
    │   反向映射: _reverse_vault[original_value] = placeholder
    │
    ├── 处理隔离: LLM 只看到脱敏后的数据
    │
    ├── 输出还原: PrivacyRedactor.unmask()
    │   O(1) 反向查找替换
    │
    ├── 持久化保护: 存储 masked 数据到 SQLite
    │   trace.input_payload = safe_input (脱敏后)
    │   trace.output_content = safe_input (脱敏后)
    │   → SQLite 中不包含原始 PII
    │
    └── 清理销毁: redactor.clear_vault()
        _vault.clear()
        _reverse_vault.clear()
        _counter = 0

持久化脱敏 (PersistentRedactor):
    • 继承 PrivacyRedactor
    • 额外将 vault 映射持久化到 SQLite privacy_vault 表
    • 支持崩溃恢复: restore_from_db(task_id)
    • 支持任务级清理: clear_task_vault(task_id)
```

### 5.3 API 安全防线

```
请求 → CORS检查 → API Key验证 → 路径遍历检查 → 业务逻辑
         │              │              │
         │              │              └── Path.is_relative_to()
         │              │                  限制在 runfiles/, vault/, skills/library/
         │              │
         │              └── Authorization: Bearer {TOKENRUN_API_KEY}
         │                  白名单: /health, /docs, /openapi.json
         │
         └── allow_origins: localhost:3000 (可通过 TOKENRUN_CORS_ORIGINS 覆盖)

SSRF 防护 (LLMProvider):
    • _is_private_host(hostname) 检查
    • 阻止: localhost, 127.0.0.1, ::1, 0.0.0.0, 169.254.*
    • 阻止: 私有 IP 地址段 (10.*, 172.16-31.*, 192.168.*)
```

---

## 6. 部署架构

### 6.1 Docker Compose 编排

```
┌─────────────────────────────────────────────────────────┐
│                    Docker Compose                        │
│                                                          │
│  ┌──────────────────────┐  ┌──────────────────────┐     │
│  │      backend         │  │      frontend        │     │
│  │   Python 3.12-slim   │  │   Node.js 20         │     │
│  │   FastAPI + uvicorn  │  │   Next.js 14         │     │
│  │   Port: 8000         │  │   Port: 3000         │     │
│  │                      │  │                      │     │
│  │  volumes:            │  │  env:                │     │
│  │   ./logs → /app/logs │  │   NEXT_PUBLIC_API_URL│     │
│  │   ./vault → /app/vault│  │   =http://backend:8000│    │
│  │   ./runfiles         │  │                      │     │
│  │   → /app/runfiles    │  │  depends_on:         │     │
│  │                      │  │   backend (healthy)  │     │
│  │  healthcheck:        │  │                      │     │
│  │   GET /health 30s    │  │                      │     │
│  └──────────────────────┘  └──────────────────────┘     │
│           │                          │                   │
│           └──────────────────────────┘                   │
│                    网络: tokenrun-net                     │
└─────────────────────────────────────────────────────────┘
```

### 6.2 环境变量配置

| 变量 | 用途 | 默认值 | 继承规则 |
|------|------|--------|----------|
| `OPENAI_API_KEY` | 共享 API Key | — | 回退值 |
| `ACTOR_API_KEY` | Actor 模型 Key | ← OPENAI_API_KEY | — |
| `ACTOR_BASE_URL` | Actor 端点 | `https://api.openai.com/v1` | — |
| `ACTOR_MODEL` | Actor 模型名 | `gpt-4o` | — |
| `CRITIC_API_KEY` | Critic 模型 Key | ← ACTOR_API_KEY | — |
| `CRITIC_BASE_URL` | Critic 端点 | ← ACTOR_BASE_URL | — |
| `CRITIC_MODEL` | Critic 模型名 | `gpt-4o-mini` | — |
| `TOKENRUN_API_KEY` | API 认证 Key | 空 (禁用) | — |
| `TOKENRUN_CORS_ORIGINS` | CORS 源 | `localhost:3000` | 逗号分隔 |

### 6.3 支持的 LLM 提供商

| 提供商 | base_url | 说明 |
|--------|----------|------|
| OpenAI | `https://api.openai.com/v1` | 默认，支持 GPT-4o/mini |
| DeepSeek | `https://api.deepseek.com/v1` | 国内替代 |
| Moonshot | `https://api.moonshot.cn/v1` | 国内替代 |
| Ollama | `http://localhost:11434/v1` | 本地模型，需 `ollama/` 前缀 |
| 任意 OpenAI 兼容 | 自定义 | 代理/中转服务 |

---

## 7. 目录结构

```
TokenRun/
├── core/                        # 核心引擎 (20+ 模块)
│   ├── models.py                # Pydantic V2 协议定义 (~260行)
│   ├── runner.py                # Actor-Critic 循环引擎 (~650行)
│   ├── orchestrator.py          # DAG 调度器 + 并发控制 (~700行)
│   ├── actor.py                 # 昂贵模型执行器 (Jinja2)
│   ├── critic.py                # 廉价模型审计器 (JSON)
│   ├── ledger.py                # Token 预算 + 熔断器
│   ├── persistence.py           # SQLite 持久化 (异步包装)
│   ├── eval_judge.py            # 多维质量评估 (5个内置评估器, ~500行)
│   ├── quality_gate.py          # 滑动窗口质量熔断器 (~120行)
│   ├── resilience.py            # 熔断器 + 舱壁 + 重试策略 (~300行)
│   ├── sandbox.py               # 安全代码执行 (AST+子进程+哨兵, ~280行)
│   ├── solidifier.py            # 技能固化 + .trs 导出
│   ├── drift_detector.py        # 漂移检测 (哈希 + 语义)
│   ├── self_healer.py           # 自动 Prompt 优化
│   ├── prompt_lineage.py        # Prompt 版本控制
│   ├── context_cache.py         # Prompt 缓存优化
│   ├── task_queue.py            # 优先级队列 (有界, HIGH/NORMAL/LOW)
│   ├── cost_scheduler.py        # Token 套利路由
│   ├── sampling_manager.py      # 1% 采样门
│   ├── telemetry.py             # 事件广播
│   ├── jinja_env.py             # Jinja2 沙箱单例
│   ├── mcp_server.py            # MCP 协议服务器 (4个工具)
│   ├── output_sink.py           # 输出持久化 (file/duckdb/vector)
│   └── app.py                   # 主控制器
├── gateway/                     # I/O 网关 (9 模块)
│   ├── provider.py              # LLM 客户端 (SSRF防护, 指数退避)
│   ├── privacy.py               # PII 脱敏引擎 (5种模式, 可逆)
│   ├── file_gateway.py          # 本地文件流式读取 (路径遍历防护)
│   ├── batch_provider.py        # OpenAI Batch API (50% 成本)
│   ├── mcp_client.py            # MCP 客户端 (JSON-RPC 2.0)
│   ├── s3_gateway.py            # S3 兼容存储 (有close方法)
│   ├── sql_gateway.py           # SQL 数据库 (有close方法)
│   ├── duckdb_gateway.py        # DuckDB 数据库
│   ├── video_gateway.py         # 视频帧提取
│   └── audio_gateway.py         # 音频转录 (Whisper)
├── api/                         # FastAPI 后端
│   └── main.py                  # REST + WebSocket + SSE + 认证
├── web/                         # Next.js Cockpit UI
│   └── src/
│       ├── app/                 # Dashboard, Missions, Skills 页面
│       ├── components/          # UI 组件 (Button, Card, Badge, ...)
│       └── lib/                 # API 客户端 + WebSocket hook
├── tests/                       # 484 个测试 (32 个测试文件)
├── skills/library/              # 预设技能包
├── docs/                        # 设计文档 (19 个文件)
├── runfiles/                    # 用户任务蓝图 (YAML)
├── vault/                       # 固化技能存储 (.trs)
├── logs/                        # SQLite traces + 日志
├── output/                      # 输出结果 (JSONL)
├── Dockerfile                   # 后端容器
└── docker-compose.yml           # 全栈编排
```
