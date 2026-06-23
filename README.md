<div align="center">

# 🏭 TokenRun

**Industrial-Grade AI Task Execution Framework**

*Convert expiring "graveyard" AI tokens into deterministic, high-quality outputs.*

[English](#english) | [中文](#中文) | [日本語](#日本語) | [한국어](#한국어)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-345%20passed-brightgreen)](#testing)

</div>

---

<a name="english"></a>
## 🇬🇧 English

### What is TokenRun?

TokenRun is a production-grade framework that transforms unreliable AI outputs into industrial-quality results through **Loop Engineering** — an Actor-Critic feedback loop where an expensive model generates output, a cheap model audits quality, and the system iterates until quality criteria are met.

**Think of it as a refinery for AI tokens.**

```
Input (raw data) → Actor (expensive model) → Critic (cheap model audit)
                       ↑                              │
                       └──── feedback if failed ───────┘
                       
Output: Deterministic, verified, high-quality results
```

### Key Features

| Feature | Description |
|---|---|
| 🔄 **Loop Engineering** | Actor-Critic feedback loop with 3 strategies (FEEDBACK_DRIVEN, EXHAUSTIVE, ONCE) |
| 📋 **Runfile Blueprints** | Declarative YAML task definitions with validation rules |
| 🔒 **Privacy Redaction** | Reversible PII masking before data leaves your device |
| 💰 **Budget Circuit Breaker** | Real-time cost tracking with automatic shutdown |
| 🎯 **1% Sampling Gate** | Validate quality at minimal cost before full execution |
| 🔑 **Fingerprint Locking** | Lock model+prompt hash to prevent silent drift |
| 🧠 **Smart Model Routing** | Auto-escalate from cheap to expensive models on failure |
| 🩺 **Self-Healing Prompts** | Meta-model auto-optimizes prompts from Critic patterns |
| 🔍 **Semantic Drift Detection** | Embedding-based similarity monitoring |
| 📦 **Skill Solidification** | Extract reusable `.trs` skill packages from successful runs |
| 🔗 **Skill Chaining** | Nest skills like building blocks for complex pipelines |
| 📊 **Knowledge Distillation** | Export [Input]→[Output] pairs as fine-tuning datasets |
| 🌐 **MCP Server** | Expose skills as MCP tools for Claude Desktop |
| ⏱️ **Time-Travel Debugging** | Slider to review any iteration state |
| 💹 **Live ROI Dashboard** | Real-time value creation metrics |

### Architecture

```
TokenRun/
├── core/                    # Core engine
│   ├── models.py            # Pydantic V2 protocol models
│   ├── runner.py            # Actor-Critic loop engine
│   ├── orchestrator.py      # Task scheduler (DAG + concurrency)
│   ├── critic.py            # Cheap model quality auditor
│   ├── actor.py             # Expensive model executor
│   ├── ledger.py            # Token budget + circuit breaker
│   ├── persistence.py       # SQLite checkpoint/resume
│   ├── drift_detector.py    # Hash + semantic drift detection
│   ├── self_healer.py       # Auto-prompt optimization
│   ├── prompt_lineage.py    # Version control for prompts
│   ├── solidifier.py        # Skill extraction + .trs export
│   ├── cost_scheduler.py    # Token arbitrage routing
│   ├── task_queue.py        # Priority queue (HIGH/NORMAL/LOW)
│   ├── sampling_manager.py  # 1% sampling gate
│   ├── telemetry.py         # Event broadcasting
│   ├── sandbox.py           # Safe code execution
│   └── mcp_server.py        # MCP protocol server
├── gateway/                 # I/O layer
│   ├── provider.py          # LLM client (OpenAI-compatible)
│   ├── privacy.py           # PII redaction engine
│   ├── file_gateway.py      # Local file streaming
│   ├── batch_provider.py    # OpenAI Batch API (50% cost)
│   ├── s3_gateway.py        # S3-compatible storage
│   ├── sql_gateway.py       # SQL database access
│   ├── video_gateway.py     # Video frame extraction
│   └── audio_gateway.py     # Audio transcription (Whisper)
├── api/                     # FastAPI backend
│   └── main.py              # REST + WebSocket + SSE
├── web/                     # Next.js Cockpit UI
│   └── src/
│       ├── app/             # Dashboard, Missions, Skills
│       ├── components/      # ShadcnUI-style components
│       └── lib/             # API client + WebSocket hook
├── tests/                   # 345 tests (pytest)
├── skills/library/          # Preset skill packages
├── docs/                    # Design documents
├── Dockerfile               # Backend container
└── docker-compose.yml       # Full stack orchestration
```

### Quick Start

```bash
# Clone
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun

# Install
pip install -e ".[dev]"

# Configure API key
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

# Run tests
python -m pytest tests/ -v

# CLI mode
python main.py                           # default test mission
python main.py runfiles/custom.yaml      # custom Runfile
python main.py --sample-only             # sampling only

# API mode
uvicorn api.main:app --reload            # localhost:8000

# Frontend
cd web && npm install && npm run dev     # localhost:3000

# Docker
docker-compose up                        # backend:8000 + frontend:3000
```

### Runfile Example

```yaml
name: "Finance_Refinery"
workflow:
  - id: "classifier"
    name: "Transaction Classification"
    actor_prompt_template: |
      Classify this transaction: {{ data }}
      Output JSON: {"category": "...", "confidence": 0.0-1.0}
    model_tiers:
      - { model: "gpt-4o-mini", escalate_after: 2 }  # cheap first
      - { model: "gpt-4o", escalate_after: 3 }        # escalate on failure
    loop_config:
      strategy: "feedback-driven"
      max_attempts: 5
      min_score: 0.85
      exit_criteria:
        - type: "json_schema"
          criteria: {"required": ["category", "confidence"]}
        - type: "llm_eval"
          criteria: "Classification must follow accounting logic"

governance:
  max_usd: 5.0
```

### Testing

```bash
# Full suite
python -m pytest tests/ -v

# Single test
python -m pytest tests/test_runner.py::TestActorCriticLoop::test_first_attempt_passes -v

# Coverage
python -m pytest tests/ --cov=core --cov=gateway --cov=api
```

### Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Pydantic V2 |
| LLM Client | httpx (async), OpenAI-compatible |
| Storage | SQLite (traces), DuckDB (future) |
| Frontend | Next.js 14, TailwindCSS, TypeScript |
| Testing | pytest, pytest-asyncio |
| Deployment | Docker, GitHub Actions CI/CD |

---

<a name="中文"></a>
## 🇨🇳 中文

### TokenRun 是什么？

TokenRun 是一个**工业级 AI 任务执行框架**，通过 **Loop Engineering（循环工程）** 将不可靠的 AI 输出转化为工业级质量的结果。

核心机制：**Actor-Critic 循环**
- **Actor**（昂贵模型，如 GPT-4o）：执行任务生成输出
- **Critic**（廉价模型，如 GPT-4o-mini）：审计质量并给出反馈
- **循环**：不合格则注入反馈重新生成，直到通过或达到最大次数

### 核心特性

- 🔄 **三种循环策略**：反馈驱动、穷举最优、单次执行
- 📋 **声明式 Runfile**：YAML 任务蓝图，定义工作流、校验规则、预算
- 🔒 **隐私脱敏**：可逆占位符替换，云端永远看不到真实数据
- 💰 **预算熔断**：实时 Token 计费，超限立即停止
- 🎯 **1% 采样闸门**：低成本验证逻辑后再全量执行
- 🧠 **动态模型路由**：先用廉价模型初筛，失败后自动提权
- 🩺 **Runfile 自愈**：Critic 连续相似反馈时，元模型自动优化 Prompt
- 🔍 **语义漂移检测**：基于 Embedding 向量相似度的质量监控
- 📦 **技能固化**：执行完成后提取最优配置为 `.trs` 技能包
- 🔗 **技能递归**：Runfile 中直接引用技能文件，形成"技能积木"
- 📊 **知识蒸馏**：导出 [输入]→[输出] 对为微调数据集
- 🌐 **MCP Server**：作为 MCP 服务器运行，Claude Desktop 可直接调用技能

### 快速开始

```bash
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY
python -m pytest tests/ -v
python main.py
```

### Runfile 示例

```yaml
name: "财务报告提纯"
workflow:
  - id: "classifier"
    name: "交易分类"
    actor_prompt_template: "将以下交易分类：{{ data }}"
    model_tiers:
      - { model: "gpt-4o-mini", escalate_after: 2 }
      - { model: "gpt-4o", escalate_after: 3 }
    loop_config:
      max_attempts: 5
      min_score: 0.85
      exit_criteria:
        - type: "llm_eval"
          criteria: "分类必须符合财务会计逻辑"

governance:
  max_usd: 5.0
```

---

<a name="日本語"></a>
## 🇯🇵 日本語

### TokenRun とは？

TokenRun は、**ループエンジニアリング**を通じて信頼性の低い AI 出力を工業品質の結果に変換する**産業用 AI タスク実行フレームワーク**です。

### 主な特徴

- 🔄 **Actor-Critic ループ**：高価なモデルが生成し、安いモデルが品質を監査
- 📋 **宣言的 Runfile**：YAML タスクブループリント
- 🔒 **プライバシー脱感作**：可逆的なPIIマスキング
- 💰 **予算サーキットブレーカー**：リアルタイムコスト追跡
- 🎯 **1% サンプリングゲート**：低コストで品質検証
- 🧠 **スマートモデルルーティング**：失敗時に自動エスカレーション
- 📦 **スキル固化**：`.trs` スキルパッケージへの抽出
- 🌐 **MCP サーバー**：Claude Desktop から直接スキル呼び出し

### クイックスタート

```bash
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun
pip install -e ".[dev]"
cp .env.example .env
python -m pytest tests/ -v
python main.py
```

---

<a name="한국어"></a>
## 🇰🇷 한국어

### TokenRun이란?

TokenRun은 **루프 엔지니어링**을 통해 신뢰할 수 없는 AI 출력을 산업 품질의 결과로 변환하는 **산업용 AI 작업 실행 프레임워크**입니다.

### 주요 기능

- 🔄 **Actor-Critic 루프**: 비싼 모델이 생성하고 저렴한 모델이 품질을 감사
- 📋 **선언적 Runfile**: YAML 작업 청사진
- 🔒 **프라이버시 비식별화**: 가역적 PII 마스킹
- 💰 **예산 차단기**: 실시간 비용 추적
- 🎯 **1% 샘플링 게이트**: 저비용 품질 검증
- 🧠 **스마트 모델 라우팅**: 실패 시 자동 에스컬레이션
- 📦 **스킬 고체화**: `.trs` 스킬 패키지로 추출
- 🌐 **MCP 서버**: Claude Desktop에서 직접 스킬 호출

### 빠른 시작

```bash
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun
pip install -e ".[dev]"
cp .env.example .env
python -m pytest tests/ -v
python main.py
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`python -m pytest tests/ -v`)
4. Commit your changes (`git commit -m 'feat: add amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

## 📧 Contact

- GitHub: [AiToByte/TokenRun](https://github.com/AiToByte/TokenRun)
