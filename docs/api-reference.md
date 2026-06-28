# TokenRun API 参考手册

---

## 概述

TokenRun API 是基于 FastAPI 的 RESTful 后端，提供任务管理、技能执行、实时监控等功能。

**基础 URL:** `http://localhost:8000`
**认证:** 可选 (通过 `TOKENRUN_API_KEY` 环境变量启用)
**协议:** REST + WebSocket + SSE

---

## 认证

当 `TOKENRUN_API_KEY` 环境变量设置时，所有端点 (除白名单外) 需要 Bearer token 认证。

**请求头:**
```
Authorization: Bearer {TOKENRUN_API_KEY}
```

**白名单端点 (无需认证):**
- `GET /health`
- `GET /docs`
- `GET /openapi.json`

---

## REST 端点

### 1. 健康检查

```
GET /health
```

**响应:**
```json
{
    "status": "ok",
    "version": "0.1.0"
}
```

---

### 2. 任务管理

#### 2.1 创建任务

```
POST /missions
```

**请求体:**
```json
{
    "runfile_path": "runfiles/test_mission.yaml",
    "sample_only": false,
    "priority": "normal"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `runfile_path` | string | 是 | Runfile 路径 (限制在 runfiles/ 目录) |
| `sample_only` | boolean | 否 | 仅执行采样阶段 |
| `priority` | string | 否 | `high` / `normal` / `low` (low = Batch API) |

**响应:**
```json
{
    "mission_id": "mission-a1b2c3d4",
    "status": "pending",
    "phase": "INIT",
    "progress": 0.0,
    "cost_usd": 0.0,
    "success_count": 0,
    "total_count": 0
}
```

**错误:**
- `403` — 路径遍历检测
- `404` — Runfile 不存在

---

#### 2.2 列出所有任务

```
GET /missions
```

**响应:**
```json
[
    {
        "mission_id": "mission-a1b2c3d4",
        "status": "running",
        "phase": "FULL_PRODUCTION",
        "progress": 0.45,
        "cost_usd": 0.12,
        "success_count": 45,
        "total_count": 100
    }
]
```

---

#### 2.3 获取单个任务

```
GET /missions/{mission_id}
```

**响应:** 同上单个任务对象。

**错误:** `404` — 任务不存在

---

#### 2.4 审批任务

```
POST /missions/{mission_id}/approve
```

**请求体:**
```json
{
    "action": "approve",
    "new_prompt": null
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `action` | string | `approve` (批准) / `abort` (中止) |
| `new_prompt` | string | 可选: 修改 Prompt 后批准 |

**响应:**
```json
{
    "status": "approved",
    "message": "任务已批准，继续全量执行"
}
```

---

#### 2.5 修改 Prompt

```
POST /missions/{mission_id}/revise
```

**请求体:**
```json
{
    "new_prompt": "请将以下文本分类为类别...\n\n{{ data }}",
    "change_log": "优化分类指令"
}
```

**响应:**
```json
{
    "status": "revised",
    "message": "Prompt 已更新，重新采样中"
}
```

---

#### 2.6 获取执行 Trace

```
GET /missions/{mission_id}/traces
```

**响应:**
```json
{
    "mission_id": "mission-a1b2c3d4",
    "traces": [
        {
            "task_id": "summarizer:abc123",
            "status": "completed",
            "iterations": [
                {
                    "iteration_index": 1,
                    "output_content": "...",
                    "evaluation": {
                        "passed": true,
                        "score": 0.92,
                        "scores": {"accuracy": 0.95, "format": 0.89}
                    }
                }
            ],
            "final_output": "..."
        }
    ]
}
```

---

#### 2.7 获取 Prompt 版本谱系

```
GET /missions/{mission_id}/lineage
```

**响应:**
```json
{
    "versions": [
        {
            "version_id": "v1",
            "hash": "a1b2c3",
            "template": "请分类...",
            "parent_id": null,
            "change_log": "初始版本",
            "stats": {"pass_rate": 0.85, "total_processed": 100}
        }
    ]
}
```

---

#### 2.8 获取版本树 (可视化)

```
GET /missions/{mission_id}/version-tree
```

**响应:**
```json
{
    "nodes": [
        {"id": "v1", "label": "v1 (85%)", "pass_rate": 0.85}
    ],
    "edges": [
        {"from": "v1", "to": "v2"}
    ]
}
```

---

#### 2.9 重放 (时间线回滚)

```
POST /missions/{mission_id}/replay
```

**请求体:**
```json
{
    "iteration": 3,
    "new_prompt": "改进后的 Prompt...",
    "rollback": true
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `iteration` | int | 从第几次迭代开始重放 |
| `new_prompt` | string | 可选: 使用新 Prompt |
| `rollback` | boolean | 是否回滚已完成的任务 |

**响应:**
```json
{
    "status": "replay_started",
    "reset_count": 50
}
```

---

#### 2.10 应用自愈建议

```
POST /missions/{mission_id}/apply-healing
```

**响应:**
```json
{
    "status": "healing_applied",
    "new_prompt": "优化后的 Prompt..."
}
```

---

#### 2.11 导出 Fine-tune 数据

```
POST /missions/{mission_id}/export
```

**请求体:**
```json
{
    "format": "openai",
    "min_score": 0.8
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `format` | string | `openai` / `alpaca` / `sharegpt` |
| `min_score` | float | 最低评分阈值 |

**响应:**
```json
{
    "file_path": "output/mission-a1b2c3d4_fine_tune.jsonl",
    "item_count": 45
}
```

---

### 3. 技能管理

#### 3.1 列出技能

```
GET /skills
```

**响应:**
```json
[
    {
        "skill_id": "TR-SKILL-a1b2c3d4e5f6",
        "name": "Finance_Refinery",
        "created_at": "2026-06-28T12:00:00"
    }
]
```

---

#### 3.2 运行技能

```
POST /skills/{skill_id}/run
```

**响应:**
```json
{
    "mission_id": "mission-e5f6g7h8",
    "status": "pending",
    "message": "技能任务已创建"
}
```

---

### 4. SSE 实时事件

```
GET /missions/{mission_id}/events
```

**响应格式 (Server-Sent Events):**
```
data: {"type": "STATUS_UPDATE", "phase": "SAMPLING", "progress": 0.1}

data: {"type": "TRACE_EVENT", "task_id": "summarizer:abc", "iteration": 1, "passed": true, "score": 0.92}

data: {"type": "QUALITY_HALT", "message": "连续 5 个任务评分低于 0.6"}
```

---

## WebSocket

### 连接

```
ws://localhost:8000/ws
```

### 订阅

**发送:**
```json
{
    "action": "subscribe",
    "mission_id": "mission-a1b2c3d4",
    "level": 2
}
```

| level | 说明 | 事件类型 |
|-------|------|----------|
| 1 | 进度 | STATUS_UPDATE, ERROR |
| 2 | 节点详情 | + QUALITY_HALT, DRIFT_*, HEALING_* |
| 3 | 完整 Trace | + TRACE_EVENT |

### 取消订阅

**发送:**
```json
{
    "action": "unsubscribe",
    "mission_id": "mission-a1b2c3d4"
}
```

### 接收事件

**STATUS_UPDATE:**
```json
{
    "type": "STATUS_UPDATE",
    "mission_id": "mission-a1b2c3d4",
    "phase": "FULL_PRODUCTION",
    "progress": 0.45,
    "cost_usd": 0.12
}
```

**TRACE_EVENT:**
```json
{
    "type": "TRACE_EVENT",
    "mission_id": "mission-a1b2c3d4",
    "task_id": "summarizer:abc123",
    "node_id": "summarizer",
    "iteration": 1,
    "passed": true,
    "score": 0.92,
    "output_preview": "分类: 金融交易..."
}
```

**QUALITY_HALT:**
```json
{
    "type": "QUALITY_HALT",
    "mission_id": "mission-a1b2c3d4",
    "message": "连续 5 个任务评分低于 0.6",
    "recent_scores": [0.3, 0.4, 0.5, 0.3, 0.2]
}
```

---

## MCP 工具

TokenRun 通过 MCP 协议暴露 4 个工具:

| 工具 | 参数 | 说明 |
|------|------|------|
| `list_skills` | — | 列出所有 .trs 技能 |
| `get_skill` | `skill_id` | 获取技能详情 |
| `run_skill` | `skill_id, input_data` | 返回执行配置 |
| `create_mission` | `runfile_path, priority` | 创建任务 |

**MCP 使用示例 (Claude Desktop):**
```json
{
    "mcpServers": {
        "tokenrun": {
            "command": "python",
            "args": ["-m", "core.mcp_server"]
        }
    }
}
```
