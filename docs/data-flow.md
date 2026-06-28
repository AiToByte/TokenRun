# TokenRun 数据链路文档

---

## 1. 端到端数据流概览

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  数据源   │────→│  隐私    │────→│  Actor   │────→│  Critic  │────→│  输出    │
│  (输入)   │     │  脱敏    │     │  生成    │     │  评估    │     │  持久化  │
└──────────┘     └──────────┘     └──────────┘     └──────────┘     └──────────┘
  本地文件         [[TR_EMAIL]]     Jinja2渲染       JSON评分         SQLite
  SQL查询          [[TR_PHONE]]     LLM调用          多维评分         JSONL文件
  S3对象           [[TR_ID_CARD]]   反馈注入         共识投票         DuckDB
  MCP工具          [[TR_API_KEY]]   模型路由         加权计算         向量库
```

---

## 2. 数据源接入

### 2.1 本地文件 (local://)

```
Runfile.context:
  - id: "finance_data"
    uri: "local://data/transactions"
    type: "local_file"

处理流程:
  FileGateway("data/transactions")
    │
    ├── glob("**/*.*") 扫描文件
    │
    ├── 路径遍历检查: resolved.is_relative_to(base_resolved)
    │
    ├── 流式读取 (Generator):
    │   for file_path in sorted(glob):
    │       yield {
    │           "file_name": "transactions.txt",
    │           "relative_path": "transactions.txt",
    │           "content": "文件内容...",
    │           "size": 1024
    │       }
    │
    └── 过滤: [f["content"] for f in stream if f.get("content")]
```

### 2.2 MCP 工具 (mcp://)

```
Runfile.context:
  - id: "api_data"
    uri: "mcp://localhost:3001"
    type: "mcp_tool"
    description: "list_transactions"
    id: '{"user_id": "123"}'

处理流程:
  MCPClient("http://localhost:3001")
    │
    ├── initialize() — JSON-RPC 握手
    │
    ├── call_tool("list_transactions", {"user_id": "123"})
    │   │
    │   └── POST / JSON-RPC 2.0
    │       {
    │           "jsonrpc": "2.0",
    │           "method": "tools/call",
    │           "params": {
    │               "name": "list_transactions",
    │               "arguments": {"user_id": "123"}
    │           },
    │           "id": 1
    │       }
    │
    └── 解析响应: [c["text"] for c in result["content"] if c["type"] == "text"]
```

### 2.3 SQL 数据库

```
Runfile.context:
  - id: "db_data"
    uri: "sql://postgresql://user:pass@localhost/mydb"
    type: "sql_query"
    description: "SELECT * FROM transactions LIMIT 1000"

处理流程:
  SQLGateway("postgresql://user:pass@localhost/mydb")
    │
    ├── query("SELECT * FROM transactions LIMIT 1000")
    │   │
    │   └── SQLAlchemy text() + 参数化查询
    │
    └── stream_rows() — 每行序列化为 JSON 字符串
```

---

## 3. 隐私脱敏数据流

### 3.1 脱敏过程

```
原始输入:
  "联系人: alice@example.com, 电话: 13800138000, API Key: sk-abc123def456"
    │
    ├── PrivacyRedactor.mask()
    │   │
    │   ├── Phase 1: 扫描所有匹配
    │   │   EMAIL:   (12, 28, "EMAIL", "alice@example.com")
    │   │   PHONE:   (37, 48, "PHONE", "13800138000")
    │   │   API_KEY: (60, 80, "API_KEY", "sk-abc123def456")
    │   │
    │   ├── Phase 2: 按位置排序，解决重叠
    │   │
    │   ├── Phase 3: 生成占位符
    │   │   [[TR_EMAIL_1]]   → alice@example.com
    │   │   [[TR_PHONE_1]]   → 13800138000
    │   │   [[TR_API_KEY_1]] → sk-abc123def456
    │   │
    │   └── Phase 4: 替换 (反向保持位置)
    │
    └── 输出:
        "联系人: [[TR_EMAIL_1]], 电话: [[TR_PHONE_1]], API Key: [[TR_API_KEY_1]]"
```

### 3.2 内存映射

```python
_vault = {
    "[[TR_EMAIL_1]]": "alice@example.com",
    "[[TR_PHONE_1]]": "13800138000",
    "[[TR_API_KEY_1]]": "sk-abc123def456",
}

_reverse_vault = {
    "alice@example.com": "[[TR_EMAIL_1]]",
    "13800138000": "[[TR_PHONE_1]]",
    "sk-abc123def456": "[[TR_API_KEY_1]]",
}
```

### 3.3 反脱敏过程

```
LLM 输出:
  "分类结果: 金融交易, 联系 [[TR_EMAIL_1]]"
    │
    ├── PrivacyRedactor.unmask()
    │   │
    │   ├── 按占位符长度降序排序 (避免部分替换)
    │   │
    │   └── 逐一替换:
    │       "[[TR_EMAIL_1]]" → "alice@example.com"
    │
    └── 输出:
        "分类结果: 金融交易, 联系 alice@example.com"
```

### 3.4 持久化保护

```
trace 存储 (SQLite):
  input_payload: "联系人: [[TR_EMAIL_1]], 电话: [[TR_PHONE_1]]"  ← 脱敏后
  output_content: "联系人: [[TR_EMAIL_1]], 电话: [[TR_PHONE_1]]"  ← 脱敏后

  → SQLite 中不包含原始 PII
```

---

## 4. Actor 生成数据流

### 4.1 Jinja2 模板渲染

```
模板:
  "请将以下文本分类为类别。
   输出 JSON: {\"category\": \"...\", \"confidence\": 0.0-1.0}

   {% if feedback %}
   上次输出有问题，请根据以下反馈改进:
   {{ feedback }}
   {% endif %}

   文本: {{ data }}"

变量:
  data = "联系人: [[TR_EMAIL_1]], 电话: [[TR_PHONE_1]]"
  feedback = "输出格式不符合 JSON 规范"

渲染后:
  "请将以下文本分类为类别。
   输出 JSON: {\"category\": \"...\", \"confidence\": 0.0-1.0}

   上次输出有问题，请根据以下反馈改进:
   输出格式不符合 JSON 规范

   文本: 联系人: [[TR_EMAIL_1]], 电话: [[TR_PHONE_1]]"
```

### 4.2 LLM API 调用

```
LLMProvider.request():
    │
    ├── 构建 payload:
    │   {
    │       "model": "gpt-4o",
    │       "messages": [
    │           {"role": "system", "content": "..."},
    │           {"role": "user", "content": "渲染后的 Prompt"}
    │       ],
    │       "temperature": 0.1,
    │       "response_format": {"type": "json_object"}  // 可选
    │   }
    │
    ├── HTTP POST {base_url}/chat/completions
    │   Headers: Authorization: Bearer {api_key}
    │
    ├── 重试逻辑:
    │   for attempt in range(max_retries):
    │       if status_code in {429, 500, 502, 503, 504}:
    │           delay = 2^attempt (或 Retry-After)
    │           await asyncio.sleep(delay)
    │           continue
    │       if status_code >= 400:
    │           raise LLMProviderError
    │
    └── 返回 LLMResponse:
        {
            "content": "{\"category\": \"金融\", \"confidence\": 0.95}",
            "prompt_tokens": 150,
            "completion_tokens": 30,
            "model_name": "gpt-4o"
        }
```

### 4.3 动态模型路由

```
model_tiers:
  - {model: "gpt-4o-mini", escalate_after: 2}
  - {model: "gpt-4o", escalate_after: 3}

执行逻辑:
  attempt 1-2: 使用 gpt-4o-mini (便宜)
  attempt 3-5: 使用 gpt-4o (贵)
  attempt 6+:  使用 gpt-4o (最后一档)

  每次失败后检查是否需要升级:
    cumulative = 0
    for tier in tiers:
        cumulative += tier.escalate_after
        if attempt <= cumulative:
            return providers[tier.model]
```

---

## 5. Critic 评估数据流

### 5.1 程序化验证 (无 LLM 成本)

```
ValidationRule.type = "regex":
    pattern = r'\{"category":\s*"[^"]+",\s*"confidence":\s*[0-9.]+\}'
    matched = re.search(pattern, output)
    score = 1.0 if matched else 0.0

ValidationRule.type = "json_schema":
    criteria = {"required": ["category", "confidence"]}
    parsed = json.loads(output)
    missing = [f for f in required if f not in parsed]
    score = 1.0 if len(missing) == 0 else 0.0

ValidationRule.type = "code_eval":
    test_code = """
    import json
    data = json.loads(output)
    assert "category" in data
    assert 0 <= data["confidence"] <= 1
    """
    result = SandboxExecutor.execute_python(test_code, variables={"output": output})
    score = result["score"]
```

### 5.2 LLM 评估 (Critic)

```
TaskCritic.evaluate():
    │
    ├── 构建评估 Prompt:
    │   "请评估以下输出的质量。
    │    任务: {task_name}
    │    输入: {input_data}
    │    输出: {output_content}
    │
    │    评估规则:
    │    1. 输出必须是有效 JSON
    │    2. 必须包含 category 和 confidence 字段
    │    3. confidence 值在 0-1 之间
    │
    │    请以 JSON 格式返回评估结果:
    │    {\"passed\": bool, \"score\": float, \"scores\": {...}, \"critique\": str}"
    │
    ├── LLMProvider.request(response_format={"type": "json_object"})
    │
    └── 解析 JSON → EvaluationResult
```

### 5.3 多维评估 (EvalJudge)

```
EvalJudge.evaluate():
    │
    ├── 并发执行所有维度:
    │   await asyncio.gather(
    │       safety_evaluator(input, output),
    │       code_quality_evaluator(input, output),
    │       completeness_evaluator(input, output),
    │       coherence_evaluator(input, output),
    │       correctness_evaluator(input, output, llm_provider),
    │   )
    │
    ├── 收集评分:
    │   scores = {
    │       "safety": 1.0,
    │       "code_quality": 0.8,
    │       "completeness": 0.7,
    │       "coherence": 0.9,
    │       "correctness": 0.85
    │   }
    │
    ├── 加权计算:
    │   weighted_score = Σ(score_i × weight_i) / Σ(weight_i)
    │   = (1.0×2.0 + 0.8×1.0 + 0.7×1.0 + 0.9×1.0 + 0.85×1.0) / (2.0+1.0+1.0+1.0+1.0)
    │   = 5.25 / 6.0 = 0.875
    │
    └── 判断: passed = weighted_score >= threshold (0.7)
```

### 5.4 共识验证

```
consensus_models: ["gpt-4o", "gpt-4o-mini", "deepseek-chat"]

执行流程:
  1. 主 Critic 评估 → primary_result
  2. 并发调用共识模型:
     await asyncio.gather(
         consensus_critic("gpt-4o").evaluate(...),
         consensus_critic("gpt-4o-mini").evaluate(...),
         consensus_critic("deepseek-chat").evaluate(...),
     )
  3. 多数投票:
     votes = [primary_result.passed, True, True, False]
     pass_count = 3, total = 4
     consensus_passed = (3/4) >= 0.5 = True
  4. 合并评分: 取每个维度的最高分
```

---

## 6. 持久化数据流

### 6.1 每次迭代持久化

```
ActorCriticLoop.run():
    │
    ├── 每次迭代后:
    │   await persistence.async_save_trace(
    │       unit_id = "summarizer:a1b2c3d4",  # node_id:input_hash
    │       input_hash = "a1b2c3d4e5f67890",
    │       status = "completed" if passed else "running",
    │       trace = {"iterations": [
    │           {
    │               "iteration": 1,
    │               "output": "...",
    │               "passed": true,
    │               "score": 0.92,
    │               "scores": {"accuracy": 0.95, "format": 0.89},
    │               "critique": null
    │           }
    │       ]},
    │       output = "最终输出..." if passed else ""
    │   )
    │
    └── 异步包装: asyncio.to_thread(self.save_trace, ...)
        → 不阻塞事件循环
```

### 6.2 SQLite 数据结构

```sql
-- 任务执行 trace
CREATE TABLE task_traces (
    id TEXT PRIMARY KEY,           -- "summarizer:a1b2c3d4"
    input_hash TEXT,               -- SHA-256[:16]
    status TEXT,                   -- pending/running/completed/failed
    trace_data TEXT,               -- JSON: {"iterations": [...]}
    final_output TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 隐私 vault (PersistentRedactor)
CREATE TABLE privacy_vault (
    task_id TEXT NOT NULL,
    placeholder TEXT NOT NULL,     -- "[[TR_EMAIL_1]]"
    original_value TEXT NOT NULL,  -- "alice@example.com"
    label TEXT NOT NULL,           -- "EMAIL"
    PRIMARY KEY (task_id, placeholder)
);
```

---

## 7. 输出持久化数据流

### 7.1 OutputSink

```
Runfile.output_sink:
  type: "file"
  output_dir: "output"
  suffix: ".jsonl"

处理流程:
  OutputSink.write(results):
    │
    ├── FileSink:
    │   output/output_{mission_id}.jsonl
    │   每行一个 JSON: {"input": "...", "output": "...", "score": 0.92, ...}
    │
    ├── DuckDBSink:
    │   conn.execute("INSERT INTO results VALUES (?, ?, ?, ...)")
    │
    └── VectorSink:
    │   collection.add(
    │       documents=[output_text],
    │       metadatas=[{score, task_id, ...}],
    │       ids=[unique_id]
    │   )
```

### 7.2 技能固化输出

```
SkillSolidifier.distill():
    │
    ├── 提取黄金样本 (Top-5 by score):
    │   golden_samples = sorted(successful, key=lambda x: score, reverse=True)[:5]
    │
    ├── 计算统计:
    │   success_rate = success_count / total_count
    │   average_retries = sum(retries) / total_count
    │
    ├── 生成 skill_id:
    │   skill_id = "TR-SKILL-" + SHA256(prompt_template)[:12]
    │
    └── 写入 .trs JSON:
        vault/TR-SKILL-a1b2c3d4e5f6.trs
```

### 7.3 Fine-tune 数据导出

```
SkillSolidifier.export_fine_tune():
    │
    ├── 过滤: score >= min_score 的成功 trace
    │
    ├── 格式化:
    │   openai:  {"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}
    │   alpaca:  {"instruction": ..., "input": "", "output": ...}
    │   sharegpt: {"conversations": [{"from": "human", ...}, {"from": "gpt", ...}]}
    │
    └── 写入 JSONL:
        output/{mission_id}_fine_tune.jsonl
```

---

## 8. 实时事件数据流

### 8.1 TelemetryManager

```
事件产生:
  orchestrator._bounded_execute():
    │
    ├── STATUS_UPDATE: phase 变化时
    ├── TRACE_EVENT: 每次迭代完成时
    ├── QUALITY_HALT: 质量熔断时
    ├── DRIFT_HALT: 漂移检测触发时
    ├── HEALING_SUGGESTION: 自愈建议时
    └── SPOT_CHECK: 随机抽样时

事件分发:
  TelemetryManager.emit(event):
    │
    ├── _event_log.append(event)  # 内存日志
    │
    └── for handler in _handlers:
        handler(event)  # 回调处理器
```

### 8.2 WebSocket 事件流

```
客户端连接 → WS /ws
    │
    ├── 订阅: {"action": "subscribe", "mission_id": "...", "level": 2}
    │
    ├── 服务端广播:
    │   _broadcast({"type": "TRACE_EVENT", ...})
    │       │
    │       └── for ws, info in _ws_clients.items():
    │           if info["mission_id"] == mission_id:
    │               if event_level <= info["level"]:
    │                   await ws.send_json(event)
    │
    └── 自动推断事件级别:
        L1: STATUS_UPDATE, ERROR
        L2: QUALITY_HALT, DRIFT_*, HEALING_*
        L3: TRACE_EVENT
```

### 8.3 SSE 事件流

```
客户端连接 → GET /missions/{id}/events
    │
    ├── StreamingResponse(media_type="text/event-stream")
    │
    └── while mission_active:
        event = await _mission_events[mission_id].wait()
        yield f"data: {json.dumps(event)}\n\n"
```

---

## 9. 完整数据流示例

### 9.1 个人财务审计场景

```
输入: 财务交易记录 (含邮箱、电话、API Key)

Step 1: 数据感知
  FileGateway("data/finance").stream_files()
  → ["交易1: 联系 alice@...", "交易2: ..."]

Step 2: 隐私脱敏
  PrivacyRedactor.mask("交易1: 联系 alice@example.com")
  → "交易1: 联系 [[TR_EMAIL_1]]"

Step 3: Actor 生成
  Jinja2渲染: "请分类: 交易1: 联系 [[TR_EMAIL_1]]"
  LLM响应: {"category": "金融", "confidence": 0.95}

Step 4: 反脱敏
  → {"category": "金融", "confidence": 0.95}

Step 5: 程序化验证
  json_schema: required=["category", "confidence"] → passed

Step 6: LLM 评估
  Critic: {"passed": true, "score": 0.92}

Step 7: 持久化
  SQLite: status="completed", trace={iterations: [...]}

Step 8: 输出
  OutputSink: output/mission-abc123.jsonl
  SkillSolidifier: vault/TR-SKILL-a1b2c3d4e5f6.trs
```
