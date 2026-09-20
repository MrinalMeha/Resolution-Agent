"""End-to-end loop test with a scripted fake LLM (no network). Proves the guardrail + tool loop + record."""
import json
import unittest

from agent.agent import ResolutionAgent
from agent.guardrails import detect_legal_or_formal_complaint
from agent.scenarios import SCENARIOS, evaluate
from agent.session import Session


class FakeLLM:
    """Plays back a list of assistant messages; records what it was sent."""
    def __init__(self, script):
        self.script, self.seen = list(script), []

    def chat(self, messages, tools):
        self.seen.append(messages)
        return self.script.pop(0)


def call(name, args, i=1):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


class GuardrailTests(unittest.TestCase):
    def test_legal_detection(self):
        for txt in ["I'll take legal action", "I am going to file a formal complaint", "my lawyer will call you",
                    "I'll report you to DGCA", "consumer court here I come"]:
            self.assertTrue(detect_legal_or_formal_complaint(txt), txt)
        for txt in ["I'm furious", "this is unacceptable", "I want a refund", "any complaints about baggage before?"]:
            self.assertFalse(detect_legal_or_formal_complaint(txt), txt)


class LoopTests(unittest.TestCase):
    def test_legal_threat_escalates_before_llm(self):
        s = Session(); s.preverify("SK4821X")
        llm = FakeLLM([say("I'm sorry. I've passed this to our specialist team, who will contact you directly.")])
        a = ResolutionAgent(s, llm)
        a.respond("This is unacceptable, I'm going to take legal action.")
        self.assertEqual(s.escalations[0]["category"], "legal_or_formal_complaint")
        self.assertEqual(s.escalations[0]["priority"], "urgent")
        self.assertEqual(s.actions[-1]["actor"], "guardrail")
        # the model was told about the case
        self.assertTrue(any("SYSTEM NOTE" in m["content"] for m in llm.seen[0] if m["role"] == "system"))

    def test_tool_loop_executes_and_records(self):
        s = Session(); s.preverify("TR1190B")
        llm = FakeLLM([
            call("get_my_booking", {}, 1),
            call("apply_delay_compensation", {"flight": "SK-118"}, 2),
            say("Done - meal voucher and lounge access are on your booking."),
        ])
        a = ResolutionAgent(s, llm)
        reply = a.respond("SK-118 is delayed, what can I get?")
        self.assertIn("lounge", reply)
        self.assertEqual([x["tool"] for x in s.actions if x["actor"] == "agent"], ["get_my_booking", "apply_delay_compensation"])
        self.assertEqual([m["role"] for m in s.transcript], ["customer", "agent"])

    def test_step_limit_falls_back_and_escalates(self):
        s = Session(); s.preverify("TR1190B")
        llm = FakeLLM([call("get_my_booking", {}, i) for i in range(10)])
        a = ResolutionAgent(s, llm, max_steps=3)
        reply = a.respond("hello")
        self.assertIn("human colleague", reply)
        self.assertEqual(s.escalations[0]["category"], "other_out_of_authority")

    def test_scenario_evaluator_pass_and_fail(self):
        sc = SCENARIOS[1]  # Arvind
        s = Session(); s.preverify(sc["pnr"])
        llm = FakeLLM([
            call("apply_delay_compensation", {"flight": "SK-118"}, 1),
            call("escalate_to_human", {"category": "compensation_beyond_policy", "summary": "hotel ask",
                                       "customer_request": "hotel", "flight": "SK-118"}, 2),
            say("ok"),
        ])
        ResolutionAgent(s, llm).respond(sc["turns"][0])
        self.assertTrue(all(ok for _, ok in evaluate(s, sc)))
        # an empty session must fail the checks
        empty = Session(); empty.preverify(sc["pnr"])
        self.assertFalse(all(ok for _, ok in evaluate(empty, sc)))


if __name__ == "__main__":
    unittest.main()
