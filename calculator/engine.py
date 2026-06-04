import copy


def _validate(parsed_json: dict) -> None:
    if "total_amount" not in parsed_json:
        raise ValueError("total_amount is required")
    if "participants" not in parsed_json or not parsed_json["participants"]:
        raise ValueError("participants is required")

    # 중복 참여자 검증
    names = [p["name"] for p in parsed_json["participants"]]
    if len(names) != len(set(names)):
        dups = [n for n in set(names) if names.count(n) > 1]
        raise ValueError(f"중복된 참여자 이름: {', '.join(dups)}")

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
                    if rate is None:
                        raise ValueError(
                            f"{p['name']}의 {key}가 null입니다. "
                            "비율을 명시해 주세요 (예: '지각자는 20% 더 내기로 했어')"
                        )
                    if not (0.0 <= rate <= 1.0):
                        raise ValueError(f"{key} {rate}가 유효 범위(0.0~1.0)를 벗어남")
            if "surcharge_amount" in exc:
                amt = exc["surcharge_amount"]
                if amt is None:
                    raise ValueError(
                        f"{p['name']}의 surcharge_amount가 null입니다. "
                        "지각비 금액을 명시해 주세요 (예: '지각비 5000원')"
                    )
                if amt < 0:
                    raise ValueError(f"surcharge_amount {amt}는 0 이상이어야 합니다")


def _calc_step1(items: list, participants: list) -> tuple[dict, dict]:
    """Step 1: 항목별 eligible 기준 1인 부담액 계산 + discount_rate 감액 + 감액분 재분배

    Returns:
        amounts: 참여자별 누적 부담액
        discount_logs: 참여자별 감액 설명 문장 목록 (LLM 역산 방지용)
    """
    amounts = {p["name"]: 0.0 for p in participants}
    discount_logs: dict[str, list[str]] = {}

    for item in items:
        item_name = item["name"]
        item_amount = item["amount"]

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

        # 완전 제외자 로그
        for name in excluded:
            discount_logs.setdefault(name, []).append(
                f"{item_name}: 1인 몫 {round(item_amount / (len(eligible) + 1)):,}원 → 완전 제외 (0원)"
            )

        for p in eligible:
            discount = partial_discounts.get(p["name"], 0.0)
            amounts[p["name"]] += per_person * (1 - discount)

            if discount > 0:
                discounted_amt = per_person * discount
                final_amt = per_person * (1 - discount)
                discount_logs.setdefault(p["name"], []).append(
                    f"{item_name}: 1인 몫 {round(per_person):,}원 × (1-{discount}) = {round(final_amt):,}원"
                    f" (감액분 {round(discounted_amt):,}원)"
                )

        # 감액분은 소멸하지 않고 비감액 eligible 참여자에게 재분배
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

    return amounts, discount_logs


def _apply_steps_2_to_4(
    amounts: dict,
    participants: list,
    total_amount: int,
    discount_logs: dict | None = None,
) -> dict:
    """Step 2(할증) → Step 3(하한선) → Step 4(반올림/검증)"""
    N = len(participants)

    # Step 1 결과 스냅샷 — 할증 설명 시 LLM에 제공할 중간값
    step1_amounts = dict(amounts)

    # ── Step 2: 할증(surcharge) 적용 ──
    # 비할증자에게만 차감 분배. 전원 할증이면 본인 제외 전체에 분배
    surcharged_names = {
        p["name"] for p in participants
        if any("surcharge_rate" in e or "surcharge_amount" in e
               for e in p.get("exceptions", []))
    }
    # 수식 설명 로그 — Python이 미리 생성해 LLM 역산 오류 방지
    surcharge_logs: dict[str, list[str]] = {}
    surcharge_deductions: dict[str, dict] = {}

    for p in participants:
        for exc in p.get("exceptions", []):
            surcharge = 0.0
            s1 = step1_amounts[p["name"]]  # 할증 전 개인 부담액

            if "surcharge_rate" in exc:
                surcharge = s1 * exc["surcharge_rate"]
                surcharge_logs[p["name"]] = [
                    f"할증 전 부담액: {round(s1):,}원",
                    f"추가 부담: {round(s1):,} × {exc['surcharge_rate']} = {round(surcharge):,}원",
                    f"최종: {round(s1):,} + {round(surcharge):,} = {round(s1 + surcharge):,}원",
                ]
            elif "surcharge_amount" in exc:
                surcharge = float(exc["surcharge_amount"])
                surcharge_logs[p["name"]] = [
                    f"할증 전 부담액: {round(s1):,}원",
                    f"추가 부담(고정): {int(surcharge):,}원",
                    f"최종: {round(s1):,} + {int(surcharge):,} = {round(s1 + surcharge):,}원",
                ]

            if surcharge:
                amounts[p["name"]] += surcharge
                non_surcharged = [q for q in participants
                                  if q["name"] != p["name"]
                                  and q["name"] not in surcharged_names]
                targets = non_surcharged or [q for q in participants if q["name"] != p["name"]]
                if targets:
                    deduction = surcharge / len(targets)
                    for o in targets:
                        amounts[o["name"]] -= deduction
                    surcharge_deductions[p["name"]] = {
                        "targets": [o["name"] for o in targets],
                        "per_person": round(deduction),
                    }

    # ── Step 3: 하한선 적용 (균등 분담액의 30%) ──
    base = total_amount / N
    floor = base * 0.3
    floor_applied = []
    total_floor_extra = 0.0

    for p in participants:
        name = p["name"]
        if amounts[name] == 0.0:
            continue  # 전액 제외(discount_rate=1.0) → 하한선 미적용
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

    # ── Step 4: 반올림 및 총액 검증 ──
    int_amounts = {p["name"]: round(amounts[p["name"]]) for p in participants}
    diff = total_amount - sum(int_amounts.values())
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

    total_verified = sum(int_amounts.values()) == total_amount

    # ── 결과 조립 ──
    participants_out = []
    for p in participants:
        name = p["name"]
        participants_out.append({
            "name": name,
            "final_amount": int_amounts[name],
            "breakdown": {
                "base": int(base),
                "step1_amount": round(step1_amounts[name]),
            },
        })

    result = {
        "participants": participants_out,
        "total_verified": total_verified,
        "floor_applied": floor_applied,
        "rounding_adjusted": rounding_adjusted,
    }
    if discount_logs:
        result["discount_logs"] = discount_logs
    if surcharge_logs:
        result["surcharge_logs"] = surcharge_logs
    if surcharge_deductions:
        result["surcharge_deductions"] = surcharge_deductions
    return result


def calculate(parsed_json: dict) -> dict:
    _validate(parsed_json)

    total_amount = parsed_json["total_amount"]
    participants = parsed_json["participants"]
    items = parsed_json.get("items", [])
    N = len(participants)

    # ── Step 1: 항목별 실참여자 기준 비용 분할 ──
    if items:
        amounts, discount_logs = _calc_step1(items, participants)
    else:
        base = total_amount / N
        amounts = {p["name"]: base for p in participants}
        discount_logs = {}

    return _apply_steps_2_to_4(amounts, participants, total_amount, discount_logs)


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
