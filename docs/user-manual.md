# TokenRun 用户手册

> 将 AI Token 转化为可靠、高质量的产出

---

## 目录

1. [快速上手](#1-快速上手)
2. [核心概念](#2-核心概念)
3. [Runfile 编写指南](#3-runfile-编写指南)
4. [CLI 使用](#4-cli-使用)
5. [API 使用](#5-api-使用)
6. [前端 Cockpit](#6-前端-cockpit)
7. [高级功能](#7-高级功能)
8. [故障排除](#8-故障排除)

---

## 1. 快速上手

### 1.1 安装

```bash
# 克隆仓库
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun

# 安装后端
pip install -e ".[dev]"

# 安装前端 (可选)
cd web && npm install && cd ..
```

### 1.2 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入 API Key
# 最简配置: 只需设置 OPENAI_API_KEY
```

**.env 示例:**
```bash
# 方式1: 共享 Key (Actor 和 Critic 使用同一个)
OPENAI_API_KEY=sk-your-key-here

# 方式2: 分别配置
ACTOR_API_KEY=sk-actor-key
ACTOR_MODEL=gpt-4o
CRITIC_API_KEY=sk-critic-key
CRITIC_MODEL=gpt-4o-mini

# 方式3: 使用国内提供商
ACTOR_BASE_URL=https://api.deepseek.com/v1
ACTOR_API_KEY=sk-deepseek-key
ACTOR_MODEL=deepseek-chat
```

### 1.3 运行

```bash
# CLI 模式 (默认测试任务)
python main.py

# CLI 模式 (自定义 Runfile)
python main.py runfiles/custom.yaml

# CLI 模式 (仅采样)
python main.py --sample-only

# API 模式
uvicorn api.main:app --reload

# 前端
cd web && npm run dev

# Docker 全栈
docker-compose up
```

### 1.4 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# 单个测试
python -m pytest tests/test_runner.py::TestActorCriticLoop::test_first_attempt_passes -v

# 带覆盖率
python -m pytest tests/ --cov=core --cov=gateway --cov=api

# Lint
ruff check core/ gateway/ api/ main.py
ruff format core/ gateway/ api/ main.py
```

---

## 2. 核心概念

### 2.1 Loop Engineering (循环工程)

传统 AI 调用: 输入 → LLM → 输出 (一次性，不可靠)

TokenRun 循环工程:
```
输入 → Actor(昂贵模型) → 输出
         ↑                    │
         │                    ▼
         │              Critic(廉价模型) → 评估
         │                    │
         │                    ├── 通过 → 最终输出
         │                    │
         └────────────────────┘ 不通过 → 反馈注入 → 重试
```

**三种策略:**

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| `feedback-driven` | 失败时注入反馈重试，通过即停止 | 通用场景 |
| `exhaustive` | 运行所有尝试，返回最高分 | 质量要求极高 |
| `once` | 单次尝试 | 简单任务 |

### 2.2 Runfile (任务蓝图)

Runfile 是 YAML 格式的声明式任务定义，描述:
- **做什么** (workflow)
- **怎么做** (loop_config)
- **用什么数据** (context)
- **在什么约束下** (security, sampling, governance)

### 2.3 Token Ledger (预算熔断)

每次 LLM 调用都经过 Ledger 记账:
- 记录 prompt_tokens 和 completion_tokens
- 实时计算 USD 成本
- 预算超限 → 立即熔断 (停止所有任务)

### 2.4 Fingerprint Locking (指纹锁定)

采样成功后锁定执行环境:
- model_id + prompt_hash + temperature + seed
- 后续执行前验证指纹一致性
- 检测到变化 → 要求重新采样

### 2.5 Sampling Gate (采样门)

全量执行前的小规模测试:
- 默认取 1% 数据
- 生成采样报告 (成功率、平均质量、预估总成本)
- 人工审批后才开始全量执行

---

## 3. Runfile 编写指南

### 3.1 基本结构

```yaml
version: "1.0"
name: "My_Task"

workflow:
  - id: "processor"
    name: "数据处理"
    actor_prompt_template: |
      请处理以下数据。
      输出 JSON: {"result": "..."}

      数据: {{ data }}
    loop_config:
      strategy: "feedback-driven"
      max_attempts: 3
      exit_criteria:
        - type: "json_schema"
          criteria: {"required": ["result"]}

governance:
  max_usd: 5.0
```

### 3.2 完整示例

```yaml
version: "1.0"
name: "Finance_Refinery"
metadata:
  description: "财务交易分类"
  author: "xiao"

# 数据源
context:
  - id: "transactions"
    uri: "local://data/finance"
    type: "local_file"

# 安全配置
security:
  masking_rules: ["emails", "api_keys", "phones"]
  local_sandbox: true

# 采样配置
sampling:
  enabled: true
  mode: "percentage"
  value: 0.01          # 1%
  auto_pause: true     # 采样后暂停等待审批

# 工作流
workflow:
  - id: "classifier"
    name: "交易分类"
    actor_prompt_template: |
      请将以下交易分类。
      输出 JSON: {"category": "...", "confidence": 0.0-1.0}

      {% if feedback %}
      上次输出有问题，请根据以下反馈改进:
      {{ feedback }}
      {% endif %}

      交易: {{ data }}

    # 动态模型路由
    model_tiers:
      - {model: "gpt-4o-mini", escalate_after: 2}
      - {model: "gpt-4o", escalate_after: 3}

    # 循环配置
    loop_config:
      strategy: "feedback-driven"
      max_attempts: 5
      min_score: 0.85
      retry_delay: 1
      score_weights:
        accuracy: 2.0
        format: 1.0
      exit_criteria:
        - type: "json_schema"
          criteria: {"required": ["category", "confidence"]}
        - type: "llm_eval"
          criteria: "分类必须符合会计逻辑"

    # 共识验证 (可选)
    loop_config:
      consensus_models: ["gpt-4o", "deepseek-chat"]
      consensus_threshold: 0.5

# 预算约束
governance:
  max_usd: 10.0
  max_loop_count: 10000

# 输出持久化 (可选)
output_sink:
  type: "file"
  output_dir: "output"
  suffix: ".jsonl"
```

### 3.3 验证规则类型

| 类型 | 成本 | 说明 | 示例 |
|------|------|------|------|
| `regex` | 免费 | 正则表达式匹配 | `criteria: "\\d+"` |
| `json_schema` | 免费 | JSON 结构校验 | `criteria: {"required": ["name"]}` |
| `code_eval` | 免费 | Python 代码测试 | `criteria: "assert len(output) > 10"` |
| `llm_eval` | 收费 | LLM 语义评估 | `criteria: "输出必须准确"` |

### 3.4 DAG 依赖

```yaml
workflow:
  - id: "classifier"
    name: "分类"
    depends_on: []
    actor_prompt_template: "..."

  - id: "summarizer"
    name: "摘要"
    depends_on: ["classifier"]
    actor_prompt_template: |
      请根据分类结果生成摘要。
      分类: {{ data }}

  - id: "validator"
    name: "验证"
    depends_on: ["summarizer"]
    actor_prompt_template: |
      请验证摘要质量。
      摘要: {{ data }}
```

---

## 4. CLI 使用

### 4.1 基本命令

```bash
# 使用默认测试任务
python main.py

# 使用自定义 Runfile
python main.py runfiles/my_task.yaml

# 仅执行采样阶段
python main.py --sample-only
```

### 4.2 输出示例

```
============================================================
  TokenRun — 工业级 AI 任务执行引擎
============================================================

📋 蓝图: Finance_Refinery (v1.0)
   工作流节点: 1
   预算上限: $10.00
   数据量: 100 条

────────────────────────────────────────────────────────
🔬 [采样阶段] 开始处理 1 个样本...
  样本 1: success
    预览: {"category": "金融", "confidence": 0.95}...

🔒 指纹已锁定: model=gpt-4o, prompt_hash=a1b2c3d4
   样本快照: e5f6g7h8

⏸️ 采样报告:
   成功率: 1/1
   平均质量: 0.92
   采样成本: $0.0023
   预估总成本: $0.23
   预估成功数: 92
   等待审批... (按 Enter 继续全量执行)

────────────────────────────────────────────────────────
🏭 [生产阶段] 开始全量处理 100 条数据...
  📦 节点 [交易分类] 处理 100 条数据...

✅ 完成！成功: 92/100
  账本: Actor: 15000 tokens ($0.075) | Critic: 5000 tokens ($0.005) | Total: $0.08

📦 技能已固化: vault/TR-SKILL-a1b2c3d4e5f6.trs

📊 [自动蒸馏] 成功率 92% > 90%，已导出训练数据: output/fine_tune.jsonl

🧹 隐私映射表已清空。任务结束。
```

---

## 5. API 使用

### 5.1 启动 API 服务

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### 5.2 核心端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/missions` | POST | 创建任务 |
| `/missions` | GET | 列出所有任务 |
| `/missions/{id}` | GET | 获取单个任务 |
| `/missions/{id}/approve` | POST | 审批任务 |
| `/missions/{id}/revise` | POST | 修改 Prompt |
| `/missions/{id}/traces` | GET | 获取执行 Trace |
| `/missions/{id}/lineage` | GET | Prompt 版本谱系 |
| `/missions/{id}/replay` | POST | 时间线回滚 |
| `/missions/{id}/export` | POST | 导出 Fine-tune 数据 |
| `/skills` | GET | 列出技能 |
| `/skills/{id}/run` | POST | 运行技能 |
| `/missions/{id}/events` | GET | SSE 实时事件 |
| `/ws` | WebSocket | 实时事件流 |

### 5.3 创建任务

```bash
curl -X POST http://localhost:8000/missions \
  -H "Content-Type: application/json" \
  -d '{
    "runfile_path": "runfiles/test_mission.yaml",
    "sample_only": false,
    "priority": "normal"
  }'
```

### 5.4 审批任务

```bash
# 批准
curl -X POST http://localhost:8000/missions/mission-abc123/approve \
  -H "Content-Type: application/json" \
  -d '{"action": "approve"}'

# 修改 Prompt 后批准
curl -X POST http://localhost:8000/missions/mission-abc123/approve \
  -H "Content-Type: application/json" \
  -d '{"action": "approve", "new_prompt": "改进后的 Prompt..."}'

# 中止
curl -X POST http://localhost:8000/missions/mission-abc123/approve \
  -H "Content-Type: application/json" \
  -d '{"action": "abort"}'
```

### 5.5 WebSocket 订阅

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onopen = () => {
  ws.send(JSON.stringify({
    action: 'subscribe',
    mission_id: 'mission-abc123',
    level: 2  // 1=进度, 2=详情, 3=完整trace
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data.type, data);
};
```

### 5.6 API 认证

```bash
# 设置 API Key
export TOKENRUN_API_KEY="my-secret-key"

# 带认证的请求
curl -H "Authorization: Bearer my-secret-key" \
  http://localhost:8000/missions
```

---

## 6. 前端 Cockpit

### 6.1 页面结构

**Dashboard (`/`):**
- 实时 ROI 图表 (双轴: 成本 + 处理量)
- 统计卡片 (处理量、成功率、总成本、单条成本)
- 近期任务列表
- 已固化技能网格

**Missions (`/missions`):**
- 任务创建表单
- 采样决策面板 (3 栏: Runfile 逻辑 + 样本结果 + 经济分析)
- 任务列表 (审批/中止按钮)
- 时间旅行调试 (滑块查看任意迭代)
- Prompt 编辑器 + 版本树可视化

**Skills (`/skills`):**
- 技能列表
- 一键重跑按钮

### 6.2 实时更新

前端通过 WebSocket 接收实时事件:
- 进度条更新
- 成本实时计算
- 质量告警通知
- 漂移检测告警

---

## 7. 高级功能

### 7.1 Ollama 本地模型审计

```yaml
workflow:
  - id: "processor"
    loop_config:
      critic_model: "ollama/llama3"
      critic_base_url: "http://localhost:11434/v1"  # 可选
```

### 7.2 多维评估 (EvalJudge)

```python
from core.eval_judge import (
    EvalJudge, EvalDimension,
    safety_evaluator, completeness_evaluator, code_quality_evaluator
)

judge = EvalJudge(
    dimensions=[
        EvalDimension(name="safety", weight=2.0, evaluator=safety_evaluator),
        EvalDimension(name="completeness", weight=1.0, evaluator=completeness_evaluator),
        EvalDimension(name="code_quality", weight=1.0, evaluator=code_quality_evaluator),
    ],
    threshold=0.7,
)

# 在 runner 中使用
engine = ActorCriticLoop(actor=actor, critic=critic, eval_judge=judge)
```

### 7.3 输出持久化

```yaml
output_sink:
  type: "file"        # file | duckdb | vector
  output_dir: "output"
  suffix: ".jsonl"
```

### 7.4 技能固化与复用

```bash
# 固化 (自动在任务完成后执行)
# 结果: vault/TR-SKILL-a1b2c3d4e5f6.trs

# 复用 (API)
curl -X POST http://localhost:8000/skills/TR-SKILL-a1b2c3d4e5f6/run

# 复用 (CLI)
python main.py --skill TR-SKILL-a1b2c3d4e5f6
```

### 7.5 MCP 集成

在 Claude Desktop 中使用 TokenRun 技能:

```json
{
  "mcpServers": {
    "tokenrun": {
      "command": "python",
      "args": ["-m", "core.mcp_server"],
      "env": {
        "TOKENRUN_VAULT_PATH": "/path/to/vault"
      }
    }
  }
}
```

### 7.6 时间线回滚

```bash
# 回滚到第 3 次迭代，使用新 Prompt 重放
curl -X POST http://localhost:8000/missions/mission-abc/replay \
  -H "Content-Type: application/json" \
  -d '{
    "iteration": 3,
    "new_prompt": "改进后的 Prompt...",
    "rollback": true
  }'
```

### 7.7 Fine-tune 数据导出

```bash
# 导出为 OpenAI 格式
curl -X POST http://localhost:8000/missions/mission-abc/export \
  -H "Content-Type: application/json" \
  -d '{"format": "openai", "min_score": 0.8}'

# 支持格式: openai, alpaca, sharegpt
```

---

## 8. 故障排除

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `未配置 API Key` | .env 未设置 | 设置 `OPENAI_API_KEY` |
| `预算已耗尽` | Ledger 熔断 | 增加 `governance.max_usd` |
| `指纹校验失败` | Prompt 被修改 | 重新采样确认质量 |
| `循环依赖` | DAG 配置错误 | 检查 `depends_on` |
| `路径遍历检测` | Runfile 路径不在 runfiles/ | 使用相对路径 |
| `SSRF blocked` | base_url 指向内网 | 使用公网地址 |
| `code_eval 超时` | 测试代码执行过慢 | 增加 sandbox timeout |
| `质量熔断` | 连续低分 | 检查 Prompt 或降低阈值 |
| `漂移检测告警` | 输出一致性下降 | 重新采样确认 |
| `前端无法连接` | CORS 或 API 未启动 | 检查 backend 状态 |
