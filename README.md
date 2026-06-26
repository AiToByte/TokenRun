<div align="center">

# 🏭 TokenRun

### Industrial-Grade AI Task Execution Framework

**Convert AI tokens into reliable, high-quality outputs.**

[English](#overview) | [中文](docs/README_CN.md) | [日本語](docs/README_JP.md) | [한국어](docs/README_KR.md)

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-000000?logo=next.js&logoColor=white)](https://nextjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Tests](https://img.shields.io/badge/tests-484%20passed-4CAF50)](#testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

---

<a name="overview"></a>

## Overview

TokenRun is a production-grade framework for executing AI tasks with guaranteed quality. Instead of accepting unreliable one-shot LLM output, TokenRun implements **Loop Engineering** — an Actor-Critic feedback loop where:

1. An **expensive model** (Actor) generates output
2. A **cheap model** (Critic) audits quality against defined criteria
3. If quality is insufficient, feedback is injected and the Actor retries
4. The loop continues until quality criteria are met or budget is exhausted

This transforms AI from an unpredictable black box into a deterministic, auditable production pipeline.

```
┌─────────────────────────────────────────────────────────────┐
│                     TokenRun Pipeline                        │
│                                                              │
│  Input → Privacy Redaction → Actor (expensive model)         │
│              ↓                    ↓                          │
│         Safe data          Generated output                  │
│                                  ↓                           │
│                          Critic (cheap model audit)           │
│                                  ↓                           │
│                     ┌── Pass → Final Output                  │
│                     │                                         │
│                     └── Fail → Feedback → Actor (retry)      │
│                                                              │
│  Token Ledger: real-time cost tracking + budget circuit break│
│  Persistence: checkpoint/resume after each iteration         │
│  Fingerprint: lock model+prompt hash for determinism         │
└─────────────────────────────────────────────────────────────┘
```

## Core Features

### Execution Engine

| Feature | Description |
|---|---|
| **Loop Engineering** | Actor-Critic feedback loop with 3 strategies: `feedback-driven`, `exhaustive`, `once` |
| **Programmatic Validation** | `regex` and `json_schema` rules validated without LLM calls — saves tokens |
| **Multi-dimensional Scoring** | Critic returns per-dimension scores (accuracy, completeness, format) with weighted aggregation |
| **Dynamic Model Routing** | Auto-escalate from cheap to expensive models after N failed retries |
| **EXHAUSTIVE Strategy** | Run all attempts, pick the highest-scoring result |

### Safety & Determinism

| Feature | Description |
|---|---|
| **Privacy Redaction** | Reversible PII masking (email, phone, ID, IP, API key) before data leaves your device |
| **Budget Circuit Breaker** | Real-time USD tracking with automatic shutdown when budget is exhausted |
| **Fingerprint Locking** | Lock model ID + prompt hash + temperature + seed to prevent silent drift |
| **1% Sampling Gate** | Validate quality at minimal cost before full production run |
| **Semantic Drift Detection** | Embedding-based cosine similarity monitoring during long tasks |

### Asset & Ecosystem

| Feature | Description |
|---|---|
| **Skill Solidification** | Extract optimal prompts + golden samples into reusable `.trs` skill packages |
| **Skill Chaining** | Reference `.trs` files in Runfile nodes — build complex pipelines from skill blocks |
| **Knowledge Distillation** | Export [Input]→[Output] pairs as fine-tuning datasets (OpenAI/Alpaca/ShareGPT) |
| **Prompt Lineage** | Version-controlled prompt evolution with pass-rate comparison |
| **Self-Healing** | Meta-model auto-optimizes prompts when Critic detects repeated failure patterns |
| **MCP Server** | Expose skills as MCP tools for Claude Desktop and other MCP clients |

### Operations

| Feature | Description |
|---|---|
| **Token Arbitrage** | Priority queue (HIGH/NORMAL/LOW) with Batch API routing for cost savings |
| **Drift Detection** | Hash-based + Embedding-based consistency monitoring |
| **Time-Travel Debugging** | Slider to review any data item at any iteration |
| **Live ROI Dashboard** | Real-time value creation metrics (items processed, success rate, cost per item) |

## Architecture

```
TokenRun/
├── core/                        # Core engine (12 modules)
│   ├── models.py                # Pydantic V2 protocol (Runfile, TaskNode, Trace)
│   ├── runner.py                # actor-critic loop engine
│   ├── orchestrator.py          # DAG scheduler + concurrency + pause/resume
│   ├── actor.py                 # expensive model executor (Jinja2 templates)
│   ├── critic.py                # cheap model auditor (structured JSON)
│   ├── ledger.py                # token budget + circuit breaker
│   ├── persistence.py           # SQLite checkpoint/resume
│   ├── drift_detector.py        # hash + semantic drift detection
│   ├── self_healer.py           # auto-prompt optimization
│   ├── prompt_lineage.py        # prompt version control
│   ├── solidifier.py            # skill extraction + .trs export
│   ├── cost_scheduler.py        # token arbitrage routing
│   ├── task_queue.py            # priority queue (HIGH/NORMAL/LOW)
│   ├── sampling_manager.py      # 1% sampling gate
│   ├── telemetry.py             # event broadcasting
│   ├── sandbox.py               # secure code execution (AST)
│   ├── eval_judge.py            # multi-dimensional quality evaluation
│   ├── quality_gate.py          # sliding-window circuit breaker
│   ├── resilience.py            # circuit breaker + bulkhead + retry
│   ├── context_cache.py         # prompt cache optimization
│   ├── jinja_env.py             # shared Jinja2 sandbox singleton
│   ├── mcp_server.py            # MCP protocol server (FastMCP)
│   └── app.py                   # main controller class
├── gateway/                     # I/O layer (9 modules)
│   ├── provider.py              # LLM client (OpenAI-compatible)
│   ├── privacy.py               # PII redaction engine
│   ├── file_gateway.py          # local file streaming
│   ├── batch_provider.py        # OpenAI Batch API (50% cost)
│   ├── mcp_client.py            # MCP client for external servers
│   ├── s3_gateway.py            # S3-compatible storage
│   ├── sql_gateway.py           # SQL database access
│   ├── video_gateway.py         # video frame extraction
│   └── audio_gateway.py         # audio transcription (Whisper)
├── api/                         # FastAPI backend
│   └── main.py                  # REST + WebSocket + SSE
├── web/                         # Next.js Cockpit UI
│   └── src/
│       ├── app/                 # Dashboard, Missions, Skills pages
│       ├── components/          # ShadcnUI-style components
│       └── lib/                 # API client + WebSocket hook
├── tests/                       # 484 tests (pytest)
├── skills/library/              # Preset skill packages
├── docs/                        # Design documents (14 files)
├── runfiles/                    # User task blueprints
├── Dockerfile                   # Backend container
└── docker-compose.yml           # Full stack orchestration
```

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+ (for frontend)
- An OpenAI-compatible API key

### Install

```bash
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env with your API key:
# OPENAI_API_KEY=sk-your-key-here
```

### Run Tests

```bash
python -m pytest tests/ -v
```

### CLI Mode

```bash
python main.py                           # default test mission
python main.py runfiles/custom.yaml      # custom Runfile
python main.py --sample-only             # sampling phase only
```

### API Mode

```bash
uvicorn api.main:app --reload            # localhost:8000
```

### Frontend

```bash
cd web && npm install && npm run dev     # localhost:3000
```

### Docker

```bash
docker-compose up                        # backend:8000 + frontend:3000
```

## Runfile Format

A Runfile is a YAML blueprint that declares what to do, how to do it, and under what constraints.

```yaml
name: "Finance_Refinery"
version: "1.0"

workflow:
  - id: "classifier"
    name: "Transaction Classification"
    actor_prompt_template: |
      Classify this transaction into a category.
      Output JSON: {"category": "...", "confidence": 0.0-1.0}

      Transaction: {{ data }}
    model_tiers:
      - { model: "gpt-4o-mini", escalate_after: 2 }
      - { model: "gpt-4o", escalate_after: 3 }
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
          criteria: "Classification must follow accounting logic"

security:
  masking_rules: ["emails", "api_keys", "phones"]

sampling:
  enabled: true
  mode: "percentage"
  value: 0.01
  auto_pause: true

governance:
  max_usd: 5.0
  max_loop_count: 10000
```

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | Shared API key (fallback for both) | — |
| `ACTOR_API_KEY` | Actor model API key | inherits `OPENAI_API_KEY` |
| `ACTOR_BASE_URL` | Actor API endpoint | `https://api.openai.com/v1` |
| `ACTOR_MODEL` | Actor model name | `gpt-4o` |
| `CRITIC_API_KEY` | Critic model API key | inherits `ACTOR_API_KEY` |
| `CRITIC_BASE_URL` | Critic API endpoint | inherits `ACTOR_BASE_URL` |
| `CRITIC_MODEL` | Critic model name | `gpt-4o-mini` |

## Testing

```bash
# Full suite
python -m pytest tests/ -v

# Single test
python -m pytest tests/test_runner.py::TestActorCriticLoop::test_first_attempt_passes -v

# With coverage
python -m pytest tests/ --cov=core --cov=gateway --cov=api
```

**484 tests** across 32 test files covering:
- Unit tests (models, privacy, ledger, provider, solidifier, persistence)
- Integration tests (E2E lifecycle, DAG execution, drift detection)
- Edge cases (budget fuse, cyclic dependencies, empty inputs)
- Security tests (PII protection, sandbox restrictions, AST bypass)
- Performance tests (throughput benchmarks)
- Concurrency tests (thread safety, async race conditions)
- Resilience tests (circuit breaker, bulkhead, retry policy)
- Quality evaluation tests (EvalJudge, multi-dimensional scoring)

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Pydantic V2 |
| LLM Client | httpx (async), OpenAI-compatible |
| Storage | SQLite (traces), DuckDB (future) |
| Frontend | Next.js 14, TailwindCSS, TypeScript |
| Testing | pytest, pytest-asyncio |
| Linting | ruff |
| Deployment | Docker, GitHub Actions CI/CD |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`python -m pytest tests/ -v`)
4. Run lint (`ruff check core/ gateway/ api/ main.py`)
5. Commit your changes (`git commit -m 'feat: add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## Documentation

| Document | Description |
|----------|-------------|
| [User Manual](docs/user-manual.md) | 快速上手指南、Runfile 编写、API 使用 |
| [Architecture](docs/architecture.md) | 技术架构、数据流、并发模型 |
| [Components](docs/components.md) | 模块详解、依赖关系、代码质量指标 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、代码规范、提交规范 |

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**TokenRun** — Transform AI tokens into reliable, high-quality outputs.

[GitHub](https://github.com/AiToByte/TokenRun) · [Issues](https://github.com/AiToByte/TokenRun/issues)

</div>
