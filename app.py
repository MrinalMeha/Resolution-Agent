"""Streamlit demo UI.  Run:  streamlit run app.py"""
import json
import os

import streamlit as st

from agent import LLMClient, LLMError, ResolutionAgent, Session
from agent.scenarios import SCENARIOS, evaluate

st.set_page_config(page_title="Airline Disruption Resolution Agent", page_icon="✈️", layout="wide")

# Streamlit Cloud secrets → env vars (so the same code works locally and hosted)
try:
    for _k in ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "GROQ_API_KEY", "LLM_API_KEY"):
        if _k in st.secrets:
            os.environ.setdefault(_k, str(st.secrets[_k]))
except Exception:
    pass

ICONS = {"ok": "✅", "already_done": "↩️", "updated_existing_case": "🔁", "rejected": "⛔", "error": "⚠️",
         "locked": "🔒", "not_found": "❔"}


# ------------------------------------------------------------------ state helpers
def start(scenario_id: int | None):
    s = Session()
    sc = next((x for x in SCENARIOS if x["id"] == scenario_id), None)
    if sc:
        s.preverify(sc["pnr"])
    st.session_state.session = s
    st.session_state.agent = ResolutionAgent(s, None)
    st.session_state.scenario = sc
    st.session_state.next_turn = 0
    st.session_state.queued = None


def get_llm():
    provider = os.getenv("LLM_PROVIDER", "groq")
    model = os.getenv("LLM_MODEL") or None
    key = os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY") or None
    sig = (provider, key, model)
    if st.session_state.get("llm_sig") != sig:
        try:
            st.session_state.llm = LLMClient(provider, api_key=key, model=model)
            st.session_state.llm_err = None
        except LLMError as e:
            st.session_state.llm, st.session_state.llm_err = None, str(e)
        st.session_state.llm_sig = sig
    return st.session_state.llm


if "session" not in st.session_state:
    start(1)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("✈️ Resolution Agent")
    st.caption("Scenario date: Wed 23 Sep 2026 · data source: assignment data pack only")

    st.subheader("Scenario")
    for sc in SCENARIOS:
        if st.button(f"{sc['id']} · {sc['title']}"):
            start(sc["id"])
            st.rerun()
    if st.button("Not signed in (agent verifies identity)"):
        start(None)
        st.rerun()
    st.caption("Identity demo → PNR `SK4821X`, email `priya.nair@example.com`")

llm = get_llm()
agent: ResolutionAgent = st.session_state.agent
agent.llm = llm
session: Session = st.session_state.session
scenario = st.session_state.scenario

# ------------------------------------------------------------------ layout
left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader("Customer chat")
    if scenario:
        st.info(f"**Scenario {scenario['id']}:** {scenario['summary']}")
    else:
        st.info("Not signed in - the agent must verify identity first.")
    if st.session_state.get("llm_err"):
        st.error(st.session_state.llm_err)

    for m in session.transcript:
        with st.chat_message("user" if m["role"] == "customer" else "assistant"):
            st.write(m["content"])

    if scenario and st.session_state.next_turn < len(scenario["turns"]):
        nxt = scenario["turns"][st.session_state.next_turn]
        if st.button(f"▶ Send scripted customer message: \"{nxt[:70]}{'…' if len(nxt) > 70 else ''}\""):
            st.session_state.queued = nxt
            st.session_state.next_turn += 1
            st.rerun()

with right:
    t_actions, t_cases, t_data, t_export = st.tabs(["🧾 Action record", "🚩 Escalations", "📦 Data & checks", "⬇️ Export"])

    with t_actions:
        st.caption("Every tool call the agent (or a guardrail) made - including rejected ones.")
        if not session.actions:
            st.write("No actions yet.")
        for a in reversed(session.actions):
            head = f"{ICONS.get(a['status'], '•')} **{a['tool']}** · {a['status']} · _{a['actor']}_ · turn {a['turn']}"
            with st.expander(head):
                st.caption(a["ts"])
                st.code(json.dumps({"args": a["args"], "result": a["result"]}, indent=2, ensure_ascii=False), language="json")

    with t_cases:
        if not session.escalations:
            st.write("No cases escalated.")
        for c in session.escalations:
            box = st.error if c["priority"] == "urgent" else st.warning
            box(f"**{c['case_id']}** · {c['category']} · priority **{c['priority']}**")
            st.code(json.dumps(c, indent=2, ensure_ascii=False), language="json")

    with t_data:
        if session.verified_pnr:
            st.markdown("**What the agent can see (verified customer only)**")
            st.json({"customer": {k: session.customer[k] for k in ("name", "tier", "prior_complaints")},
                     "segments": session.segments()}, expanded=False)
        if scenario:
            st.markdown("**Expected outcome**")
            st.write(scenario["expect"])
            if st.button("Run checks on this conversation"):
                for desc, ok in evaluate(session, scenario):
                    st.write(("✅ " if ok else "❌ ") + desc)
        with st.expander("Policy rules used (from data pack)"):
            st.markdown(
                "- **Cancelled by airline:** free rebooking on next flight within 24h **or** full refund (customer's choice)\n"
                "- **Delay <3h:** ₹500 meal voucher · **>3h:** meal voucher + lounge · **>5h:** meal voucher + hotel for the *delayed hours only*\n"
                "- **Refunds:** full, within 7 business days, original payment method only\n"
                "- **Fare difference:** voluntary move to higher fare → customer pays; waiver above ₹1,500 needs supervisor\n"
                "- **Gold/Platinum:** priority rebooking only, no extra compensation\n"
                "- **Must escalate:** beyond-policy compensation, fare waiver >₹1,500, non-airline-caused exceptions, "
                "legal/formal complaints, refund to another method"
            )

    with t_export:
        st.download_button("Download conversation + action record (JSON)",
                           json.dumps(session.export(), indent=2, ensure_ascii=False),
                           file_name="conversation_record.json", mime="application/json")
        st.caption("Contains the full transcript, every tool call with results, and all escalation handoff cases.")

# ------------------------------------------------------------------ chat input (pinned to bottom)
# Placed outside columns so Streamlit renders it at the very bottom of the page.
typed = st.chat_input("Type as the customer…")
prompt = typed or st.session_state.pop("queued", None)
if prompt:
    session.add_message("customer", prompt)
    with left:
        with st.chat_message("user"):
            st.write(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Checking booking & policy…"):
                reply = agent.respond(prompt)
                st.write(reply)
