import pytest
from calculator.engine import calculate, recalculate


# ── 입력 검증 ──────────────────────────────────────────────────────────────

def test_missing_total_amount_raises():
    with pytest.raises(ValueError):
        calculate({"participants": [{"name": "A", "exceptions": []}]})


def test_missing_participants_raises():
    with pytest.raises(ValueError):
        calculate({"total_amount": 80000})


def test_items_sum_mismatch_raises():
    with pytest.raises(ValueError, match="총액"):
        calculate({
            "total_amount": 80000,
            "items": [{"name": "주류", "amount": 30000}, {"name": "안주", "amount": 40000}],  # 70000 ≠ 80000
            "participants": [{"name": "A", "exceptions": []}],
        })


def test_invalid_discount_rate_raises():
    with pytest.raises(ValueError):
        calculate({
            "total_amount": 10000,
            "participants": [
                {"name": "A", "exceptions": []},
                {"name": "B", "exceptions": [
                    {"type": "test", "target_items": ["주류"], "discount_rate": 1.5}
                ]},
            ],
        })


# ── 기본 균등 분배 ─────────────────────────────────────────────────────────

def test_equal_split_no_exceptions():
    result = calculate({
        "total_amount": 80000,
        "items": [{"name": "주류", "amount": 30000}, {"name": "안주", "amount": 50000}],
        "participants": [
            {"name": "A", "exceptions": []},
            {"name": "B", "exceptions": []},
            {"name": "C", "exceptions": []},
            {"name": "D", "exceptions": []},
        ],
    })
    assert result["total_verified"] is True
    amounts = {p["name"]: p["final_amount"] for p in result["participants"]}
    assert all(amt == 20000 for amt in amounts.values())


# ── 예외 조건 및 재분배 ────────────────────────────────────────────────────

def test_full_exclusion_redistributed():
    """D가 주류 전액 제외 → 감액분이 A, B에게 균등 재분배"""
    result = calculate({
        "total_amount": 60000,
        "items": [{"name": "주류", "amount": 30000}, {"name": "안주", "amount": 30000}],
        "participants": [
            {"name": "A", "exceptions": []},
            {"name": "B", "exceptions": []},
            {"name": "D", "exceptions": [
                {"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0}
            ]},
        ],
    })
    # base = 20,000 / D saves 30000*1.0/3 = 10,000 / A,B each +5,000
    amounts = {p["name"]: p["final_amount"] for p in result["participants"]}
    assert amounts["A"] == 25000
    assert amounts["B"] == 25000
    assert amounts["D"] == 10000
    assert result["total_verified"] is True


def test_partial_discount_redistributed():
    """C가 안주 30% 감액 → 감액분이 나머지에게 재분배"""
    result = calculate({
        "total_amount": 40000,
        "items": [{"name": "안주", "amount": 40000}],
        "participants": [
            {"name": "A", "exceptions": []},
            {"name": "B", "exceptions": []},
            {"name": "C", "exceptions": [
                {"type": "소량 섭취", "target_items": ["안주"], "discount_rate": 0.3}
            ]},
            {"name": "D", "exceptions": []},
        ],
    })
    # base = 10,000 / C saves 40000*0.3/4 = 3,000 / A,B,D each +1,000
    amounts = {p["name"]: p["final_amount"] for p in result["participants"]}
    assert amounts["C"] == 7000
    assert amounts["A"] == 11000
    assert result["total_verified"] is True


# ── 최소 부담 하한선 ───────────────────────────────────────────────────────

def test_floor_applied_when_discount_too_deep():
    """감액 후 균등분담액의 30% 미만이면 하한선 강제 적용"""
    result = calculate({
        "total_amount": 10000,
        "items": [{"name": "주류", "amount": 10000}],
        "participants": [
            {"name": "A", "exceptions": []},
            {"name": "B", "exceptions": [
                {"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0}
            ]},
        ],
    })
    # base = 5,000 / floor = 1,500 / B would pay 0 → clamped to 1,500
    amounts = {p["name"]: p["final_amount"] for p in result["participants"]}
    assert amounts["B"] >= 1500
    assert "B" in result["floor_applied"]
    assert result["total_verified"] is True


# ── 시나리오 A ─────────────────────────────────────────────────────────────

INPUT_A = {
    "total_amount": 80000,
    "items": [{"name": "주류", "amount": 30000}, {"name": "안주", "amount": 50000}],
    "participants": [
        {"name": "A", "exceptions": []},
        {"name": "B", "exceptions": []},
        {"name": "C", "exceptions": [
            {"type": "늦은 도착", "target_items": ["안주"], "discount_rate": 0.3}
        ]},
        {"name": "D", "exceptions": [
            {"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0}
        ]},
    ],
}


def test_scenario_a_total_verified():
    result = calculate(INPUT_A)
    assert result["total_verified"] is True
    assert sum(p["final_amount"] for p in result["participants"]) == 80000


def test_scenario_a_amounts():
    result = calculate(INPUT_A)
    amounts = {p["name"]: p["final_amount"] for p in result["participants"]}
    # A,B 예외 없음 → 동일
    assert amounts["A"] == amounts["B"]
    # D(주류 제외) < A / C(안주 감액) < A
    assert amounts["D"] < amounts["A"]
    assert amounts["C"] < amounts["A"]


def test_scenario_a_exact_amounts():
    result = calculate(INPUT_A)
    amounts = {p["name"]: p["final_amount"] for p in result["participants"]}
    # A,B: 23,750 / C: 18,750 / D: 13,750
    assert amounts["A"] == 23750
    assert amounts["B"] == 23750
    assert amounts["C"] == 18750
    assert amounts["D"] == 13750


# ── 시나리오 B (선결제) ────────────────────────────────────────────────────

INPUT_B = {
    "total_amount": 120000,
    "items": [
        {"name": "주류", "amount": 50000},
        {"name": "안주", "amount": 50000},
        {"name": "공통비", "amount": 20000},
    ],
    "sponsor": {"name": "A", "prepaid": 50000},
    "participants": [
        {"name": "A", "exceptions": []},
        {"name": "B", "exceptions": []},
        {"name": "C", "exceptions": []},
        {"name": "D", "exceptions": [
            {"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0}
        ]},
        {"name": "E", "exceptions": [
            {"type": "늦은 도착 + 소량 섭취", "target_items": ["안주"], "discount_rate": 0.5}
        ]},
    ],
}


def test_scenario_b_total_verified():
    result = calculate(INPUT_B)
    assert result["total_verified"] is True


def test_scenario_b_sponsor_accounting():
    """선결제 반영 후 final_amount 합계 + 선결제 = 총액"""
    result = calculate(INPUT_B)
    total_collected = sum(p["final_amount"] for p in result["participants"])
    assert total_collected + 50000 == 120000


# ── 시나리오 C (피드백 재계산) ─────────────────────────────────────────────

def test_scenario_c_total_verified():
    feedback = {
        "name": "D",
        "additional_exception": {"type": "소량 섭취", "target_items": ["안주"], "discount_rate": 0.5},
    }
    result = recalculate(INPUT_B, feedback)
    assert result["total_verified"] is True


def test_scenario_c_d_pays_less_than_b():
    """피드백 추가 후 D의 부담이 줄어야 한다"""
    feedback = {
        "name": "D",
        "additional_exception": {"type": "소량 섭취", "target_items": ["안주"], "discount_rate": 0.5},
    }
    result_b = calculate(INPUT_B)
    result_c = recalculate(INPUT_B, feedback)
    d_b = next(p["final_amount"] for p in result_b["participants"] if p["name"] == "D")
    d_c = next(p["final_amount"] for p in result_c["participants"] if p["name"] == "D")
    assert d_c < d_b
