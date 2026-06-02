# calculator/ — 정산 계산 모듈

## 역할 범위

정산의 모든 산술 연산을 담당하는 순수 Python 모듈이다.
웹 서버가 아니며, ai/의 CalculationNode가 직접 import하여 호출한다.

```python
from calculator.engine import calculate, recalculate
result = calculate(parsed_json)
```

LLM은 이 모듈에 관여하지 않으며, 이 모듈은 LLM을 호출하지 않는다.
예외 조건의 **감액률은 ai/의 InputParsingNode(LLM)가 결정**하며,
이 모듈은 `parsed_json`에 담긴 `discount_rate` 값을 받아 금액으로 환산하기만 한다.

## 주요 함수 인터페이스

```python
calculate(parsed_json: dict) -> dict
# 입력: 구조화된 정산 JSON (참여자별 예외 조건 + discount_rate 포함)
# 출력: 참여자별 부담 금액, 적용된 감액 내역, 총액 검증 결과

recalculate(parsed_json: dict, feedback_json: dict) -> dict
# 피드백 조건 반영 재계산
```

## 계산 처리 순서

1. 기본: 총액 ÷ 참여자 수 (N분의 1)
2. 예외 조건 반영: `discount_rate` 또는 `surcharge_rate`를 받아 계산
   - `discount_rate: 1.0` → 해당 항목 전액 제외 (본인↓, 나머지↑)
   - `discount_rate: 0.3` → 해당 항목 비용의 30% 감액 후 나머지에게 재분배
   - `surcharge_rate: 0.2` → 해당 항목 비용의 20% 추가 부담 (본인↑, 나머지↓)
   - 재분배 대상: 해당 항목에 예외 조건이 없는 나머지 참여자
3. 최소 부담 하한선: 균등 분담액의 30% (강제 적용)
4. 결제자(sponsor) 처리: 전액 결제자의 final_amount = 공정분담액 - 전액 (음수 가능)
5. 총액 검증: 참여자별 합계 ↔ 입력 총액 비교
6. 반올림 오차: 자동 보정 (차액 재분배)

## 예외 조건 타입

| 조건 | 키 | 예시 |
|------|----|------|
| 술 미섭취 | `discount_rate: 1.0` | `{"type": "술 미섭취", "target_items": ["주류"], "discount_rate": 1.0}` |
| 소량 섭취 | `discount_rate: 0.3~0.5` | `{"type": "소량 섭취", "target_items": ["안주"], "discount_rate": 0.4}` |
| 중도 귀가 | `discount_rate: 0.5` | `{"type": "중도 귀가", "target_items": ["주류", "안주"], "discount_rate": 0.5}` |
| 지각/늦은 도착 | `surcharge_rate: 0.15~0.3` | `{"type": "늦은 도착", "target_items": ["안주"], "surcharge_rate": 0.2}` |

## 예외 처리

| 상황 | 처리 방식 |
|------|-----------|
| 총액 or 참여자 정보 누락 | `ValueError` 발생 — ai/의 SafetyCheckNode가 upstream에서 차단 |
| 항목별 금액 합계 ≠ 총액 | `ValueError` 발생 + 모순 지점 메시지 포함 |
| `discount_rate` 또는 `surcharge_rate` 범위 초과 (0.0~1.0 외) | `ValueError` 발생 |
| 하한선 미달 케이스 | 균등 분담액의 30%로 강제 적용 후 정상 반환 |

## breakdown 필드 구조

```python
"breakdown": {
    "base": int,          # 균등 분담액
    "redistributed": int, # 타인 예외로 받거나 낸 순 금액
    "discounted": int,    # 본인 감액 합계
    "surcharged": int,    # 본인 할증 합계
}
```
