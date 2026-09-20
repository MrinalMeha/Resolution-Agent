"""Offline tests (no API key needed): policy engine + tool guardrails.  Run: python -m unittest -v"""
import unittest

from agent import policy
from agent.session import Session
from agent.tools import Toolbox


def make(pnr):
    s = Session()
    s.preverify(pnr)
    return s, Toolbox(s)


class PolicyTests(unittest.TestCase):
    def test_delay_4h_meal_and_lounge_no_hotel(self):
        e = policy.delay_entitlements(240, "07:10", "11:10")
        types = [x["type"] for x in e["entitled"]]
        self.assertEqual(types, ["meal_voucher", "lounge_access"])
        self.assertIn("hotel_accommodation", [x["type"] for x in e["not_entitled"]])

    def test_delay_6h_hotel_for_delayed_hours_only(self):
        e = policy.delay_entitlements(360, "14:00", "20:00")
        hotel = next(x for x in e["entitled"] if x["type"] == "hotel_accommodation")
        self.assertEqual(hotel["hours_covered"], 6)
        self.assertIn("full_night_hotel_stay", [x["type"] for x in e["not_entitled"]])

    def test_delay_under_3h_500_voucher(self):
        e = policy.delay_entitlements(120, "10:00", "12:00")
        self.assertEqual(e["entitled"], [{"type": "meal_voucher", "amount_inr": 500}])

    def test_exactly_3h_is_policy_gap(self):
        self.assertTrue(policy.delay_entitlements(180, "10:00", "13:00")["policy_gap"])

    def test_fare_difference_threshold(self):
        self.assertTrue(policy.fare_difference(2000)["waiver_requires_supervisor"])
        self.assertFalse(policy.fare_difference(1500)["waiver_requires_supervisor"])

    def test_non_airline_cause_needs_escalation(self):
        self.assertEqual(policy.cancellation_entitlements("Gold", "customer_missed")["must_escalate"],
                         "non_airline_caused_exception")


class ToolGuardrailTests(unittest.TestCase):
    def test_unverified_customer_blocked(self):
        s = Session()
        r = Toolbox(s).call("get_my_booking")
        self.assertEqual(r["status"], "rejected")

    def test_verification_wrong_email(self):
        s = Session()
        r = Toolbox(s).call("verify_customer", {"booking_reference": "SK4821X", "email": "x@y.com"})
        self.assertEqual(r["status"], "rejected")
        self.assertIsNone(s.verified_pnr)

    def test_verification_ok_and_scoped(self):
        s = Session()
        t = Toolbox(s)
        self.assertEqual(t.call("verify_customer", {"booking_reference": "sk4821x", "email": "PRIYA.NAIR@example.com"})["status"], "ok")
        # cannot see another customer's flight
        self.assertEqual(t.call("get_entitlements", {"flight": "SK-118"})["status"], "error")

    def test_priya_refund_original_method_only(self):
        s, t = make("SK4821X")
        bad = t.call("initiate_refund", {"flight": "SK-204", "refund_destination": "different_method_requested"})
        self.assertEqual(bad["status"], "rejected")
        ok = t.call("initiate_refund", {"flight": "sk204"})
        self.assertEqual(ok["status"], "ok")
        self.assertEqual(ok["destination"], "original payment method")
        self.assertIn("7 business days", ok["processing_time"])

    def test_refund_is_idempotent_and_blocks_rebooking(self):
        s, t = make("SK4821X")
        t.call("initiate_refund", {"flight": "SK-204"})
        self.assertEqual(t.call("initiate_refund", {"flight": "SK-204"})["status"], "already_done")
        self.assertEqual(t.call("rebook_next_available", {"flight": "SK-204"})["status"], "rejected")

    def test_rebooking_priority_for_gold_and_no_invented_flight(self):
        s, t = make("SK4821X")
        r = t.call("rebook_next_available", {"flight": "SK-204"})
        self.assertTrue(r["priority_access"])
        self.assertIsNone(r["assigned_flight"])
        self.assertEqual(r["charge_inr"], 0)

    def test_no_refund_or_rebook_on_unaffected_flight(self):
        s, t = make("SK4821X")
        self.assertEqual(t.call("initiate_refund", {"flight": "RETURN"})["status"], "rejected")
        self.assertEqual(t.call("rebook_next_available", {"flight": "RETURN"})["status"], "rejected")

    def test_arvind_no_hotel(self):
        s, t = make("TR1190B")
        r = t.call("apply_delay_compensation", {"flight": "SK-118"})
        self.assertEqual({i["type"] for i in r["issued_now"]}, {"meal_voucher", "lounge_access"})
        self.assertNotIn(("hotel_accommodation", "SK-118"), s.issued)
        self.assertEqual(t.call("apply_delay_compensation", {"flight": "SK-118"})["status"], "already_done")

    def test_meher_hotel_six_hours_and_fare_rules(self):
        s, t = make("WL7742")
        r = t.call("apply_delay_compensation", {"flight": "SK-305"})
        hotel = next(i for i in r["issued_now"] if i["type"] == "hotel_accommodation")
        self.assertEqual(hotel["hours_covered"], 6)
        fd = t.call("check_fare_difference", {"flight": "SK-305"})
        self.assertEqual(fd["amount_inr"], 2000)
        self.assertTrue(fd["waiver_requires_supervisor"])
        self.assertEqual(t.call("request_voluntary_rebooking",
                                {"flight": "SK-305", "customer_accepts_fare_difference": False})["status"], "rejected")

    def test_delay_comp_rejected_for_cancelled_flight(self):
        s, t = make("SK4821X")
        self.assertEqual(t.call("apply_delay_compensation", {"flight": "SK-204"})["status"], "rejected")

    def test_escalation_records_context_and_dedupes(self):
        s, t = make("SK4821X")
        t.call("initiate_refund", {"flight": "SK-204"})
        a = t.call("escalate_to_human", {"category": "compensation_beyond_policy", "flight": "RETURN",
                                         "summary": "Wants business upgrade", "customer_request": "upgrade"})
        b = t.call("escalate_to_human", {"category": "compensation_beyond_policy", "flight": "RETURN",
                                         "summary": "again", "customer_request": "upgrade please"})
        self.assertEqual(a["status"], "ok")
        self.assertEqual(b["status"], "updated_existing_case")
        self.assertEqual(len(s.escalations), 1)
        self.assertIn("initiate_refund → ok", s.escalations[0]["actions_taken_so_far"])

    def test_every_call_is_logged(self):
        s, t = make("TR1190B")
        n = len(s.actions)
        t.call("get_my_booking")
        t.call("nonexistent_tool")
        self.assertEqual(len(s.actions), n + 2)


if __name__ == "__main__":
    unittest.main()
