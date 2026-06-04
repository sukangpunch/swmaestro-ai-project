from typing import TypedDict


class SettlementState(TypedDict, total=False):
    raw_input: str
    parsed_json: dict
    strategy: str
    calculation_result: dict
    feedback_history: list
    calc_explanation: str
    final_report: str
    safety_error: str
