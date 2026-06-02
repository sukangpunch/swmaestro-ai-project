import copy


def _validate(parsed_json: dict) -> None:
    if "total_amount" not in parsed_json:
        raise ValueError("total_amount is required")
    if "participants" not in parsed_json or not parsed_json["participants"]:
        raise ValueError("participants is required")

    items = parsed_json.get("items", [])
    if items:
        items_sum = sum(item["amount"] for item in items)
        if items_sum != parsed_json["total_amount"]:
            raise ValueError(
                f"총액 불일치: items 합계({items_sum}) ≠ total_amount({parsed_json['total_amount']})"
            )

    for p in parsed_json["participants"]:
        for exc in p.get("exceptions", []):
            for key in ("discount_rate", "surcharge_rate"):
                if key in exc:
                    rate = exc[key]
                    if not (0.0 <= rate <= 1.0):
                        raise ValueError(f"{key} {rate}가 유효 범위(0.0~1.0)를 벗어남")


def calculate(parsed_json: dict) -> dict:
    _validate(parsed_json)

    total_amount = parsed_json["total_amount"]
    participants = parsed_json["participants"]
    items = parsed_json.get("items", [])
    sponsor = parsed_json.get("sponsor")
    N = len(participants)

    amounts = {p["name"]: 0.0 for p in participants}

    # ── Step 1: 항목별 실참여자 기준 비용 분할 ──
    if items:
        for item in items:
            item_name = item["name"]
            item_amount = item["amount"]

            # discount_rate=1.0 → 완전 제외, 0 < rate < 1 → 부분 감액
            excluded = set()
            partial_discounts = {}

            for p in participants:
                for exc in p.get("exceptions", []):
                    if item_name in exc.get("target_items", []) and "discount_rate" in exc:
                        rate = exc["discount_rate"]
                        if rate >= 1.0:
                            excluded.add(p["name"])
                        else:
                            partial_discounts[p["name"]] = rate

            eligible = [p for p in participants if p["name"] not in excluded]
            if not eligible:
                continue

            per_person = item_amount / len(eligible)

            for p in eligible:
                discount = partial_discounts.get(p["name"], 0.0)
                amounts[p["name"]] += per_person * (1 - discount)

            # 부분 감액분을 감액 없는 eligible 참여자에게 재분배
            total_discount_amount = sum(
                per_person * rate
                for name, rate in partial_discounts.items()
                if name not in excluded
            )
            non_discounted = [p for p in eligible if p["name"] not in partial_discounts]
            if total_discount_amount > 0 and non_discounted:
                redistribute = total_discount_amount / len(non_discounted)
                for p in non_discounted:
                    amounts[p["name"]] += redistribute
    else:
        # items 미제공 시 균등 분할
        base = total_amount / N
        amounts = {p["name"]: base for p in participants}

    # ── Step 2: 할증(surcharge) 적용 ──
    for p in participants:
        for exc in p.get("exceptions", []):
            if "surcharge_rate" in exc:
                surcharge_rate = exc["surcharge_rate"]
                surcharge_amount = amounts[p["name"]] * surcharge_rate
                amounts[p["name"]] += surcharge_amount

                # 할증 잉여분을 다른 참여자에게 균등 차감
                others = [q for q in participants if q["name"] != p["name"]]
                if others:
                    deduction = surcharge_amount / len(others)
                    for o in others:
                        amounts[o["name"]] -= deduction

    # ── Step 3: 하한선 적용 (균등 분담액의 30%) ──
    base = total_amount / N
    floor = base * 0.3
    floor_applied = []
    total_floor_extra = 0.0

    for p in participants:
        name = p["name"]
        if amounts[name] < floor:
            total_floor_extra += floor - amounts[name]
            amounts[name] = floor
            floor_applied.append(name)

    if total_floor_extra > 0:
        non_floored = [p["name"] for p in participants if p["name"] not in floor_applied]
        if non_floored:
            total_non_floored = sum(amounts[n] for n in non_floored)
            for n in non_floored:
                if total_non_floored > 0:
                    amounts[n] -= total_floor_extra * amounts[n] / total_non_floored
                else:
                    amounts[n] -= total_floor_extra / len(non_floored)

    # ── Step 4: 선결제 반영 ──
    if sponsor:
        amounts[sponsor["name"]] -= sponsor["prepaid"]

    # ── Step 5: 반올림 및 총액 검증 ──
    int_amounts = {p["name"]: round(amounts[p["name"]]) for p in participants}
    expected_total = total_amount - (sponsor["prepaid"] if sponsor else 0)

    diff = expected_total - sum(int_amounts.values())
    rounding_adjusted = None
    if diff != 0:
        fracs = {p["name"]: amounts[p["name"]] - int(amounts[p["name"]]) for p in participants}
        adj = (
            max(fracs, key=lambda n: fracs[n])
            if diff > 0
            else min(fracs, key=lambda n: fracs[n])
        )
        int_amounts[adj] += diff
        rounding_adjusted = adj

    total_verified = sum(int_amounts.values()) == expected_total

    # ── 결과 조립 ──
    participants_out = []
    for p in participants:
        name = p["name"]
        participants_out.append({
            "name": name,
            "final_amount": int_amounts[name],
            "breakdown": {
                "base": int(base),
            },
        })

    return {
        "participants": participants_out,
        "total_verified": total_verified,
        "floor_applied": floor_applied,
        "rounding_adjusted": rounding_adjusted,
    }


def recalculate(parsed_json: dict, feedback_json: dict) -> dict:
    modified = copy.deepcopy(parsed_json)

    target_name = feedback_json["name"]
    additional_exc = feedback_json.get("additional_exception")

    if additional_exc:
        for p in modified["participants"]:
            if p["name"] == target_name:
                p.setdefault("exceptions", []).append(additional_exc)
                break

    return calculate(modified)