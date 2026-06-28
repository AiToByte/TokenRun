# TokenRun 开发者指南

---

## 1. 开发环境

### 1.1 环境要求

- Python 3.10+ (推荐 3.12)
- Node.js 18+ (前端开发)
- Git
- ruff (linting)

### 1.2 安装

```bash
# 克隆
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun

# 安装 (开发模式)
pip install -e ".[dev]"

# 安装 lint 工具
pip install ruff

# 安装前端依赖
cd web && npm install && cd ..
```

### 1.3 IDE 配置

**VS Code 推荐扩展:**
- Python (ms-python)
- Pylance
- Ruff
- Tailwind CSS IntelliSense

**PyCharm:**
- 启用 ruff 作为外部工具
- 配置 pytest 作为测试运行器

---

## 2. 项目结构

```
TokenRun/
├── core/              # 核心引擎 (Python)
│   ├── models.py      # Pydantic 协议定义
│   ├── runner.py      # Actor-Critic 循环
│   ├── orchestrator.py # DAG 调度器
│   └── ...
├── gateway/           # I/O 网关
│   ├── provider.py    # LLM 客户端
│   ├── privacy.py     # 隐私脱敏
│   └── ...
├── api/               # FastAPI 后端
│   └── main.py
├── web/               # Next.js 前端
│   └── src/
├── tests/             # 测试文件
├── docs/              # 文档
├── runfiles/          # 示例 Runfile
└── skills/library/    # 预设技能
```

---

## 3. 代码规范

### 3.1 Python 风格

- **类型提示:** 所有函数参数和返回值必须有类型提示
- **文档字符串:** 所有公开类和方法必须有 docstring
- **不可变性:** 优先使用 `model_copy(update={...})` 而非原地修改
- **文件大小:** 单文件不超过 800 行
- **函数大小:** 单函数不超过 50 行

### 3.2 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `ActorCriticLoop` |
| 函数/方法 | snake_case | `run_mass_production` |
| 常量 | UPPER_SNAKE | `MAX_RETRIES` |
| 私有方法 | _前缀 | `_internal_method` |
| 布尔变量 | is_/has_/can_ | `is_halted` |

### 3.3 Pydantic 模型

```python
# 所有模型使用 extra="forbid"
class MyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: float = 0.0
```

### 3.4 错误处理

```python
# ✅ 好: 显式处理
try:
    result = await provider.request(messages)
except LLMProviderError as exc:
    logger.error(f"LLM 请求失败: {exc}")
    raise

# ❌ 坏: 静默吞掉
try:
    result = await provider.request(messages)
except Exception:
    pass
```

---

## 4. 测试

### 4.1 测试框架

- **pytest** + **pytest-asyncio**
- **unittest.mock.AsyncMock** 用于 LLM 调用
- **MagicMock(spec=...)** 用于类型安全

### 4.2 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# 单个文件
python -m pytest tests/test_runner.py -v

# 单个测试
python -m pytest tests/test_runner.py::TestActorCriticLoop::test_first_attempt_passes -v

# 带覆盖率
python -m pytest tests/ --cov=core --cov=gateway --cov=api

# 只运行异步测试
python -m pytest tests/ -v -k "async"
```

### 4.3 测试模式

**AAA 模式 (Arrange-Act-Assert):**
```python
async def test_first_attempt_passes(self):
    # Arrange
    actor = MagicMock(spec=TaskActor)
    actor.generate = AsyncMock(return_value=_actor_response("OK"))
    critic = MagicMock(spec=TaskCritic)
    critic.evaluate = AsyncMock(return_value=_critic_response(passed=True))

    engine = ActorCriticLoop(actor=actor, critic=critic)
    node = _make_node(max_attempts=3)

    # Act
    result = await engine.run(node, "test data")

    # Assert
    assert result["status"] == "success"
    assert result["trace"].status == TaskStatus.COMPLETED
```

**Mock LLM 调用:**
```python
def _actor_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        prompt_tokens=100,
        completion_tokens=50,
        model_name="gpt-4o",
    )

def _critic_response(passed: bool, score: float = 0.9) -> EvaluationResult:
    return EvaluationResult(
        passed=passed,
        score=score,
        scores={"accuracy": score},
        critique=None if passed else "需要改进",
    )
```

### 4.4 测试覆盖要求

- 新功能: 100% 测试覆盖
- Bug 修复: 必须有回归测试
- 总体覆盖率: 80%+

---

## 5. Linting

### 5.1 使用 ruff

```bash
# 检查
ruff check core/ gateway/ api/ main.py

# 格式检查
ruff format --check core/ gateway/ api/ main.py

# 自动修复
ruff check --fix core/ gateway/ api/ main.py

# 自动格式化
ruff format core/ gateway/ api/ main.py
```

### 5.2 CI 检查

GitHub Actions 自动运行:
- Python 3.10, 3.11, 3.12, 3.13 测试矩阵
- ruff lint + format 检查
- 前端 build 检查

---

## 6. 提交规范

### 6.1 Conventional Commits

```
<type>(<scope>): <description>

[optional body]
```

**类型:**

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构 |
| `docs` | 文档 |
| `test` | 测试 |
| `chore` | 构建/工具 |
| `perf` | 性能优化 |

**示例:**
```
feat(runner): add consensus validation support
fix(privacy): correct label extraction from placeholder
docs(readme): update test count
test(eval_judge): add weight verification tests
```

### 6.2 分支策略

```bash
# 功能分支
git checkout -b feature/consensus-validation

# Bug 修复
git checkout -b fix/privacy-label-extraction

# 提交
git commit -m "feat(runner): add consensus validation support"

# 推送
git push origin feature/consensus-validation
```

---

## 7. 添加新功能

### 7.1 添加新的验证规则类型

1. 在 `core/runner.py` 的 `_run_programmatic_rules()` 中添加处理逻辑
2. 在 `core/models.py` 的 `ValidationRule` docstring 中记录新类型
3. 添加测试 `tests/test_runner.py`
4. 更新文档 `docs/components.md`

### 7.2 添加新的网关

1. 在 `gateway/` 目录创建新文件
2. 实现 `close()` 方法和 context manager
3. 在 `core/app.py` 的 `sense_resources()` 中添加协议支持
4. 添加测试 `tests/test_gateway.py`
5. 更新文档

### 7.3 添加新的评估维度

1. 在 `core/eval_judge.py` 中创建评估函数:
```python
async def my_evaluator(input_data: str, output: str) -> float:
    """自定义评估器"""
    score = 1.0
    # 评估逻辑...
    return score
```

2. 注册到 EvalJudge:
```python
judge.register_dimension(
    EvalDimension(name="my_dim", weight=1.0, evaluator=my_evaluator)
)
```

3. 添加测试

### 7.4 添加新的 API 端点

1. 在 `api/main.py` 中添加路由:
```python
@app.get("/my-endpoint")
async def my_endpoint():
    return {"result": "..."}
```

2. 添加请求/响应模型 (Pydantic)
3. 添加测试 `tests/test_api.py`
4. 更新 `docs/api-reference.md`

---

## 8. 调试

### 8.1 日志

```python
# 使用 print (项目约定，用于用户可见输出)
print(f"📋 蓝图: {runfile.name}")

# 使用 telemetry (用于 WebSocket 广播)
self.telemetry.emit("EVENT_TYPE", "system", {"key": "value"})
```

### 8.2 SQLite 调试

```bash
# 查看 traces
sqlite3 logs/tokenrun_traces.db "SELECT id, status, updated_at FROM task_traces"

# 查看隐私 vault
sqlite3 logs/tokenrun_traces.db "SELECT * FROM privacy_vault"

# 重置任务状态
sqlite3 logs/tokenrun_traces.db "UPDATE task_traces SET status='pending' WHERE id='...'"
```

### 8.3 前端调试

```bash
# 查看 WebSocket 连接
# 浏览器 DevTools → Network → WS

# 查看 API 请求
# 浏览器 DevTools → Network → Fetch/XHR
```

---

## 9. 常见问题

### Q: 如何添加新的 LLM 提供商?

A: 在 `gateway/provider.py` 的 `LLMProvider` 中，只要 base_url 指向 OpenAI-compatible API 即可，无需修改代码。

### Q: 如何跳过采样阶段?

A: 在 Runfile 中设置 `sampling.enabled: false`，或 CLI 使用 `--sample-only` 仅执行采样。

### Q: 如何使用本地模型?

A: 配置 `ACTOR_BASE_URL=http://localhost:11434/v1` 和 `ACTOR_MODEL=llama3` (Ollama)。

### Q: 如何限制并发数?

A: 修改 `core/app.py` 中 `TROrchestrator` 的 `concurrency` 参数。

### Q: 如何导出训练数据?

A: 任务完成后自动导出 (成功率 >90%)，或通过 API `/missions/{id}/export` 手动导出。

---

## 10. 相关文档

| 文档 | 内容 |
|------|------|
| [architecture.md](./architecture.md) | 技术架构、数据流、并发模型 |
| [components.md](./components.md) | 模块详解、依赖关系 |
| [data-flow.md](./data-flow.md) | 端到端数据链路 |
| [api-reference.md](./api-reference.md) | API 端点参考 |
| [user-manual.md](./user-manual.md) | 用户操作手册 |
| [deployment.md](./deployment.md) | 部署指南 |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 贡献指南 |
