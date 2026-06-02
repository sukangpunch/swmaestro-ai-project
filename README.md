# AI 정산 비서

모임 정산 시 자연어로 입력된 예외 조건(술 미섭취, 지각, 중도 귀가 등)을  
LLM 맥락 추론 + 규칙 기반 계산 엔진으로 처리하는 Agentic Workflow 프로젝트.

---

## 서비스 개요

```
"총 8만원이고 A, B, C, D 있어. 주류 3만원 / 안주 5만원. C는 늦게 왔고 D는 술 안 마셨어."
```

위와 같이 자연어로 상황을 입력하면:

1. **LLM**이 예외 조건을 파악하고 감액률(discount_rate)·할증률(surcharge_rate)을 결정
2. **계산 엔진**이 rate를 실제 금액으로 환산하여 각자 부담액 계산
3. **UI**가 정산 결과와 카카오톡 공유용 메시지를 출력

---

## 아키텍처

```
[Streamlit UI — front/]
       │  자연어 입력 / payer 이름
       ▼
[LangGraph Workflow — ai/]
  ┌─────────────────────────────────────────────┐
  │  InputParsingNode   → 자연어 → 구조화 JSON   │
  │  SafetyCheckNode    → 입력 유효성 검사        │
  │  RouteRequestNode   → 전략 결정 (LLM)        │
  │  CalculationNode    → calculator/ 호출        │
  │  ReportGenerationNode → 설명 + 공유 메시지    │
  │  FeedbackParsingNode  → 피드백 재계산 진입    │
  └─────────────────────────────────────────────┘
       │
       ▼
[계산 엔진 — calculator/]
  calculate(parsed_json) → 부담액 계산 + 검증
```

세 폴더 모두 **하나의 Python 프로세스**에서 실행된다. HTTP 통신 없이 직접 import 호출.

---

## 폴더 구조

```
swmaestro-ai-project/
├── front/
│   └── app.py              # Streamlit UI 진입점
│
├── ai/
│   ├── graph.py            # LangGraph StateGraph 정의
│   ├── nodes.py            # 각 노드 구현 + Upstage LLM 호출
│   └── state.py            # SettlementState TypedDict 스키마
│
├── calculator/
│   ├── engine.py           # 정산 계산 함수 (calculate / recalculate)
│   └── tests/
│       └── test_engine.py  # 계산 엔진 단위 테스트
│
├── .env.example            # 환경변수 키 목록 (팀원 참고용)
└── CLAUDE.md               # 프로젝트 설계 원칙 및 개발 가이드
```

---

## 폴더별 역할

### `front/` — Streamlit UI

| 역할 | 설명 |
|------|------|
| 입력 수신 | 자연어 정산 상황, 결제자(payer) 이름 |
| 워크플로우 실행 | `graph.invoke(state)` 직접 호출 |
| 결과 표시 | 참여자별 부담액 카드, 계산 근거, 공유 메시지 |
| 피드백 모드 | 이전 결과가 있으면 자동으로 재계산 모드 전환 |

**화면 구성:**
```
[좌측 사이드바]              [메인 영역]
  📋 상황별 예시              # 💸 AI 정산 비서
  🗑️ 대화 초기화              ─────────────────
  ─────────                  👤 사용자 입력
  🍺 예시1 [채우기↗]          🤖 정산 결과 카드
  ...                        ─────────────────
                             [정산 상황 입력창]
                             [전송 →]
```

---

### `ai/` — Agentic Workflow (LangGraph + Upstage)

LLM은 **"얼마나 적용할지 판단"** 만 담당하며, **산술 계산은 수행하지 않는다.**

#### LangGraph 노드 흐름

```
START
  │
  ├─ [기존 parsed_json 없음] ──► InputParsingNode
  │                                    │
  │                               SafetyCheckNode ──► [오류 시] END
  │                                    │
  │                               RouteRequestNode   (전략: SIMPLE / EXCEPTION)
  │                                    │
  └─ [기존 parsed_json 있음] ──► FeedbackParsingNode
                                       │
                                  CalculationNode  (calculator/ 호출)
                                       │
                                  ReportGenerationNode
                                       │
                                      END
```

#### 각 노드 역할

| 노드 | 담당 | LLM 호출 |
|------|------|----------|
| `InputParsingNode` | 자연어 → 구조화 JSON, 예외 조건 rate 결정 | O |
| `SafetyCheckNode` | 총액 누락·불일치, 중복 참여자, null rate 감지 | X |
| `RouteRequestNode` | 전략 분기 결정 (SIMPLE / EXCEPTION) | O |
| `CalculationNode` | `calculator.engine.calculate()` 호출 | X |
| `ReportGenerationNode` | 계산 근거 설명 + 공유 메시지 생성 | O |
| `FeedbackParsingNode` | 피드백 조건만 기존 parsed_json에 반영 | O |

#### LangGraph State 스키마

```python
class SettlementState(TypedDict, total=False):
    raw_input          : str        # 사용자 자연어 입력
    parsed_json        : dict       # 구조화 JSON (예외 조건 + rate 포함)
    strategy           : str        # SIMPLE | EXCEPTION
    calculation_result : dict       # calculator/ 반환값
    feedback_history   : list[str]  # 피드백 이력
    calc_explanation   : str        # 계산 근거 설명 텍스트
    final_report       : str        # 공유용 메시지
    safety_error       : str        # 유효성 오류 메시지
```

#### parsed_json 구조 예시

```json
{
  "total_amount": 80000,
  "items": [
    { "name": "주류", "amount": 30000 },
    { "name": "안주", "amount": 50000 }
  ],
  "participants": [
    { "name": "A", "exceptions": [] },
    { "name": "B", "exceptions": [] },
    { "name": "C", "exceptions": [
      { "type": "늦은 도착", "target_items": ["주류", "안주"], "surcharge_rate": 0.2 }
    ]},
    { "name": "D", "exceptions": [
      { "type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0 }
    ]}
  ]
}
```

---

### `calculator/` — 정산 계산 엔진

LLM이 결정한 `discount_rate` / `surcharge_rate`를 실제 금액으로 환산하는 **순수 Python 모듈**.  
LLM을 호출하지 않으며, 웹 서버가 아니다.

#### 계산 처리 순서

| 단계 | 내용 |
|------|------|
| Step 0 | 항목 없으면 총액 ÷ N (균등 분배) |
| Step 1 | 항목별 eligible 참여자 기준 1인 몫 계산 + discount_rate 감액 + 감액분 재분배 |
| Step 2 | surcharge_rate / surcharge_amount 할증 적용 → 비할증자에게 차감 분배 |
| Step 3 | 최소 부담 하한선: 균등 분담액의 **30%** (강제 적용) |
| Step 4 | 반올림 오차 자동 보정 + 총액 검증 |

#### 예외 조건 rate 기준표

| 상황 | rate 종류 | 기준값 |
|------|----------|-------|
| 술 미섭취 / 전혀 안 먹음 | `discount_rate` | 1.0 (전액 제외) |
| 거의 안 먹음 / 한 입만 | `discount_rate` | 0.7 |
| 소량 섭취 / 절반 정도 | `discount_rate` | 0.5 |
| 중도 귀가 (절반 이상 비움) | `discount_rate` | 0.5 (모든 항목) |
| 잠깐 있다 감 | `discount_rate` | 0.3 |
| 지각 (비율 명시) | `surcharge_rate` | 명시 비율 (예: 0.2) |
| 지각 (금액 명시) | `surcharge_amount` | 명시 금액 (예: 5000) |

#### 주요 함수

```python
from calculator.engine import calculate, recalculate

# 초기 계산
result = calculate(parsed_json)

# 피드백 반영 재계산
result = recalculate(parsed_json, feedback_json)
```

---

## 실행 방법

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env 파일에 UPSTAGE_API_KEY 값 입력

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 서비스 실행 (루트 디렉토리에서 실행해야 .env 로드됨)
streamlit run front/app.py
```

- 접속 URL: http://localhost:8501
- `.env`는 git에 포함되지 않으며 `.env.example`을 참고해 생성

---

## 테스트

```bash
pytest calculator/tests/
```

---

## 테스트 시나리오

| 시나리오 | 입력 예시 |
|---------|----------|
| A. 술 미섭취 + 지각 | 4명, 8만원 (주류 3만/안주 5만), D 미섭취, C 지각 |
| B. 선결제 + 복합 예외 | 5명, 12만원, A 전액 결제 |
| C. 피드백 재계산 | 기존 세션 유지 후 추가 조건 입력 |

---

## 핵심 설계 원칙

- **LLM은 계산하지 않는다.** 얼마나 적용할지(rate) 판단만 하고, 금액 환산은 `calculator/`가 담당
- **전략은 3가지:** SIMPLE(균등) / EXCEPTION(예외 조건 반영) / SPONSOR(선결제자 포함)
- **최소 부담 하한선:** 균등 분담액의 30% (어떤 예외 조건이 있어도 최소 이만큼은 부담)
- **세션은 브라우저 종료 시 초기화** — 장기 저장소 없음

---

## 환경변수

| 변수명 | 설명 |
|--------|------|
| `UPSTAGE_API_KEY` | Upstage Solar LLM API 키 |
