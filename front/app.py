import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from ai.graph import graph

# ── Page config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI 정산 비서",
    page_icon="💸",
    layout="centered",
)

# ── CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
.main .block-container {
    max-width: 760px;
    padding: 1.5rem 1.5rem 2rem;
}

/* Strategy badge */
.strategy-badge {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 12px; border-radius: 20px;
    font-size: 0.8rem; font-weight: 600; margin-bottom: 0.5rem;
}
.badge-simple    { background:#DCFCE7; color:#166534; }
.badge-exception { background:#FEF3C7; color:#92400E; }
.badge-sponsor   { background:#DBEAFE; color:#1E40AF; }

/* Participant card */
.participant-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 16px; border-radius: 10px;
    background: #FFFFFF; border: 1px solid #E2E8F0; margin-bottom: 8px;
}
.participant-name   { font-size: 1rem; font-weight: 600; color: #1E293B; }
.participant-amount { font-size: 1.15rem; font-weight: 700; color: #2563EB; }
.participant-note   { font-size: 0.78rem; color: #94A3B8; margin-top: 1px; }

/* Section label */
.section-header {
    font-size: 0.82rem; font-weight: 600; color: #64748B;
    text-transform: uppercase; letter-spacing: 0.07em; margin: 1rem 0 0.5rem;
}

/* Input box area */
.input-box {
    background: #FFFFFF;
    border: 1.5px solid #CBD5E1;
    border-radius: 12px;
    padding: 2px 4px;
    margin-top: 12px;
}

/* Sidebar example card */
.ex-card {
    background: #FFFFFF; border: 1px solid #E2E8F0;
    border-radius: 10px; padding: 9px 12px 5px; margin-bottom: 4px;
}
.ex-title { font-size: 0.87rem; font-weight: 600; color: #1E293B; }
.ex-desc  { font-size: 0.75rem; color: #94A3B8; }

hr { margin: 1rem 0 !important; border-color: #E2E8F0 !important; }
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []

if "draft" not in st.session_state:
    st.session_state["draft"] = ""

# draft 클리어 플래그 — 위젯 렌더 전에 처리해야 에러가 없음
if st.session_state.pop("_clear_draft", False):
    st.session_state["draft"] = ""


# ── Example prompts ───────────────────────────────────────────────────
_EXAMPLES = [
    {
        "icon": "🍺",
        "title": "술 미섭취 + 지각",
        "desc": "4명 · 주류+안주 · 예외 2가지",
        "prompt": "총 8만원이고 A, B, C, D 있어. 주류 3만원 / 안주 5만원. C는 늦게 왔고 D는 술 안 마셨어.",
    },
    {
        "icon": "💳",
        "title": "선결제 + 복합 예외",
        "desc": "5명 · A 전액 결제 · 예외 2가지",
        "prompt": "총 12만원이야. 주류 5만원, 안주 5만원, 공통비 2만원. A, B, C, D, E 5명. D는 술 안 마셨고 E는 좀 늦게 왔어. A가 전액 결제했어.",
    },
    {
        "icon": "👥",
        "title": "균등 분배",
        "desc": "예외 없이 N빵",
        "prompt": "총 6만원이고 A, B, C 세 명이서 균등하게 나눠.",
    },
    {
        "icon": "🚪",
        "title": "중도 귀가",
        "desc": "중간에 자리 뜬 경우",
        "prompt": "총 10만원이고 A, B, C, D야. 안주 6만원, 주류 4만원. D는 중간에 먼저 갔어.",
    },
    {
        "icon": "🥤",
        "title": "소량 섭취",
        "desc": "거의 안 먹은 참여자 포함",
        "prompt": "총 9만원이야. A, B, C, D, E 5명. 안주 6만원 / 주류 3만원. E는 음식을 거의 안 먹었어.",
    },
    {
        "icon": "🎂",
        "title": "생일 주인공 면제",
        "desc": "특정 인원 비용 전액 면제",
        "prompt": "총 15만원이야. 주류 5만원, 안주 10만원. A, B, C, D, E 5명이고 오늘 A 생일이라 A는 안 내도 돼. B가 전액 냈어.",
    },
]


# ── Helpers ───────────────────────────────────────────────────────────
def _invoke_graph(prompt: str, payer: str) -> dict:
    prev = next(
        (m for m in reversed(st.session_state.messages) if m["role"] == "assistant"),
        None,
    )
    try:
        if prev and prev.get("parsed_json", {}).get("participants"):
            result = graph.invoke({
                "raw_input": prompt,
                "payer": payer or prev.get("payer", ""),
                "parsed_json": prev["parsed_json"],
                "strategy": prev.get("strategy", ""),
                "feedback_history": prev.get("feedback_history") or [],
            })
        else:
            result = graph.invoke({
                "raw_input": prompt,
                "payer": payer,
                "feedback_history": [],
            })
        return dict(result)
    except Exception as e:
        return {"error": str(e)}


def _exception_notes(p: dict) -> str:
    bd = p.get("breakdown", {})
    parts = []
    if bd.get("discounted", 0) > 0:
        parts.append(f"−{bd['discounted']:,}원 감액")
    if bd.get("surcharged", 0) > 0:
        parts.append(f"+{bd['surcharged']:,}원 할증")
    return " · ".join(parts)


def _card(label: str, amount_str: str, notes: str, style: str = "") -> None:
    # 멀티라인 HTML은 chat_message 안에서 마크다운 파서가 오동작하므로 단일 라인으로 작성
    note_part = f'<div class="participant-note">{notes}</div>' if notes else ""
    st.markdown(
        f'<div class="participant-row" style="{style}"><div><div class="participant-name">{label}</div>{note_part}</div><div class="participant-amount">{amount_str}</div></div>',
        unsafe_allow_html=True,
    )


def _render_result(msg: dict) -> None:
    if msg.get("error"):
        st.error(f"오류: {msg['error']}")
        return
    if msg.get("safety_error"):
        st.warning(f"⚠️ {msg['safety_error']}\n\n입력 내용을 수정해 다시 시도해주세요.")
        return

    cr = msg.get("calculation_result")
    if not cr or not cr.get("participants"):
        st.info("정산 결과를 처리하지 못했습니다.")
        return

    participants = cr["participants"]
    current_payer = (msg.get("payer") or "").strip()
    strategy = msg.get("strategy", "SIMPLE")
    final_report = msg.get("final_report", "")

    badge_cfg = {
        "SIMPLE":    ("badge-simple",    "균등 분배"),
        "EXCEPTION": ("badge-exception", "⚡ 예외 조건 반영"),
        "SPONSOR":   ("badge-sponsor",   "💳 선결제 포함"),
    }
    badge_class, badge_label = badge_cfg.get(strategy, ("badge-simple", strategy))
    st.markdown(
        f'<span class="strategy-badge {badge_class}">{badge_label}</span>',
        unsafe_allow_html=True,
    )

    if current_payer:
        payer_row = next((p for p in participants if p["name"] == current_payer), None)
        total_paid = msg.get("parsed_json", {}).get("total_amount", 0)
        st.markdown(
            f'<div class="section-header">💳 {current_payer} ({total_paid:,}원 결제) · 이체할 금액</div>',
            unsafe_allow_html=True,
        )
        for p in participants:
            if p["name"] == current_payer:
                continue
            _card(f"{p['name']} → {current_payer}", f"{p['final_amount']:,}원", _exception_notes(p))
        if payer_row:
            _card(
                f"{current_payer} 수령 예정액", f"{-payer_row['final_amount']:,}원",
                "모두 이체 완료 시",
                style="border-color:#BFDBFE;background:#EFF6FF;margin-top:4px;",
            )
    else:
        st.markdown('<div class="section-header">정산 결과</div>', unsafe_allow_html=True)
        for p in participants:
            _card(p["name"], f"{p['final_amount']:,}원", _exception_notes(p))

    floor_applied = cr.get("floor_applied", [])
    if floor_applied:
        st.caption(f"💡 최소 부담 하한선(30%) 적용: {', '.join(floor_applied)}")
    if not cr.get("total_verified", True):
        st.warning("총액 검증 불일치가 감지되었습니다.")

    if final_report:
        with st.expander("📋 계산 근거 보기"):
            st.write(final_report)
        st.markdown('<div class="section-header">공유용 정산 메시지</div>', unsafe_allow_html=True)
        st.caption("우측 상단 복사 버튼으로 클립보드에 복사하세요.")
        st.code(final_report, language=None)


# ── Sidebar ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("<h3 style='margin-bottom:4px;'>📋 상황별 예시</h3>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#64748B;font-size:0.82rem;margin:0 0 8px;'>"
        "클릭하면 입력창에 채워집니다. 수정 후 전송하세요.</p>",
        unsafe_allow_html=True,
    )

    if st.button("🗑️ 대화 초기화", use_container_width=True):
        st.session_state.messages = []
        st.session_state["draft"] = ""
        st.rerun()

    st.divider()

    for i, ex in enumerate(_EXAMPLES):
        st.markdown(
            f"""<div class="ex-card">
                <div class="ex-title">{ex["icon"]} {ex["title"]}</div>
                <div class="ex-desc">{ex["desc"]}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        if st.button("입력창에 채우기 ↗", key=f"_ex_{i}", use_container_width=True):
            st.session_state["draft"] = ex["prompt"]
            st.rerun()


# ── Header ────────────────────────────────────────────────────────────
st.markdown("# 💸 AI 정산 비서")
st.markdown(
    "<p style='color:#64748B;font-size:0.92rem;margin-top:-0.5rem;'>"
    "자연어로 정산 조건을 입력하세요. "
    "결과가 나온 후 추가 조건을 입력하면 자동으로 재계산됩니다.</p>",
    unsafe_allow_html=True,
)

# ── Chat history (오래된 순서대로 위→아래 표시) ───────────────────────
for msg in st.session_state.messages:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant"):
            _render_result(msg)


# ── Input section (하단) ──────────────────────────────────────────────
st.markdown("---")

# 프롬프트 입력 (사이드바 예시로 채울 수 있음)
draft = st.text_area(
    "정산 상황 입력",
    key="draft",
    placeholder=(
        "정산 상황을 자연어로 입력하세요.\n"
        "예) 총 8만원이고 A, B, C, D 있어. 주류 3만원 / 안주 5만원. C는 늦게 왔고 D는 술 안 마셨어."
    ),
    height=96,
    label_visibility="collapsed",
)

send_clicked = st.button(
    "전송 →",
    type="primary",
    use_container_width=True,
    disabled=not bool(st.session_state.get("draft", "").strip()),
)

st.caption("좌측 예시를 클릭하면 입력창에 채워집니다. 수정 후 전송하세요.")

# 새 메시지가 있을 때 하단으로 자동 스크롤
if st.session_state.messages:
    st.components.v1.html(
        "<script>window.parent.document.querySelector('.main').scrollTo(0, 999999);</script>",
        height=0,
    )

# ── Send handler ──────────────────────────────────────────────────────
if send_clicked and draft.strip():
    with st.spinner("AI가 분석 중입니다..."):
        result = _invoke_graph(draft.strip(), "")

    st.session_state.messages.append({
        "role": "user",
        "content": draft.strip(),
    })
    st.session_state.messages.append({
        "role": "assistant",
        **result,
    })
    st.session_state["_clear_draft"] = True  # 위젯 렌더 전에 처리됨
    st.rerun()
