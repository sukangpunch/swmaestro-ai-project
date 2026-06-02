# AI 정산 비서 — 서비스 동작 흐름 완전 해설

초보자도 이해할 수 있도록, 사용자가 문장을 입력하는 순간부터 결과가 화면에 나타나기까지의 전 과정을 단계별로 설명합니다.

---

## 전체 구조 한눈에 보기

```
사용자가 텍스트 입력
        │
        ▼
┌─────────────────────┐
│      front/         │  Streamlit 화면
│      app.py         │  - 입력 받기
│                     │  - 결과 렌더링
└────────┬────────────┘
         │  graph.invoke(state) 직접 호출 (HTTP 없음)
         ▼
┌─────────────────────────────────────────────────────┐
│                     ai/                             │
│              LangGraph 워크플로우                    │
│                                                     │
│  [입력 파싱] → [안전 검사] → [전략 결정]             │
│                                  │                  │
│                                  ▼                  │
│                           [계산 호출] ──────────────┼──┐
│                                  │                  │  │
│                                  ▼                  │  │
│                           [결과 설명 생성]           │  │
└─────────────────────────────────┬───────────────────┘  │
                                  │                       │ 직접 import
                                  │           ┌───────────┘
                                  │           ▼
                                  │  ┌─────────────────┐
                                  │  │  calculator/    │
                                  │  │  engine.py      │
                                  │  │  - 순수 계산만  │
                                  │  └────────┬────────┘
                                  │           │ 계산 결과 반환
                                  │           └───────────┐
                                  ◄──────────────────────┘
                                  │
                        최종 State 반환
                                  │
                                  ▼
                           front/ 화면에 표시
```

세 폴더는 **같은 Python 프로세스** 안에서 실행됩니다. 네트워크 통신(HTTP)이 전혀 없고, 함수 호출처럼 직접 import해서 사용합니다.

---

## 1단계: front/ — 사용자 입력 받기

**파일:** `front/app.py`

### 사용자가 하는 일

1. 텍스트 영역에 정산 상황을 자연어로 입력합니다.
   - 예: `"총 8만원이고 A, B, C, D 있어. 주류 3만원 / 안주 5만원. C는 늦게 왔고 D는 술 안 마셨어."`
2. "전송 →" 버튼을 누릅니다.

### 내부에서 일어나는 일

```python
# app.py의 send handler
if send_clicked and draft.strip():
    with st.spinner("AI가 분석 중입니다..."):
        result = _invoke_graph(draft.strip(), "")
```

`_invoke_graph()` 함수가 핵심입니다. 이 함수는 두 가지 경우를 구분합니다:

```python
def _invoke_graph(prompt: str, payer: str) -> dict:
    prev = next(
        (m for m in reversed(st.session_state.messages) if m["role"] == "assistant"),
        None,
    )

    if prev and prev.get("parsed_json", {}).get("participants"):
        # 이전 대화가 있으면 → 피드백(재계산) 모드
        result = graph.invoke({
            "raw_input": prompt,
            "parsed_json": prev["parsed_json"],  # 기존 정산 정보를 넘겨줌
            "feedback_history": prev.get("feedback_history") or [],
        })
    else:
        # 처음 입력이면 → 초기 정산 모드
        result = graph.invoke({
            "raw_input": prompt,
            "feedback_history": [],
        })
```

> **핵심:** `graph.invoke()`를 호출하는 순간, 제어권이 `ai/` 폴더로 넘어갑니다.
> `graph`는 `ai/graph.py`에서 만든 LangGraph 워크플로우 객체입니다.

---

## 2단계: ai/ — LangGraph 워크플로우

**파일:** `ai/graph.py`, `ai/nodes.py`, `ai/state.py`

### State(상태) 객체란?

LangGraph는 **State**라는 공유 딕셔너리를 들고 노드들을 하나씩 통과합니다.
각 노드는 State를 읽고, 자기 역할에 해당하는 필드를 추가하거나 수정합니다.

```python
# ai/state.py
class SettlementState(TypedDict, total=False):
    raw_input         : str   # 사용자가 입력한 원문 텍스트
    payer             : str   # 전액 결제자 이름
    parsed_json       : dict  # LLM이 파싱한 구조화 JSON
    strategy          : str   # SIMPLE / EXCEPTION / SPONSOR
    calculation_result: dict  # calculator/가 계산한 결과
    feedback_history  : list  # 이전 피드백 이력
    final_report      : str   # LLM이 생성한 설명 텍스트
    safety_error      : str   # 오류 메시지 (없으면 빈 문자열)
```

State는 여행 가방처럼 생각하면 됩니다. 각 노드가 자기 짐(데이터)을 하나씩 넣으면서 다음 노드로 전달합니다.

### 워크플로우 분기

```
START
  │
  ├─ parsed_json에 이미 참여자 정보 있음? ──→ [feedback_parsing 노드]
  │                                                    │
  └─ 없음 (첫 요청) ──→ [input_parsing 노드]           │
                               │                       │
                               ▼                       │
                        [safety_check 노드]             │
                               │                       │
                  오류 있음? ──┤                        │
                  │           └─ 없음 ──→ [route_request 노드]
                  ▼                              │      │
                 END                             │      │
                                                 ▼      ▼
                                          [calculation 노드] ←─┘
                                                 │
                                                 ▼
                                       [report_generation 노드]
                                                 │
                                                 ▼
                                                END
```

### 노드 1: input_parsing_node — 자연어 → 구조화 JSON

**하는 일:** 사용자가 입력한 문장을 LLM(Upstage Solar)에 보내서 구조화된 JSON으로 파싱합니다.

```python
def input_parsing_node(state: SettlementState) -> dict:
    content = _call_llm(_INPUT_PARSING_SYSTEM, state["raw_input"])
    parsed = _extract_json(content)
    parsed = _post_validate_exceptions(parsed)  # LLM 오류 보정
    return {"parsed_json": parsed}
```

LLM에게 주는 지시(System Prompt):

- "참여자, 총 금액, 비용 항목, 예외 조건을 JSON으로 추출하라"
- "술 미섭취 → `discount_rate: 1.0` (전액 제외)"
- "지각 → `surcharge_rate` (할증, 비율 언급 없으면 null)"
- "산술 계산은 하지 말고, 비율(rate)만 결정하라"

**입력 예시:**

```
"총 8만원이고 A, B, C, D 있어. 주류 3만원 / 안주 5만원. C는 늦게 왔고 D는 술 안 마셨어."
```

**LLM이 반환하는 JSON:**

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
    {
      "name": "C",
      "exceptions": [
        {
          "type": "늦은 도착",
          "target_items": ["주류", "안주"],
          "surcharge_rate": 0.2
        }
      ]
    },
    {
      "name": "D",
      "exceptions": [
        { "type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0 }
      ]
    }
  ]
}
```

> **LLM의 역할은 여기서 끝납니다.** "D는 주류에서 얼마를 빼야 하나?"라는 계산은 LLM이 하지 않습니다.
> LLM은 오직 **"D는 주류에서 100% 제외해야 한다(1.0)"**는 비율 결정만 합니다.

**`_post_validate_exceptions` 보정 함수:**
LLM이 지각을 잘못 분류하는 경우를 코드로 강제 수정합니다.

- "늦은 도착"인데 `discount_rate`를 썼으면 → `surcharge_rate`로 강제 교체
- 할증 비율이 없으면 `null` 유지 (다음 노드에서 오류 감지)

### 노드 2: safety_check_node — 입력값 검증

**하는 일:** 파싱된 JSON에 논리적 오류가 없는지 확인합니다.

```python
def safety_check_node(state: SettlementState) -> dict:
    pj = state.get("parsed_json", {})

    # 필수 정보 누락 확인
    if not pj.get("total_amount") or not pj.get("participants"):
        return {"safety_error": "total_amount 또는 participants 정보가 누락되었습니다."}

    # 항목 합계 ≠ 총액 확인
    items_sum = sum(i["amount"] for i in pj.get("items", []))
    if items_sum and abs(items_sum - pj["total_amount"]) > 1:
        return {"safety_error": f"항목 합계({items_sum:,}원)가 총액과 다릅니다."}

    # 지각인데 할증 비율 미지정 확인
    for p in pj.get("participants", []):
        for exc in p.get("exceptions", []):
            if "surcharge_rate" in exc and exc["surcharge_rate"] is None:
                return {"safety_error": f"{p['name']}의 지각 비율이 지정되지 않았습니다."}

    return {"safety_error": ""}  # 오류 없음
```

오류가 있으면 워크플로우가 즉시 종료되고, front/에서 경고 메시지를 표시합니다.

### 노드 3: route_request_node — 전략 결정

**하는 일:** 이 정산이 어떤 방식인지 판단합니다.

```python
def route_request_node(state: SettlementState) -> dict:
    user = f"원문: {state['raw_input']}\n파싱 결과: {json.dumps(state['parsed_json'])}"
    content = _call_llm(_ROUTE_REQUEST_SYSTEM, user)
    result = _extract_json(content)
    return {"strategy": result.get("strategy", "SIMPLE")}
```

LLM에게 주는 지시:

- 선결제자 있음 → `SPONSOR`
- 예외 조건 하나라도 있음 → `EXCEPTION`
- 그 외 → `SIMPLE`

**결과:** `state["strategy"] = "EXCEPTION"`

### 노드 4: calculation_node — 계산기 호출

**하는 일:** calculator/의 `calculate()` 함수를 직접 호출합니다.

```python
def calculation_node(state: SettlementState) -> dict:
    result = calculate(state["parsed_json"])  # calculator/engine.py 직접 호출
    return {"calculation_result": result}
```

딱 두 줄입니다. ai/ 노드는 **계산을 직접 하지 않고**, calculator/에 통째로 위임합니다.

### 노드 5: report_generation_node — 설명 생성

**하는 일:** 계산 결과를 바탕으로 사람이 읽기 좋은 설명과 공유용 메시지를 생성합니다.

```python
def report_generation_node(state: SettlementState) -> dict:
    context = (
        f"정산 전략: {state['strategy']}\n"
        f"원문: {state['raw_input']}\n"
        f"정산 입력: {json.dumps(state['parsed_json'])}\n"
        f"계산 결과: {json.dumps(state['calculation_result'])}"
    )
    content = _call_llm(_REPORT_GENERATION_SYSTEM, context, temperature=0.3)
    return {"final_report": content}
```

LLM이 생성하는 텍스트 예시:

```
D는 주류를 마시지 않아 주류 비용에서 제외되었습니다.
C는 늦게 도착하여 할증이 적용되었습니다.

--- 공유용 정산 메시지 ---
A: 22,500원
B: 22,500원
C: 24,750원 (지각 할증)
D: 10,250원 (주류 제외)
```

---

## 3단계: calculator/ — 순수 계산 엔진

**파일:** `calculator/engine.py`

LLM이나 웹 서버와 완전히 독립된 **순수 Python 함수**입니다.
`parsed_json`을 받아서 각 참여자의 정확한 금액을 계산해 반환합니다.

### 계산 5단계

위 예시(총 8만원, A/B/C/D, 주류 3만/안주 5만, C 지각 20%, D 주류 제외)를 따라가 봅니다.

---

#### Step 1: 항목별 기본 분할

주류(30,000원) 계산:

- D는 `discount_rate: 1.0` → 완전 제외
- 남은 참여자: A, B, C → 1인당 30,000 ÷ 3 = **10,000원**

안주(50,000원) 계산:

- 예외 없음 → A, B, C, D 모두 부담
- 1인당 50,000 ÷ 4 = **12,500원**

기본 합산 결과:

```
A: 10,000 + 12,500 = 22,500원
B: 10,000 + 12,500 = 22,500원
C: 10,000 + 12,500 = 22,500원
D:       0 + 12,500 = 12,500원
```

---

#### Step 2: 할증(surcharge) 적용

C의 지각: `surcharge_rate: 0.2`

- C의 현재 금액 22,500원의 20% = **4,500원 추가 부담**
- C: 22,500 + 4,500 = **27,000원**
- 이 4,500원을 나머지 A, B, D에게 1,500원씩 차감:
  - A: 22,500 - 1,500 = **21,000원**
  - B: 22,500 - 1,500 = **21,000원**
  - D: 12,500 - 1,500 = **11,000원**

---

#### Step 3: 하한선 적용 (균등 분담액의 30%)

균등 분담액 = 80,000 ÷ 4 = 20,000원
하한선 = 20,000 × 0.3 = **6,000원**

현재 D는 11,000원 → 하한선(6,000원) 초과 → 그대로 유지

> 만약 D가 6,000원 미만이었다면 강제로 6,000원으로 올리고,
> 부족분을 다른 참여자에게 분배합니다.

---

#### Step 4: 선결제 반영 (해당 없으면 skip)

선결제자가 있을 때만 적용됩니다.
예) A가 8만원 전액 결제했다면:

- A의 final_amount = 21,000 - 80,000 = **-59,000원** (수령 예정)
- 나머지는 A에게 각자 금액을 이체

---

#### Step 5: 반올림 및 총액 검증

소수점이 생겼다면 반올림 후 차이(1~2원)를 가장 소수점 부분이 큰 사람에게 보정합니다.

최종 합계가 총액(80,000원)과 일치하는지 검증합니다.

**최종 반환값:**

```python
{
    "participants": [
        {"name": "A", "final_amount": 21000, "breakdown": {"base": 20000}},
        {"name": "B", "final_amount": 21000, "breakdown": {"base": 20000}},
        {"name": "C", "final_amount": 27000, "breakdown": {"base": 20000}},
        {"name": "D", "final_amount": 11000, "breakdown": {"base": 20000}},
    ],
    "total_verified": True,
    "floor_applied": [],
    "rounding_adjusted": None,
}
```

---

## 4단계: 결과가 front/로 돌아오는 과정

`graph.invoke()`가 완료되면 최종 State 딕셔너리가 `_invoke_graph()`로 반환됩니다.

```python
# front/app.py
result = _invoke_graph(draft.strip(), "")  # ← 여기서 전체 State가 반환됨

# messages에 저장
st.session_state.messages.append({"role": "user", "content": draft.strip()})
st.session_state.messages.append({"role": "assistant", **result})

st.rerun()  # 화면 다시 그리기
```

`st.rerun()` 호출 후 화면을 다시 그리면서 `_render_result(msg)`가 실행됩니다:

```python
def _render_result(msg: dict) -> None:
    cr = msg.get("calculation_result")       # calculator/ 결과
    strategy = msg.get("strategy", "SIMPLE") # 전략 배지 표시
    final_report = msg.get("final_report")   # LLM 설명 텍스트

    # 전략 배지 표시 (균등 분배 / 예외 조건 반영 / 선결제 포함)
    st.markdown(f'<span class="strategy-badge ...">{badge_label}</span>')

    # 참여자별 카드 표시
    for p in cr["participants"]:
        _card(p["name"], f"{p['final_amount']:,}원", ...)

    # 계산 근거 + 공유 메시지
    with st.expander("📋 계산 근거 보기"):
        st.write(final_report)
    st.code(final_report)
```

---

## 5단계: 피드백 (재계산) 흐름

사용자가 결과를 보고 "D도 사실 좀 늦게 왔어"라고 추가 입력하면:

```
front/ → _invoke_graph() 호출 시
         기존 parsed_json + 새 raw_input을 State에 담아 전달
         │
         ▼
ai/ graph → START → parsed_json에 참여자 있음 감지
         │
         ▼
  [feedback_parsing 노드]
  - 기존 parsed_json을 덮어쓰지 않고
  - 새로 말한 조건(D 지각)만 추가
  - 수정된 parsed_json 반환
         │
         ▼
  [calculation 노드] (바로 재계산, 파싱/안전검사/전략결정 생략)
         │
         ▼
  [report_generation 노드]
         │
         ▼
  front/ 화면 업데이트
```

피드백 시에는 **InputParsing → SafetyCheck → RouteRequest를 건너뜁니다.**
기존 정산 정보를 유지하면서 변경된 조건만 반영하기 때문입니다.

---

## 전체 흐름 요약표

| 단계           | 담당                                   | 역할                                | LLM 사용? |
| -------------- | -------------------------------------- | ----------------------------------- | --------- |
| 1. 입력 수신   | `front/app.py`                         | 텍스트 입력 → `graph.invoke()` 호출 | ❌        |
| 2. 자연어 파싱 | `ai/nodes.py` `input_parsing_node`     | 문장 → JSON + rate 결정             | ✅        |
| 3. 안전 검사   | `ai/nodes.py` `safety_check_node`      | 누락/모순 감지                      | ❌        |
| 4. 전략 결정   | `ai/nodes.py` `route_request_node`     | SIMPLE/EXCEPTION/SPONSOR 분류       | ✅        |
| 5. 계산        | `calculator/engine.py` `calculate()`   | rate → 실제 금액 환산               | ❌        |
| 6. 설명 생성   | `ai/nodes.py` `report_generation_node` | 결과 → 자연어 설명                  | ✅        |
| 7. 결과 표시   | `front/app.py` `_render_result()`      | 카드/배지/공유메시지 렌더링         | ❌        |

**LLM이 하는 것:** 자연어 이해, 비율(rate) 판단, 전략 분류, 설명 생성  
**LLM이 절대 안 하는 것:** 덧셈/뺄셈/나눗셈 — 모든 산술은 `calculator/engine.py`에서만
