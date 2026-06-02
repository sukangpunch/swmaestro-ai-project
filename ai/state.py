from typing import TypedDict


class SettlementState(TypedDict, total=False):
    raw_input: str
    payer: str          # 전액 결제자 이름 (프론트에서 입력, 없으면 빈 문자열)
    parsed_json: dict
    strategy: str
    calculation_result: dict
    feedback_history: list
    final_report: str
    safety_error: str
