# 프로젝트 비교 분석 — 내 프로젝트 vs `ai-sw-maestro-ai-study`

> 비교 대상: `c:\Users\kanghj\Downloads\ai-sw-maestro-ai-study-main.zip` (이하 **상대 프로젝트**)
> 기준: 내 프로젝트 **AI 정산 비서** (이하 **내 프로젝트**)
> 작성일: 2026-06-03

---

## 0. 한눈에 보기

| 항목 | 내 프로젝트 (AI 정산 비서) | 상대 프로젝트 (Medical QA Agent) |
|------|---------------------------|----------------------------------|
| 도메인 | 모임 정산 (계산 중심) | 의료 Q&A (RAG 검색 중심) |
| 공통 기반 | **LangGraph + Upstage Solar** Agentic Workflow (동일) | **LangGraph + Upstage Solar** Agentic Workflow (동일) |
| 아키텍처 | 단일 프로세스, `front → ai → calculator` **직접 import** (HTTP 없음) | **FastAPI(8001) ↔ Streamlit(8002)** HTTP 분리 + Docker Compose |
| LLM 호출 | `openai` 클라이언트 + `base_url` 수동 지정 | `langchain-upstage` `ChatUpstage` |
| LLM 출력 파싱 | **정규식 + `json.loads` 수동 추출** | **`with_structured_output(Pydantic)`** |
| 도메인 계산 | **순수 Python 계산 엔진** (`calculator/`) | RAG 검색 (ChromaDB 벡터 스토어) |
| 입출력 스키마 | `TypedDict` 1개 | Pydantic 모델 5개 (요청/응답/분석/스트림) |
| 의존성 관리 | **없음** (`requirements.txt`/`pyproject.toml` 부재) | `pyproject.toml` + `uv.lock` |
| 배포 | 없음 (로컬 streamlit 실행) | Dockerfile ×2 + `docker-compose.yml` + `start.sh` |
| 테스트 | **단위 테스트 216줄 보유** | **없음** |
| 문서 | README + 폴더별 `CLAUDE.md` 풍부 | README 사실상 비어 있음(제목만) |

핵심 요약: **내 프로젝트는 "도메인 로직 깊이·테스트·문서"가 강하고, 상대 프로젝트는 "엔지니어링 인프라(의존성/배포/LLM 연동 견고성)"가 강하다.**

---

## 1. 구조적 차이

### 1-1. 통신 방식 — 가장 큰 아키텍처 차이

**내 프로젝트** — 모놀리식, 직접 import
```
[Streamlit] → graph.invoke() → [LangGraph 노드] → calculate() → [순수 계산 엔진]
            (전부 하나의 Python 프로세스, HTTP 없음)
```

**상대 프로젝트** — 서비스 분리, HTTP/SSE
```
[Streamlit :8002] --httpx--> [FastAPI :8001] → graph.ainvoke() → [LangGraph 노드] → ChromaDB
                  (Docker Compose로 2개 컨테이너 오케스트레이션)
```

- 내 방식: MVP 범위에 적합하고 단순하다. 디버깅·배포가 쉽다.
- 상대 방식: 프론트/백 독립 배포·확장이 가능하고, SSE로 노드별 진행 상황을 실시간 스트리밍한다. 다만 단일 사용자 MVP에는 과한 측면이 있다.

> 결론: 통신 방식은 **우열이 아니라 트레이드오프**다. 내 프로젝트의 단일 프로세스 설계는 `CLAUDE.md`의 "세 폴더 모두 하나의 프로세스" 원칙과 일치하므로 유지가 타당하다.

### 1-2. 폴더 레이어링

상대 프로젝트는 **`app/core/` 공통 레이어**를 둔다:
```
app/core/config.py     # 환경변수 일괄 로드
app/core/llm.py        # get_llm() LLM 팩토리
app/core/embedding.py  # 임베딩 팩토리
app/core/database.py   # ChromaDB 클라이언트 싱글톤
app/prompts/templates.py  # 프롬프트 분리
app/schemas.py         # Pydantic 모델 모음
```

내 프로젝트는 `ai/nodes.py` **한 파일에 LLM 클라이언트·프롬프트·파싱·검증 로직이 모두 모여 있다** (349줄).

---

## 2. 상대 프로젝트가 더 나은 점 (도입 검토 대상)

### ⭐ 2-1. `with_structured_output` 으로 견고한 LLM 출력 파싱
상대 프로젝트:
```python
structured_llm = llm.with_structured_output(QueryAnalysis)  # Pydantic 스키마 강제
analysis = structured_llm.invoke([...])  # 검증된 객체 반환, 실패 시 재시도
```
내 프로젝트:
```python
match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)  # 마크다운 펜스 수동 제거
return json.loads(text.strip())                              # 깨지면 그대로 예외
```
→ 내 방식은 LLM이 설명 문장을 덧붙이거나 JSON이 깨지면 바로 실패한다. 실제로 `_extract_json` + `_post_validate_exceptions` 라는 **보정 코드를 직접 만들어 메꾸고 있는데**, 이는 structured output을 쓰면 상당 부분 불필요해진다.

### ⭐ 2-2. `langchain-upstage` 사용
상대 프로젝트는 `ChatUpstage` / `UpstageEmbeddings` 공식 LangChain 통합을 쓴다. 내 프로젝트는 `openai` 클라이언트에 `base_url`을 수동 지정한다(동작은 하지만 LangChain 생태계의 structured output·콜백·트레이싱 기능을 못 쓴다).

### ⭐ 2-3. 의존성 관리 파일 존재
상대: `pyproject.toml` + `uv.lock` 로 버전 고정. 내 프로젝트는 **의존성 명세 파일이 아예 없다** — README에는 `pip install -r requirements.txt`라고 적혀 있으나 **해당 파일이 존재하지 않는다.** (재현 불가능, 협업 시 치명적)

### 2-4. Pydantic 입출력 스키마
요청/응답/구조화출력/스트림 이벤트를 Pydantic으로 정의해 타입 안전성과 자동 검증을 확보한다. 내 프로젝트의 `parsed_json`은 자유 `dict`라 구조 보장이 코드 곳곳의 방어 로직에 의존한다.

### 2-5. 배포 인프라
Dockerfile ×2 + docker-compose + `start.sh`(.env 체크 포함). 내 프로젝트는 수동 streamlit 실행만 있다.

### 2-6. LLM 노드 예외 폴백
```python
except Exception as e:
    logger.warning("Query analysis failed: %s", e)
    return { ...기본값... }   # 파싱 실패해도 흐름 유지
```
내 프로젝트의 LLM 노드는 try/except가 없어 LLM/파싱 실패 시 전체가 중단된다.

### 2-7. 환경변수 일원화 + 환경변수 예시 충실
상대 `.env.example`은 키 8개를 설명과 함께 제공(LANGSMITH 트레이싱 포함). 내 `.env.example`은 `UPSTAGE_API_KEY=` 한 줄뿐.

---

## 3. 내 프로젝트가 더 나은 점 (유지·강화할 강점)

### ⭐ 3-1. 계산/판단의 책임 분리 (핵심 설계 우위)
내 프로젝트는 **"LLM은 비율(rate) 판단만, 금액 산술은 순수 Python `calculator/`"** 라는 원칙을 코드로 철저히 구현했다. `engine.py`는 항목별 분할 → 감액·재분배 → 할증·차감분배 → 30% 하한선 → 반올림 오차 보정 → 총액 검증까지 결정론적으로 처리한다.
→ 상대 프로젝트에는 이런 도메인 계산 엔진이 없다(RAG 검색이 핵심이라 성격이 다르긴 하다). **금액을 다루는 서비스에서 LLM에 산술을 맡기지 않는 설계는 명확한 강점이다.**

### ⭐ 3-2. 단위 테스트 보유
`calculator/tests/test_engine.py` 216줄로 계산 로직을 검증한다. **상대 프로젝트는 테스트가 0개다.**

### ⭐ 3-3. LLM 오분류 방어 보정 레이어
`_post_validate_exceptions()`가 "지각을 discount로 잘못 분류" 같은 LLM 실수를 키워드 기반으로 코드에서 교정한다. 또 `SafetyCheckNode`가 총액 누락·중복 참여자·null rate 등을 사전 차단한다. → LLM 출력의 비결정성을 다층 방어하는 실전적 설계.

### ⭐ 3-4. 문서화
README(서비스 흐름·노드표·parsed_json 예시)와 폴더별 `CLAUDE.md`가 충실하다. **상대 README는 제목 한 줄뿐이다.**

### 3-5. 프롬프트 엔지니어링 깊이
감액률 기준표, 할증 분기(비율/금액), 공유 메시지의 강력한 anti-hallucination 지시("산술 계산 금지", "근거 언급 금지")까지 정교하다.

### 3-6. 범위에 맞는 단순함
HTTP·Docker·벡터DB 없이 단일 프로세스로 끝내 MVP 범위에 과하지 않다.

---

## 4. 사소하지만 고칠 점 (내 프로젝트)

- `ai/nodes.py:283` `report_generation_node` 에 디버그용 `print("[CR]", ...)` 가 남아 있다 → 제거 또는 `logging` 전환.
- `_call_llm(..., tag="")` 의 `tag` 파라미터가 선언되었으나 **전혀 사용되지 않는다** → 로깅에 활용하거나 제거.
- LLM 노드에 예외 처리가 없다 (2-6 참조).
- `git status` 기준 `PLAN.md` / `SERVICE_FLOW.md` 가 삭제(D) 상태 → 정리 커밋 필요.

---

## 5. 권장 액션 (우선순위순)

| 순위 | 액션 | 근거 |
|------|------|------|
| 1 | **`requirements.txt` 또는 `pyproject.toml` 추가** | 현재 의존성 명세 파일 부재 → 재현 불가. 가장 시급 |
| 2 | LLM 노드에 **structured output 도입** (`langchain-upstage` + Pydantic, 또는 openai의 `response_format`) | `_extract_json`/`_post_validate_exceptions`의 fragile JSON 파싱 대체 |
| 3 | LLM 노드 **try/except 폴백** 추가 | 파싱 실패 시 전체 중단 방지 |
| 4 | `ai/core_llm.py`(또는 `config.py`)로 **LLM 클라이언트·프롬프트 분리** | `nodes.py` 349줄 → 책임 분리, 상대의 `core/` 레이어 참고 |
| 5 | 디버그 `print` 제거 + `tag` 활용/제거 | 코드 위생 |
| 6 | (선택) Dockerfile 추가 | 데모/배포 재현성. MVP엔 우선순위 낮음 |

> **유지할 강점**: 계산 엔진 분리, 단위 테스트, 방어 보정 레이어, 문서화 — 이 4가지는 상대 프로젝트보다 확실히 앞서므로 그대로 가져간다.

---

## 6. 결론

두 프로젝트는 **같은 스터디(SW Maestro AI)의 LangGraph + Upstage Solar 라는 동일 기반** 위에 서 있지만, 도메인(정산 계산 vs 의료 RAG)이 달라 강점이 갈린다.

- **내 프로젝트의 정체성**은 *"LLM이 판단하고 코드가 계산하는, 검증 가능하고 문서화된 도메인 엔진"* 이다 — 이 방향이 정산 서비스에 정확히 부합한다.
- **상대 프로젝트에서 배울 것**은 도메인이 아니라 **엔지니어링 기반**: structured output, 의존성 고정, 레이어 분리, 예외 폴백, 배포 인프라다.

→ **도메인 설계는 내 것을 유지하고, 엔지니어링 견고성(1~4번 액션)만 상대에게서 흡수하면** 두 프로젝트의 장점을 모두 갖춘 형태가 된다.
