# 贡献指南

感谢你对 TokenRun 的关注！本文档将帮助你快速参与项目开发。

---

## 目录

- [开发环境](#开发环境)
- [项目结构](#项目结构)
- [开发流程](#开发流程)
- [代码规范](#代码规范)
- [测试要求](#测试要求)
- [提交规范](#提交规范)
- [Pull Request 流程](#pull-request-流程)

---

## 开发环境

### 前置要求

- Python 3.10+
- Node.js 18+（前端开发）
- Git

### 安装

```bash
# 克隆仓库
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun

# 安装后端依赖
pip install -e ".[dev]"

# 安装前端依赖（可选）
cd web && npm install && cd ..

# 安装 lint 工具
pip install ruff
```

### 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的 API Key
```

---

## 项目结构

```
TokenRun/
├── core/              # 核心引擎（Python）
│   ├── models.py      # Pydantic 协议定义
│   ├── runner.py      # Actor-Critic 循环
│   ├── orchestrator.py # DAG 编排器
│   └── ...
├── gateway/           # I/O 网关
│   ├── provider.py    # LLM 客户端
│   ├── privacy.py     # 隐私脱敏
│   └── ...
├── api/               # FastAPI 后端
├── web/               # Next.js 前端
├── tests/             # 测试文件
├── docs/              # 文档
└── runfiles/          # 示例 Runfile
```

---

## 开发流程

### 1. 创建分支

```bash
git checkout -b feature/your-feature-name
# 或
git checkout -b fix/your-bug-fix
```

### 2. 开发

- 编写代码
- 添加测试
- 更新文档（如需要）

### 3. 验证

```bash
# 运行测试
python -m pytest tests/ -v

# 运行 lint
ruff check core/ gateway/ api/ main.py

# 格式化
ruff format core/ gateway/ api/ main.py
```

### 4. 提交

```bash
git add .
git commit -m "feat: your feature description"
```

### 5. 推送

```bash
git push origin feature/your-feature-name
```

### 6. 创建 Pull Request

---

## 代码规范

### Python

- **类型提示**：所有函数参数和返回值必须有类型提示
- **文档字符串**：所有公开类和方法必须有 docstring
- **不可变性**：优先使用不可变数据结构
- **文件大小**：单文件不超过 800 行
- **函数大小**：单函数不超过 50 行

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `ActorCriticLoop` |
| 函数/方法 | snake_case | `run_mass_production` |
| 常量 | UPPER_SNAKE | `MAX_RETRIES` |
| 私有方法 | _前缀 | `_internal_method` |
| 布尔变量 | is_/has_/can_ | `is_halted` |

### 错误处理

```python
# ✅ 好：显式处理
try:
    result = await provider.request(messages)
except LLMProviderError as exc:
    logger.error(f"LLM 请求失败: {exc}")
    raise

# ❌ 坏：静默吞掉
try:
    result = await provider.request(messages)
except Exception:
    pass
```

---

## 测试要求

### 最低覆盖率

- 新功能：100% 测试覆盖
- Bug 修复：必须有回归测试
- 总体覆盖率：80%+

### 测试结构

```python
class TestFeatureName:
    """Tests for FeatureName."""

    def test_basic_case(self):
        """基本用例测试."""
        # Arrange
        input_data = "..."

        # Act
        result = feature(input_data)

        # Assert
        assert result == expected

    def test_edge_case(self):
        """边界条件测试."""
        ...

    def test_error_case(self):
        """错误处理测试."""
        ...
```

### 异步测试

```python
import pytest

@pytest.mark.asyncio
async def test_async_feature():
    result = await async_function()
    assert result is not None
```

---

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>(<scope>): <description>

[optional body]
```

### 类型

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `refactor` | 重构（不改变功能） |
| `docs` | 文档更新 |
| `test` | 测试相关 |
| `chore` | 构建/工具链 |
| `perf` | 性能优化 |

### 示例

```
feat(runner): add EvalJudge integration for multi-dimensional scoring
fix(privacy): correct label extraction from placeholder strings
docs(readme): update test count and feature list
test(eval_judge): add weight verification tests
```

---

## Pull Request 流程

### PR 标题

与提交信息格式相同：`feat(scope): description`

### PR 描述模板

```markdown
## 变更说明
简要描述本次变更的内容和原因。

## 变更类型
- [ ] 新功能
- [ ] Bug 修复
- [ ] 重构
- [ ] 文档更新
- [ ] 测试

## 测试
- [ ] 所有现有测试通过
- [ ] 添加了新测试
- [ ] 测试覆盖率满足要求

## 检查清单
- [ ] 代码符合项目规范
- [ ] 已运行 `ruff check`
- [ ] 已运行 `ruff format`
- [ ] 已更新相关文档
```

### 审查要求

- 至少 1 人审查通过
- 所有 CI 检查通过
- 无合并冲突

---

## 报告 Bug

使用 GitHub Issues，包含：

1. **环境信息**：Python 版本、OS
2. **复现步骤**：最小复现路径
3. **期望行为**：应该发生什么
4. **实际行为**：实际发生了什么
5. **错误日志**：完整的 traceback

---

## 功能建议

使用 GitHub Issues，包含：

1. **问题描述**：解决什么问题
2. **方案建议**：如何实现
3. **替代方案**：考虑过哪些其他方案
4. **上下文**：使用场景

---

## 行为准则

- 尊重所有参与者
- 接受建设性批评
- 专注于对社区最有利的事情
- 对他人表示同理心
