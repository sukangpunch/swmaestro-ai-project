# ai/ — Agentic Workflow (LangGraph + Upstage)

## 역할 범위

- 자연어 입력 파싱 → 구조화 JSON 생성
- 예외 조건별 감액률 결정 (맥락 기반, LLM 판단)
- 정산 전략 분기 결정 (SIMPLE / EXCEPTION / SPONSOR)
- 피드백 조건 구조화
- 계산 결과 기반 설명 생성

## 이 폴더에서 절대 하지 않는 것

- 감액률을 금액으로 환산하는 산술 연산 (calculator/에서만 수행)
- 30% 하한선 적용, 총액 검증, 반올림 보정 (calculator/ 내부에서 처리됨)
- Streamlit UI 렌더링

## LangGraph 노드 목록 및 순서

1. InputParsingNode : 자연어 → 구조화 JSON 생성 + 예외 조건별 감액률 결정 (Upstage LLM)
2. SafetyCheckNode : 총액 누락/모순 감지, 재입력 요청
3. RouteRequestNode : 전략 분기 결정 (Upstage LLM)
4. CalculationNode : `from calculator.engine import calculate` 직접 호출 → 하한선·검증이 포함된 최종 결과 수신
5. ReportGenerationNode : 계산 근거 설명 + 공유 메시지 생성 (Upstage LLM)
6. FeedbackParsingNode : 피드백 입력 시 수정 조건 구조화 → CalculationNode로 재진입

> FairnessAdjustNode / ValidatorNode는 별도 노드로 두지 않는다.
> 이 로직은 calculator/ 내부에 있으며, CalculationNode가 받은 결과에 이미 반영되어 있다.

## 프롬프트 작성 규칙

- InputParsingNode : 산술 계산 금지. 예외 조건을 `discount_rate` / `surcharge_rate` 로 분류.
  - **감액 조건** (소비 감소) → `discount_rate` 사용
    - 술 미섭취 → 주류 `discount_rate: 1.0`
    - 소량 섭취 → `discount_rate: 0.3~0.5`
    - 중도 귀가 → 모든 항목 `discount_rate: 0.5`
  - **할증 조건** (패널티) → `surcharge_rate` 사용
    - 지각/늦은 도착 → 안주 `surcharge_rate: 0.15~0.3` (단순 언급이면 0.2)
- RouteRequestNode : "단어 기반이 아닌 문맥 기반으로 판단하라"
  - 선결제자 있음 → SPONSOR, 예외 조건 하나라도 있음 → EXCEPTION, 나머지 → SIMPLE
  - 예: `"아 나 이따 가야해"` → 중도 귀가(EXCEPTION)로 분류
- FeedbackParsingNode : "기존 정산 정보를 임의로 덮어쓰지 말고, 새로 말한 조건만 추가/수정. discount_rate / surcharge_rate 모두 사용 가능."
- ReportGenerationNode : "특정 참여자를 비난하지 말고, 항목 참여 여부와 rate 적용 이유를 기준으로 중립적으로 설명하라"

## LLM 설정

- 모델 : Upstage Solar 계열
- API Key : 환경변수 `UPSTAGE_API_KEY` 사용

## LangGraph State 스키마 (공유 인터페이스)

State 객체에 반드시 포함되어야 하는 필드:

```python
raw_input         : str        # 사용자 원문 (피드백 시에는 피드백 텍스트)
payer             : str        # 전액 결제자 이름 (front/에서 입력, 없으면 빈 문자열)
parsed_json       : dict       # 구조화 결과 — discount_rate/surcharge_rate 포함
strategy          : str        # SIMPLE | EXCEPTION | SPONSOR
calculation_result: dict       # calculator/ 반환값 (하한선·검증 포함)
feedback_history  : list[str]  # 피드백 이력
final_report      : str        # 최종 설명 텍스트
safety_error      : str        # SafetyCheckNode 오류 메시지 (없으면 빈 문자열)
```

## payer 주입 규칙 (_inject_payer)

`ai/nodes.py`의 `_inject_payer(parsed, payer)`:
- `payer`가 참여자 목록에 있으면 `parsed_json`에 `sponsor: {name: payer, prepaid: total_amount}` 추가
- `input_parsing_node`와 `feedback_parsing_node` 두 곳 모두에서 호출
- payer가 없거나 참여자 목록에 없으면 아무것도 하지 않음 (기존 parsed_json 그대로 반환)

`parsed_json` 구조 예시:

```json
{
  "total_amount": 80000,
  "items": [
    {"name": "주류", "amount": 30000},
    {"name": "안주", "amount": 50000}
  ],
  "participants": [
    {"name": "A", "exceptions": []},
    {"name": "B", "exceptions": []},
    {"name": "C", "exceptions": [
      {"type": "늦은 도착", "target_items": ["안주"], "discount_rate": 0.3}
    ]},
    {"name": "D", "exceptions": [
      {"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0}
    ]}
  ]
}
```

`discount_rate`는 해당 항목에서 제외되는 비율이다. `1.0` = 전액 제외, `0.3` = 30% 감액.
