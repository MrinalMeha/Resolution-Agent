"""The resolution agent: guardrails → LLM tool-calling loop → grounded reply, with a full record."""
from __future__ import annotations

import json

from .guardrails import detect_legal_or_formal_complaint
from .llm import LLMClient, LLMError
from .prompts import build_system_prompt
from .session import Session
from .tools import TOOL_SPECS, Toolbox

FALLBACK_REPLY = ("I'm sorry - I'm having trouble completing this right now. I've passed your request to a human colleague "
                  "who will contact you directly.")


class ResolutionAgent:
    def __init__(self, session: Session, llm: LLMClient | None = None, max_steps: int = 8):
        self.s = session
        self.llm = llm
        self.tools = Toolbox(session)
        self.max_steps = max_steps
        self.messages: list[dict] = []      # LLM-side history (includes tool calls/results)

    # ------------------------------------------------------------------
    def respond(self, user_text: str) -> str:
        s = self.s
        s.turn += 1
        s.add_message("customer", user_text)
        self.messages.append({"role": "user", "content": user_text})

        # 1) Deterministic guardrail: legal threat / formal complaint → escalate immediately.
        if detect_legal_or_formal_complaint(user_text) and not any(
                c["category"] == "legal_or_formal_complaint" for c in s.escalations):
            res = self.tools.call("escalate_to_human", {
                "category": "legal_or_formal_complaint",
                "summary": "Customer threatened legal action / a formal complaint. Escalated immediately by guardrail; "
                           "no concessions were offered by the agent.",
                "customer_request": user_text,
            }, actor="guardrail")
            self.messages.append({"role": "system", "content": (
                f"[SYSTEM NOTE] Guardrail detected a legal threat / formal complaint and opened case {res.get('case_id')}. "
                "Acknowledge with empathy and tell the customer the specialist support team will contact them directly. "
                "Do not negotiate, offer compensation, or take further actions in this reply.")})

        reply = self._run_llm_loop()
        s.add_message("agent", reply)
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    # ------------------------------------------------------------------
    def _run_llm_loop(self) -> str:
        if self.llm is None:
            return "(No language model configured.)"
        for _ in range(self.max_steps):
            history = [{"role": "system", "content": build_system_prompt(self.s)}] + self.messages
            tools = [t for t in TOOL_SPECS if not (self.s.verified_pnr and t["function"]["name"] == "verify_customer")]
            try:
                msg = self.llm.chat(history, tools)
            except LLMError as e:
                return f"⚠️ {e}"
            calls = msg.get("tool_calls")
            if not calls:
                text = (msg.get("content") or "").strip()
                if text:
                    return text
                self.messages.append({"role": "user", "content": "(Please reply to the customer.)"})
                continue
            self.messages.append(msg)
            for tc in calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self.tools.call(name, args)
                self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                      "content": json.dumps(result, ensure_ascii=False)})
        # Safety net: never leave the customer hanging.
        if not any(c["category"] == "other_out_of_authority" for c in self.s.escalations):
            self.tools.call("escalate_to_human", {
                "category": "other_out_of_authority",
                "summary": "Agent exceeded its step limit without resolving the request.",
                "customer_request": "See conversation transcript.",
            }, actor="guardrail")
        return FALLBACK_REPLY
