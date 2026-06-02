import json
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

from calculator.engine import calculate
from ai.state import SettlementState

load_dotenv()

_client = OpenAI(
    api_key=os.getenv("UPSTAGE_API_KEY"),
    base_url="https://api.upstage.ai/v1",
)
_MODEL = "solar-pro"

_INPUT_PARSING_SYSTEM = """당신은 정산 데이터 파서입니다.
산술 계산은 수행하지 말고, 다음을 수행하라:
1. 참여자, 총 금액, 비용 항목, 예외 조건을 JSON으로 추출하라.
2. 각 예외 조건을 아래 규칙에 따라 반드시 정확히 분류하라.

   [감액 조건] 실제로 소비를 덜 한 경우 → discount_rate 사용 (본인 부담 감소)
   - 술 미섭취: 주류 항목 discount_rate 1.0
   - 소량 섭취: discount_rate 0.3~0.5
   - 중도 귀가 (절반 이상 자리 비움): 모든 항목 discount_rate 0.5

   [할증 조건] 패널티를 부과하는 경우 → surcharge_rate 사용 (본인 부담 증가)
   - 지각/늦은 도착: 사용자가 비율이나 금액을 명시한 경우에만 surcharge_rate에 해당 값을 넣어라.
     사용자가 비율을 명시하지 않은 경우 surcharge_rate를 null로 설정하라.
     예) "지각자는 20% 더 내기로 했어" → surcharge_rate: 0.2
     예) "C는 늦게 왔어" (비율 언급 없음) → surcharge_rate: null

반드시 유효한 JSON만 반환하라. 설명 없이 JSON만 출력하라.

출력 형식 예시 (지각 C, 술 미섭취 D):
{
  "total_amount": 80000,
  "items": [{"name": "주류", "amount": 30000}, {"name": "안주", "amount": 50000}],
  "participants": [
    {"name": "A", "exceptions": []},
    {"name": "B", "exceptions": []},
    {"name": "C", "exceptions": [{"type": "늦은 도착", "target_items": ["주류", "안주"], "surcharge_rate": 0.2}]},
    {"name": "D", "exceptions": [{"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0}]}
  ]
}"""

_ROUTE_REQUEST_SYSTEM = """정산 전략을 결정하라. 단어 기반이 아닌 문맥 기반으로 판단하라.
규칙:
- 선결제자가 있으면 SPONSOR
- 예외 조건(지각, 술 미섭취, 중도 귀가, 소량 섭취 등)이 하나라도 있으면 EXCEPTION
- 그 외에는 SIMPLE

반드시 다음 JSON만 반환하라. 설명 없이 JSON만 출력하라:
{"strategy": "SIMPLE" | "EXCEPTION" | "SPONSOR"}"""

_REPORT_GENERATION_SYSTEM = """정산 결과를 친절하게 설명하라.
규칙:
- 특정 참여자를 비난하지 말고, 항목 참여 여부와 감액 이유를 기준으로 중립적으로 설명하라
- 각 참여자가 얼마를 내야 하는지 명확히 안내하라
- 마지막에 복사용 공유 메시지를 별도로 제공하라 (--- 구분선 사용)"""

_FEEDBACK_PARSING_SYSTEM = """기존 정산 정보에 피드백을 반영하라.
규칙:
- 기존 parsed_json을 임의로 덮어쓰지 말라
- 사용자가 새로 말한 조건만 추가 또는 수정하라
- 산술 계산은 수행하지 말고, 조건과 rate 변경 사항만 추출하라
- 감액 조건(소비 덜 함)은 discount_rate, 할증 조건(지각 등 패널티)은 surcharge_rate 사용
- 모든 rate는 0.0~1.0 범위로 결정하라
- 수정된 전체 parsed_json을 반환하라. 설명 없이 JSON만 출력하라."""


def _call_llm(system: str, user: str, temperature: float = 0) -> str:
    response = _client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )
    return response.choices[0].message.content


def _extract_json(text: str) -> dict:
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    return json.loads(text.strip())


def _inject_payer(parsed: dict, payer: str) -> dict:
    """결제자가 참여자 목록에 있으면 sponsor(전액 선결제)로 주입한다."""
    if not payer:
        return parsed
    names = [p["name"] for p in parsed.get("participants", [])]
    if payer not in names:
        return parsed
    return {**parsed, "sponsor": {"name": payer, "prepaid": parsed.get("total_amount", 0)}}


def _post_validate_exceptions(parsed: dict) -> dict:
    """LLM이 지각/늦은 도착을 discount_rate로 잘못 분류하는 케이스를 코드 레벨에서 강제 보정한다.

    규칙:
    - 지각/늦은 도착 타입 → 반드시 surcharge_rate 사용
    - target_items → 해당 정산의 모든 항목으로 교정
    - discount_rate가 잘못 들어온 경우 surcharge_rate로 전환
    """
    SURCHARGE_KEYWORDS = {"지각", "늦은 도착", "늦게", "late", "지각비", "늦음", "늦은"}

    items = parsed.get("items", [])
    all_item_names = [item["name"] for item in items]

    for p in parsed.get("participants", []):
        for exc in p.get("exceptions", []):
            exc_type = exc.get("type", "")
            if any(kw in exc_type for kw in SURCHARGE_KEYWORDS):
                # discount_rate → surcharge_rate 강제 교정
                if "discount_rate" in exc:
                    exc["surcharge_rate"] = exc.pop("discount_rate")
                # surcharge_rate 미존재 시 기본값 주입
                if "surcharge_rate" not in exc:
                    exc["surcharge_rate"] = None
                # target_items를 모든 항목으로 교정
                if all_item_names:
                    exc["target_items"] = all_item_names

    return parsed


def input_parsing_node(state: SettlementState) -> dict:
    content = _call_llm(_INPUT_PARSING_SYSTEM, state["raw_input"])
    parsed = _extract_json(content)
    parsed = _post_validate_exceptions(parsed)
    payer = (state.get("payer") or "").strip()
    return {"parsed_json": _inject_payer(parsed, payer)}


def safety_check_node(state: SettlementState) -> dict:
    pj = state.get("parsed_json", {})
    if not pj.get("total_amount") or not pj.get("participants"):
        return {"safety_error": "total_amount 또는 participants 정보가 누락되었습니다."}

    items_sum = sum(i["amount"] for i in pj.get("items", []))
    if items_sum and abs(items_sum - pj["total_amount"]) > 1:
        return {
            "safety_error": (
                f"항목 합계({items_sum:,}원)가 총액({pj['total_amount']:,}원)과 일치하지 않습니다."
            )
        }

    # ── 할증 비율 미지정 감지 ──
    missing_rate_names = []
    for p in pj.get("participants", []):
        for exc in p.get("exceptions", []):
            if "surcharge_rate" in exc and exc["surcharge_rate"] is None:
                missing_rate_names.append(p["name"])

    if missing_rate_names:
        names = ", ".join(missing_rate_names)
        return {
            "safety_error": (
                f"{names}에 대한 할증(지각 등) 비율이 지정되지 않았습니다.\n"
                f"예) \"지각자는 20% 더 내기로 했어\" 또는 \"지각비 5000원\" 형태로 알려주세요."
            )
        }

    return {"safety_error": ""}


def route_request_node(state: SettlementState) -> dict:
    user = (
        f"원문: {state['raw_input']}\n"
        f"파싱 결과: {json.dumps(state.get('parsed_json', {}), ensure_ascii=False)}"
    )
    content = _call_llm(_ROUTE_REQUEST_SYSTEM, user)
    result = _extract_json(content)
    return {"strategy": result.get("strategy", "SIMPLE")}


def calculation_node(state: SettlementState) -> dict:
    result = calculate(state["parsed_json"])
    return {"calculation_result": result}


def report_generation_node(state: SettlementState) -> dict:
    context = (
        f"정산 전략: {state.get('strategy', 'SIMPLE')}\n"
        f"원문: {state.get('raw_input', '')}\n"
        f"정산 입력: {json.dumps(state.get('parsed_json', {}), ensure_ascii=False)}\n"
        f"계산 결과: {json.dumps(state.get('calculation_result', {}), ensure_ascii=False)}"
    )
    content = _call_llm(_REPORT_GENERATION_SYSTEM, context, temperature=0.3)
    return {"final_report": content}


def feedback_parsing_node(state: SettlementState) -> dict:
    history = state.get("feedback_history") or []
    history_text = "\n".join(history) if history else "(없음)"
    context = (
        f"기존 정산 정보:\n{json.dumps(state.get('parsed_json', {}), ensure_ascii=False)}\n"
        f"이전 피드백 이력:\n{history_text}\n"
        f"새 피드백: {state['raw_input']}"
    )
    content = _call_llm(_FEEDBACK_PARSING_SYSTEM, context)
    updated_parsed = _extract_json(content)
    updated_parsed = _post_validate_exceptions(updated_parsed)
    payer = (state.get("payer") or "").strip()
    updated_history = list(history) + [state["raw_input"]]
    return {"parsed_json": _inject_payer(updated_parsed, payer), "feedback_history": updated_history}
