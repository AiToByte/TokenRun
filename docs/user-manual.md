# TokenRun 用户手册

> 将 AI Token 转化为可靠、高质量的产出

---

## 目录

1. [快速上手](#快速上手)
2. [核心概念](#核心概念)
3. [Runfile 编写指南](#runfile-编写指南)
4. [CLI 使用](#cli-使用)
5. [API 使用](#api-使用)
6. [前端 Cockpit](#前端-cockpit)
7. [高级功能](#高级功能)
8. [故障排除](#故障排除)

---

## 快速上手

### 环境要求

- Python 3.10+
- Node.js 18+（前端可选）
- OpenAI 兼容 API Key

### 安装

```bash
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun
pip install -e ".[dev]"
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key：
# OPENAI_API_KEY=sk-your-key-here
```

### 5 分钟体验

```bash
# 运行默认测试任务
python main.py

# 仅运行采样阶段（验证质量）
python main.py --sample-only

# 使用自定义 Runfile
python main.py runfiles/custom.yaml
```

---

## 核心概念

### 循环工程 (Loop Engineering)

TokenRun 的核心机制 — Actor-Critic 反馈循环：

```
输入 → 隐私脱敏 → Actor（贵模型生成）
                        ↓
                  Critic（便宜模型审计）
                        ↓
               ┌── 通过 → 最终输出
               └── 失败 → 反馈注入 → Actor 重试
```

**三种策略**：
- `feedback-driven`：带反馈重试，直到通过或达到最大次数
- `exhaustive`：运行所有尝试，选择最高分结果
- `once`：单次执行，不重试

### Runfile（任务蓝图）

YAML 格式的声明式任务定义，描述：
- **做什么**（workflow 节点）
- **怎么做**（loop_config 策略）
- **用什么数据**（context 资源）
- **什么约束**（security、governance）

### Token 账本 (Ledger)

实时追踪每次 LLM 调用的成本，支持：
- 预算上限自动熔断
- ROI 实时计算
- Actor/Critic 分别计费

### 指纹锁定 (Fingerprint)

锁定 `model_id + prompt_hash + temperature + seed`，防止云端模型静默漂移。

---

## Runfile 编写指南

### 基础结构

```yaml
name: "我的任务"
version: "1.0"

workflow:
  - id: "task-1"
    name: "任务名称"
    actor_prompt_template: |
      你的 Prompt 模板，使用 {{ data }} 引用输入数据。
    loop_config:
      strategy: "feedback-driven"
      max_attempts: 5
      min_score: 0.8
      exit_criteria:
        - type: "json_schema"
          criteria:
            required: ["field1", "field2"]
```

### 完整示例

```yaml
name: "Finance_Refinery"
version: "1.0"

# 工作流节点（支持 DAG 依赖）
workflow:
  - id: "classifier"
    name: "交易分类"
    actor_prompt_template: |
      将以下交易分类到对应类别。
      输出 JSON: {"category": "...", "confidence": 0.0-1.0}

      交易: {{ data }}

    # 动态模型路由：失败后自动升级模型
    model_tiers:
      - { model: "gpt-4o-mini", escalate_after: 2 }
      - { model: "gpt-4o", escalate_after: 3 }

    loop_config:
      strategy: "feedback-driven"
      max_attempts: 5
      min_score: 0.85
      retry_delay: 1

      # 多维度评分权重
      score_weights:
        accuracy: 2.0
        format: 1.0

      # 验证规则（程序化 + LLM）
      exit_criteria:
        - type: "json_schema"
          criteria:
            required: ["category", "confidence"]
        - type: "regex"
          criteria: '"category"\s*:\s*"[^"]+"'
        - type: "llm_eval"
          criteria: "分类必须符合会计逻辑"

      # 共识审计（多模型投票）
      consensus_models: ["gpt-4o", "gpt-4o-mini"]
      consensus_threshold: 0.6

# 安全配置
security:
  masking_rules: ["emails", "api_keys", "phones"]
  local_sandbox: true

# 采样配置
sampling:
  enabled: true
  mode: "percentage"
  value: 0.01
  auto_pause: true

# 治理约束
governance:
  max_usd: 5.0
  max_loop_count: 10000

# 输出持久化（可选）
output_sink:
  type: "file"
  output_dir: "output"
  suffix: ".jsonl"
```

### 验证规则类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `regex` | 正则表达式匹配 | `'"category"\s*:\s*"[^"]+"'` |
| `json_schema` | JSON 结构校验 | `{"required": ["field1"]}` |
| `code_eval` | Python 代码执行 | `assert "category" in json.loads(output)` |
| `llm_eval` | LLM 自然语言评估 | `"输出必须符合会计逻辑"` |

### DAG 依赖

```yaml
workflow:
  - id: "step1"
    name: "第一步"
    actor_prompt_template: "..."

  - id: "step2"
    name: "第二步"
    depends_on: ["step1"]  # 依赖 step1 的输出
    actor_prompt_template: |
      基于以下分析结果继续处理: {{ data }}
```

---

## CLI 使用

```bash
# 默认测试任务
python main.py

# 自定义 Runfile
python main.py path/to/runfile.yaml

# 仅采样（验证质量）
python main.py --sample-only

# 指定环境变量
OPENAI_API_KEY=sk-xxx python main.py runfile.yaml
```

---

## API 使用

### 启动服务

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

### 核心端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/missions` | 创建任务 |
| `GET` | `/missions` | 列出所有任务 |
| `GET` | `/missions/{id}` | 查询任务状态 |
| `POST` | `/missions/{id}/approve` | 审批/中止任务 |
| `POST` | `/missions/{id}/replay` | 重放（支持时间线回滚） |
| `GET` | `/missions/{id}/traces` | 获取执行轨迹 |
| `GET` | `/skills` | 列出已固化技能 |
| `POST` | `/skills/{id}/run` | 运行技能 |
| `WS` | `/ws` | WebSocket 实时事件流 |

### 创建任务

```bash
curl -X POST http://localhost:8000/missions \
  -H "Content-Type: application/json" \
  -d '{"runfile_path": "runfiles/test_mission.yaml"}'
```

### WebSocket 订阅

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');
ws.send(JSON.stringify({
  action: 'subscribe',
  mission_id: 'your-mission-id',
  level: 2  // 1=进度, 2=节点, 3=完整轨迹
}));
ws.onmessage = (event) => console.log(JSON.parse(event.data));
```

### 重放与时间线回滚

```bash
# 普通重放（带新 Prompt 继续跑）
curl -X POST "http://localhost:8000/missions/{id}/replay?new_prompt=改进后的Prompt"

# 时间线回滚（重置已完成任务，重新执行）
curl -X POST "http://localhost:8000/missions/{id}/replay?rollback=true&new_prompt=新Prompt"
```

---

## 前端 Cockpit

启动前端：

```bash
cd web && npm install && npm run dev
# 访问 http://localhost:3000
```

### 页面功能

| 页面 | 功能 |
|------|------|
| **Dashboard** | 实时 ROI 价值曲线、任务概览 |
| **Missions** | 任务创建、审批、版本进化树、时间旅行调试 |
| **Skills** | 已固化技能列表、一键运行 |

---

## 高级功能

### 本地模型审计（Ollama）

```yaml
loop_config:
  critic_model: "ollama/llama3"  # 零成本本地审计
  # critic_base_url: "http://localhost:11434/v1"  # 自定义地址
```

### 多维度评估 (EvalJudge)

```python
from core.eval_judge import EvalJudge, EvalDimension, safety_evaluator, completeness_evaluator

judge = EvalJudge(
    dimensions=[
        EvalDimension("safety", weight=0.4, evaluator=safety_evaluator),
        EvalDimension("completeness", weight=0.6, evaluator=completeness_evaluator),
    ],
    threshold=0.7,
)

# 注入到 runner
runner = ActorCriticLoop(actor=actor, critic=critic, eval_judge=judge)
```

### 输出持久化

```yaml
# 文件输出
output_sink:
  type: "file"
  output_dir: "output"

# DuckDB 结构化存储
output_sink:
  type: "duckdb"
  db_path: "results.db"
  table_name: "my_results"

# 向量数据库（Chroma）
output_sink:
  type: "vector"
  collection_name: "my_collection"
  backend: "chroma"
```

### 技能固化

任务完成后，最优 Prompt 和黄金样本自动打包为 `.trs` 文件：

```python
from core.solidifier import SkillSolidifier

solidifier = SkillSolidifier(vault_path="vault")
skill_id = solidifier.distill(
    task_name="classifier",
    traces=traces,
    prompt_template=prompt_template,
)
```

### MCP 服务器

将技能暴露为 MCP 工具：

```bash
python -m core.mcp_server
```

在 Claude Desktop 中配置后，可直接调用已固化技能。

---

## 故障排除

### 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| `BudgetExceededError` | 预算耗尽 | 增加 `governance.max_usd` 或检查成本 |
| 质量熔断触发 | 连续低分 | 检查 Prompt 质量或降低 `quality_threshold` |
| 指纹校验失败 | 配置变更 | 重新采样确认质量 |
| 漂移检测告警 | 模型输出变化 | 检查 `drift_action` 配置 |
| 沙箱执行超时 | 代码死循环 | 检查 `code_eval` 规则 |

### 日志查看

```bash
# API 日志
uvicorn api.main:app --log-level debug

# 测试详细输出
python -m pytest tests/ -v --tb=long
```

### 重置任务状态

```python
from core.persistence import TaskPersistence

persistence = TaskPersistence()
persistence.reset_to_pending("task-id")  # 重置单个
persistence.reset_multiple(["id1", "id2"])  # 批量重置
```
