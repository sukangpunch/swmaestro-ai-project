# AI 정산 비서 — LangGraph Agent 워크플로우 구조도

## 전체 흐름

```mermaid
flowchart TD
    START([▶ START]) --> entry_check{기존 parsed_json\n있는가?}

    entry_check -- "없음\n(최초 입력)" --> input_parsing
    entry_check -- "있음\n(피드백 입력)" --> feedback_parsing

    subgraph LLM_NODES["🤖 LLM 호출 노드 (Upstage Solar)"]
        input_parsing["📥 InputParsingNode\n자연어 → 구조화 JSON\ndiscount_rate / surcharge_rate 결정"]
        feedback_parsing["🔄 FeedbackParsingNode\n피드백 조건 반영\n기존 parsed_json 부분 수정"]
        route_request["🔀 RouteRequestNode\n전략 분기 결정\nSIMPLE / EXCEPTION"]
        report_generation["📝 ReportGenerationNode\n계산 근거 설명 생성\n카카오톡 공유 메시지 생성"]
    end

    subgraph LOGIC_NODES["⚙️ 순수 로직 노드"]
        safety_check["🛡️ SafetyCheckNode\n총액 누락·모순 감지\n중복 참여자 감지\nnull rate 감지"]
        calculation["🧮 CalculationNode\ncalculator.engine.calculate 호출\n하한선(30%) 적용 포함"]
    end

    input_parsing --> safety_check
    feedback_parsing --> safety_check

    safety_check --> safety_route{safety_error\n있는가?}
    safety_route -- "오류 있음\n(재입력 요청)" --> END_ERR([⛔ END\n오류 메시지 반환])
    safety_route -- "정상" --> route_request

    route_request --> calculation
    calculation --> report_generation
    report_generation --> END_OK([✅ END\n정산 결과 반환])
```

---

## State 흐름 (SettlementState)

```mermaid
flowchart LR
    subgraph state["SettlementState (공유 상태)"]
        direction TB
        s1[raw_input: str]
        s2[parsed_json: dict]
        s3[strategy: str]
        s4[calculation_result: dict]
        s5[feedback_history: list]
        s6[calc_explanation: str]
        s7[final_report: str]
        s8[safety_error: str]
    end

    input_parsing["InputParsingNode"] -- "parsed_json 세팅" --> s2
    feedback_parsing["FeedbackParsingNode"] -- "parsed_json 수정\nfeedback_history 추가" --> s2 & s5
    safety_check["SafetyCheckNode"] -- "safety_error 세팅" --> s8
    route_request["RouteRequestNode"] -- "strategy 세팅" --> s3
    calculation["CalculationNode"] -- "calculation_result 세팅" --> s4
    report_generation["ReportGenerationNode"] -- "calc_explanation\nfinal_report 세팅" --> s6 & s7
```

---

## 노드별 역할 요약

| 노드 | LLM | 입력 | 출력 |
|------|-----|------|------|
| **InputParsingNode** | ✅ Upstage Solar | `raw_input` (자연어) | `parsed_json` (구조화 JSON + rate) |
| **SafetyCheckNode** | ❌ 순수 Python | `parsed_json` | `safety_error` (오류 시 문자열) |
| **RouteRequestNode** | ✅ Upstage Solar | `raw_input` + `parsed_json` | `strategy` (SIMPLE / EXCEPTION) |
| **CalculationNode** | ❌ calculator/ 호출 | `parsed_json` | `calculation_result` (최종 금액) |
| **ReportGenerationNode** | ✅ Upstage Solar | `calculation_result` + `raw_input` | `calc_explanation` + `final_report` |
| **FeedbackParsingNode** | ✅ Upstage Solar | `raw_input` + `parsed_json` + `feedback_history` | `parsed_json` (수정됨) |

---

## 설계 원칙

- **LLM 담당**: 자연어 파싱, rate 결정(0.0~1.0), 전략 분기, 설명 생성
- **calculator/ 담당**: rate → 실제 금액 환산, 30% 하한선 적용, 총액 검증
- **피드백 루프**: `FeedbackParsingNode` → `SafetyCheckNode` → `RouteRequestNode` → `CalculationNode` → `ReportGenerationNode`
- **HTTP 통신 없음**: `front/` → `ai/` → `calculator/` 순서로 직접 import 호출
