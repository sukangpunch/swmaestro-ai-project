# AI 정산 비서 — 프로젝트 개요

## 서비스 정의

모임 정산 시 자연어로 입력된 예외 조건(술 미섭취, 늦은 도착, 선결제 등)을
LLM 맥락 추론 + 규칙 기반 계산 엔진으로 처리하는 Agentic Workflow 프로젝트.

## 폴더 구조

- front/ : Streamlit UI, session_state 세션 관리
- ai/ : LangGraph StateGraph, Upstage LLM 호출, 노드 체인
- calculator/ : 순수 Python 정산 계산 모듈 (웹 서버 없음)

세 폴더 모두 하나의 프로세스에서 실행된다.
front/ → ai/ → calculator/ 순서로 직접 import하여 호출한다. HTTP 통신 없음.

## 서비스 실행

```bash
# 1. .env 파일 생성 후 UPSTAGE_API_KEY 값 입력
cp .env.example .env

# 2. 실행 (루트에서 실행해야 .env를 올바르게 로드함)
streamlit run front/app.py
```

- Streamlit : http://localhost:8501
- `.env`는 git에 커밋하지 않는다 (`.gitignore`에 포함됨)
- `.env.example`은 커밋한다 — 팀원이 참고하는 키 목록

## 핵심 설계 원칙

1. 금액의 산술 계산은 LLM이 수행하지 않는다.
   → LLM 담당: 자연어 파싱, 예외 조건별 rate 결정(0.0~1.0), 전략 분기, 설명 생성
   → calculator/ 담당: rate를 실제 금액으로 환산하는 연산 전체
   → 구분 기준: "얼마나 적용할지 판단"은 LLM, "그 비율로 계산"은 calculator/
2. 정산 전략은 SIMPLE / EXCEPTION / SPONSOR 3가지로만 분기된다.
3. 최소 부담 하한선: 균등 분담액의 30%
4. 세션은 브라우저 종료 시 초기화되며 장기 저장소는 없다.

## 예외 조건 분류 규칙

예외 조건은 성격에 따라 두 가지 rate로 분류한다:

| 구분 | rate 종류 | 대상 조건 | 효과 |
|------|----------|-----------|------|
| 감액 | `discount_rate` | 술 미섭취, 소량 섭취, 중도 귀가 | 해당 항목 비용에서 차감, 나머지에게 재분배 |
| 할증 | `surcharge_rate` | 지각/늦은 도착 | 해당 항목 비용을 추가 부담, 나머지에게 배분 |

- `discount_rate: 1.0` = 해당 항목 전액 제외
- `surcharge_rate: 0.2` = 해당 항목의 20% 추가 부담
- 두 rate 모두 유효 범위: 0.0 ~ 1.0

## 결제자(Payer) 처리 규칙

한 명이 전액을 먼저 결제한 경우 front/에서 `payer` 이름을 입력받는다.

- ai/nodes.py의 `_inject_payer()`가 `parsed_json`에 `sponsor: {name, prepaid: total_amount}` 를 자동 주입
- calculator/가 sponsor 로직으로 계산: 결제자의 `final_amount` = 공정 부담액 - 전액 (음수 = 수령 예정)
- 나머지 참여자의 `final_amount` = 결제자에게 이체할 금액
- 결과 표시: "B → A: 23,750원" 형태

## 구현 제외 항목 (MVP 범위 밖)

- 실제 계좌 송금 및 금융 트랜잭션
- OCR, 카드 내역/계좌 연동
- 회원가입/로그인, 장기 데이터 저장

## 환경변수

- UPSTAGE_API_KEY : .env 파일로 관리, 코드에 하드코딩 금지

## 테스트 시나리오

시나리오 A: 술 미섭취 + 지각 (4명, 8만원, 주류 3만/안주 5만, D 미섭취, C 지각)
시나리오 B: 선결제 + 예외 조건 복합 (5명, 12만원, A 전액 결제)
시나리오 C: 피드백 재계산 (기존 세션 유지 + 조건 추가)
