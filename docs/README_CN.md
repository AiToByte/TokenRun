<div align="center">

# 🏭 TokenRun

### 工业级 AI 任务执行框架

**将 AI TOKEN 转换为可靠、高质量的输出结果。**

[English](../README.md) | [中文](#概述) | [日本語](README_JP.md) | [한국어](README_KR.md)

</div>

---

<a name="概述"></a>

## 概述

TokenRun 是一个生产级的 AI 任务执行框架，通过 **Loop Engineering（循环工程）** 将不可靠的 AI 输出转化为工业级质量的结果。

核心机制：**Actor-Critic 循环**

1. **Actor**（昂贵模型，如 GPT-4o）执行任务生成输出
2. **Critic**（廉价模型，如 GPT-4o-mini）审计质量并给出反馈
3. 如果质量不达标，注入反馈后 Actor 重新生成
4. 循环持续直到质量达标或预算耗尽

```
输入 → 隐私脱敏 → Actor（昂贵模型）
                        ↓
                   Critic（廉价模型审计）
                        ↓
               ┌── 通过 → 最终输出
               └── 不通过 → 反馈注入 → Actor（重试）
```

## 核心特性

### 执行引擎

| 特性 | 说明 |
|---|---|
| **循环工程** | Actor-Critic 反馈循环，3 种策略：反馈驱动、穷举最优、单次执行 |
| **程序化校验** | `regex` 和 `json_schema` 规则无需 LLM 调用即可验证，节省 Token |
| **多维评分** | Critic 返回多维度评分（准确性、完整性、格式），支持加权聚合 |
| **动态模型路由** | 重试 N 次失败后自动从廉价模型升级到昂贵模型 |
| **穷举策略** | 运行所有尝试，选择得分最高的结果 |

### 安全与确定性

| 特性 | 说明 |
|---|---|
| **隐私脱敏** | 可逆 PII 掩码（邮箱、手机、身份证、IP、API Key），数据离开设备前脱敏 |
| **预算熔断** | 实时 USD 追踪，超预算自动停止 |
| **指纹锁定** | 锁定模型 ID + Prompt 哈希 + temperature + seed，防止静默漂移 |
| **1% 采样闸门** | 以最低成本验证质量后再全量执行 |
| **语义漂移检测** | 基于 Embedding 余弦相似度的长期任务监控 |

### 资产与生态

| 特性 | 说明 |
|---|---|
| **技能固化** | 提取最优 Prompt + 黄金样本为可复用的 `.trs` 技能包 |
| **技能嵌套** | Runfile 节点直接引用 `.trs` 文件，构建技能积木 |
| **知识蒸馏** | 导出 [输入]→[输出] 对为微调数据集（OpenAI/Alpaca/ShareGPT） |
| **Prompt 谱系** | 版本控制的 Prompt 演进，支持通过率对比 |
| **自愈机制** | 元模型自动优化 Prompt（当 Critic 检测到重复失败模式时） |
| **MCP Server** | 将技能暴露为 MCP 工具，供 Claude Desktop 等客户端调用 |

## 快速开始

```bash
# 克隆
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun

# 安装
pip install -e ".[dev]"

# 配置
cp .env.example .env
# 编辑 .env 填入 API Key

# 测试
python -m pytest tests/ -v

# CLI 运行
python main.py

# API 模式
uvicorn api.main:app --reload

# 前端
cd web && npm install && npm run dev

# Docker
docker-compose up
```

## Runfile 示例

```yaml
name: "财务报告提纯"
workflow:
  - id: "classifier"
    name: "交易分类"
    actor_prompt_template: |
      将以下交易分类，输出 JSON：
      {"category": "...", "confidence": 0.0-1.0}
      交易：{{ data }}
    model_tiers:
      - { model: "gpt-4o-mini", escalate_after: 2 }
      - { model: "gpt-4o", escalate_after: 3 }
    loop_config:
      max_attempts: 5
      min_score: 0.85
      exit_criteria:
        - type: "json_schema"
          criteria: {"required": ["category", "confidence"]}
        - type: "llm_eval"
          criteria: "分类必须符合财务会计逻辑"

governance:
  max_usd: 5.0
```

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端 | Python 3.10+, FastAPI, Pydantic V2 |
| LLM 客户端 | httpx (异步), OpenAI 兼容 |
| 存储 | SQLite (轨迹), DuckDB (规划中) |
| 前端 | Next.js 14, TailwindCSS, TypeScript |
| 测试 | pytest, pytest-asyncio |
| 部署 | Docker, GitHub Actions CI/CD |
