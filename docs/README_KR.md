<div align="center">

# 🏭 TokenRun

### 산업용 AI 작업 실행 프레임워크

**AI 토큰을 신뢰할 수 있는 고품질 출력으로 변환합니다.**

[English](../README.md) | [中文](README_CN.md) | [日本語](README_JP.md) | [한국어](#개요)

</div>

---

<a name="개요"></a>

## 개요

TokenRun은 **루프 엔지니어링**을 통해 신뢰할 수 없는 AI 출력을 산업 품질의 결과로 변환하는 프로덕션 그레이드 프레임워크입니다.

핵심 메커니즘: **Actor-Critic 루프**

1. **Actor** (비싼 모델, 예: GPT-4o)가 작업을 실행하고 출력을 생성
2. **Critic** (저렴한 모델, 예: GPT-4o-mini)이 품질을 감사하고 피드백 제공
3. 품질이 기준에 미달하면 피드백을 주입하고 Actor가 재시도
4. 품질 기준을 충족하거나 예산이 소진될 때까지 루프 계속

## 주요 기능

### 실행 엔진

| 기능 | 설명 |
|---|---|
| **루프 엔지니어링** | Actor-Critic 피드백 루프, 3가지 전략: 피드백驱动, 철저한, 단일 |
| **프로그래밍 검증** | `regex` 및 `json_schema` 규칙은 LLM 호출 없이 검증 |
| **다차원 스코어링** | Critic이 차원별 점수(정확성, 완전성, 형식)를 반환 |
| **동적 모델 라우팅** | N회 실패 후 저렴한 모델에서 비싼 모델로 자동 에스컬레이션 |

### 안전성과 확정성

| 기능 | 설명 |
|---|---|
| **프라이버시 비식별화** | 가역적 PII 마스킹 (이메일, 전화, 신분증, IP, API Key) |
| **예산 차단기** | 실시간 USD 추적, 예산 초과 시 자동 중지 |
| **지문 잠금** | 모델 ID + 프롬프트 해시 + temperature + seed 잠금 |

### 자산과 생태계

| 기능 | 설명 |
|---|---|
| **스킬 고체화** | 최적 프롬프트 + 골든 샘플을 `.trs` 스킬 패키지로 추출 |
| **스킬 체이닝** | Runfile 노드에서 `.trs` 파일 참조 |
| **지식 증류** | [입력]→[출력] 쌍을 미세 조정 데이터셋으로 내보내기 |

## 빠른 시작

```bash
git clone https://github.com/AiToByte/TokenRun.git
cd TokenRun
pip install -e ".[dev]"
cp .env.example .env
python -m pytest tests/ -v
python main.py
```

## 기술 스택

| 계층 | 기술 |
|---|---|
| 백엔드 | Python 3.10+, FastAPI, Pydantic V2 |
| LLM 클라이언트 | httpx (비동기), OpenAI 호환 |
| 스토리지 | SQLite (추적), DuckDB (예정) |
| 프론트엔드 | Next.js 14, TailwindCSS, TypeScript |
| 테스트 | pytest, pytest-asyncio |
