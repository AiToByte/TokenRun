# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TokenRun** is an industrial-grade AI task execution framework that converts AI tokens into deterministic, high-quality outputs. The core mechanism is **Loop Engineering**: an Actor-Critic loop where an expensive model (Actor) generates output, a cheap model (Critic) audits quality, and the system iterates until quality criteria are met.

## Architecture

The system follows a pipeline architecture: **Runfile (YAML blueprint) → Orchestrator (DAG scheduler) → Runner (Actor-Critic loop) → Persistence (SQLite traces)**.

### Data Flow

```
Input Data → Privacy Redaction → Actor (expensive model, Jinja2 templates)
                                        ↓
                                Generated output
                                        ↓
                                Programmatic validation (regex/json_schema/code_eval — no LLM cost)
                                        ↓
                                Critic (cheap model, structured JSON scoring)
                                        ↓
                                Pass → Final Output + Trace persistence
                                Fail → Feedback injection → Actor retry
```

### Core Engine (`core/`)

- **`models.py`** — Pydantic V2 protocol models. All models use `extra="forbid"` for strict validation. Key types: `Runfile`, `TaskNode`, `TaskTrace`, `EvaluationResult`, `LoopConfig`, `ValidationRule`.
- **`runner.py`** — `ActorCriticLoop` engine. 3 strategies: `FEEDBACK_DRIVEN` (retry with critique), `EXHAUSTIVE` (run all, pick best), `ONCE` (single shot). Splits exit criteria into programmatic (regex/json_schema/code_eval) vs LLM-eval rules. Supports consensus validation (multi-model voting) and dynamic model tier escalation.
- **`orchestrator.py`** — `TROrchestrator` with async concurrency (`asyncio.Semaphore`), topological DAG execution (Kahn's algorithm), sampling gate, fingerprint verification, pause/resume, quality circuit breaker (sliding window), drift detection, and self-healing integration.
- **`actor.py`** / **`critic.py`** — Actor uses Jinja2 templates with feedback injection. Critic returns structured JSON with per-dimension scores.
- **`ledger.py`** — `TokenLedger` with budget circuit breaker (`BudgetExceededError`), ROI reporting, conservative fallback pricing for unknown models.
- **`persistence.py`** — `TaskPersistence` SQLite storage with `threading.Lock` for concurrent writes, idempotent `INSERT OR REPLACE`.
- **`eval_judge.py`** — `EvalJudge` multi-dimensional quality evaluation (alternative to Critic for structured scoring).
- **`quality_gate.py`** — Sliding-window circuit breaker for quality monitoring.
- **`resilience.py`** — Circuit breaker + bulkhead + retry patterns for fault tolerance.
- **`sandbox.py`** — Security-hardened Python execution (AST-based) for `code_eval` rules.
- **`solidifier.py`** — Extracts optimal prompts and golden samples into `.trs` skill packages. Also exports fine-tune datasets (OpenAI/Alpaca/ShareGPT formats).
- **`drift_detector.py`** — Hash-based + embedding-based consistency monitoring, raises `DriftAlert` on mismatch.
- **`self_healer.py`** — Meta-model auto-optimizes prompts when Critic detects repeated failure patterns.
- **`prompt_lineage.py`** — Version-controlled prompt evolution with parent tracking and pass-rate stats.
- **`context_cache.py`** — Prompt cache optimization for cost reduction.
- **`task_queue.py`** — Priority queue (HIGH/NORMAL/LOW) for token arbitrage routing.
- **`cost_scheduler.py`** — Routes LOW-priority tasks to Batch API for 50% cost savings.
- **`app.py`** — `TokenRunApp` main controller wiring all components into a unified lifecycle: sampling → approval → production → solidification.

### Gateway (`gateway/`)

- **`provider.py`** — `LLMProvider` async httpx client for OpenAI-compatible APIs. Exponential backoff, Retry-After support, `LLMProviderError` custom exception. Also supports embedding generation.
- **`privacy.py`** — `PrivacyRedactor` reversible PII masking (EMAIL, PHONE, ID_CARD, IP_ADDR, API_KEY) with `[[TR_{LABEL}_{N}]]` placeholders. `PersistentRedactor` subclass adds SQLite-backed vault for crash recovery.
- **`file_gateway.py`** / **`s3_gateway.py`** / **`sql_gateway.py`** / **`duckdb_gateway.py`** — Data source adapters.
- **`batch_provider.py`** — OpenAI Batch API integration (50% cost, 24h window).
- **`mcp_client.py`** — MCP protocol client for external tool servers.

### API & Frontend

- **`api/main.py`** — FastAPI backend with REST endpoints (`/missions`, `/skills`, `/health`) and WebSocket (`/ws`) for real-time events.
- **`web/`** — Next.js 14 + TailwindCSS Cockpit UI with Dashboard, Missions, Skills pages.

## Key Design Decisions

- **Determinism over creativity**: Default `temperature=0.1`; fingerprint locking prevents silent prompt drift.
- **Cheap model audits expensive model**: Critic uses structured JSON output for reliable parsing.
- **Reversible masking**: Privacy redaction uses placeholder mapping stored only in memory; destroyed after task completion.
- **Financial safety as first-class**: Every LLM call passes through the Ledger; budget exceeded → immediate circuit break.
- **Programmatic validation saves tokens**: regex/json_schema/code_eval rules are validated without LLM calls; only `llm_eval` uses the Critic.
- **Immutable Pydantic models**: All protocol models use `ConfigDict(extra="forbid")` — rejects unknown fields at parse time.

## Commands

```bash
# Install
pip install -e ".[dev]"

# Run all tests
python -m pytest tests/ -v

# Run single test
python -m pytest tests/test_runner.py::TestActorCriticLoop::test_first_attempt_passes -v

# Run with coverage
python -m pytest tests/ --cov=core --cov=gateway --cov=api

# Lint
ruff check core/ gateway/ api/ main.py

# Format check
ruff format --check core/ gateway/ api/ main.py

# Auto-format
ruff format core/ gateway/ api/ main.py

# CLI mode
python main.py                          # default test_mission
python main.py runfiles/custom.yaml     # custom Runfile
python main.py --sample-only            # sampling phase only

# API mode
uvicorn api.main:app --reload           # localhost:8000

# Frontend
cd web && npm install && npm run dev    # localhost:3000

# Docker
docker-compose up                       # backend:8000 + frontend:3000
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

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = "auto"` in pyproject.toml)
- **Pattern**: `unittest.mock.AsyncMock` for LLM calls, `MagicMock(spec=...)` for type safety
- **484 tests** across 32 test files covering: unit (models, privacy, ledger, provider), integration (E2E lifecycle, DAG execution), edge cases (budget fuse, cyclic deps), security (PII, sandbox, AST bypass), performance, concurrency, resilience (circuit breaker, bulkhead), and quality evaluation
- **Run single test**: `python -m pytest tests/test_runner.py::TestActorCriticLoop::test_first_attempt_passes -v`

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, Pydantic V2, httpx, Jinja2, Rich
- **Storage**: SQLite (traces), DuckDB (future)
- **Frontend**: Next.js 14 + TailwindCSS + Lucide icons
- **Linting**: ruff
- **Deployment**: Docker + docker-compose, GitHub Actions CI (tests on Python 3.10–3.13)
