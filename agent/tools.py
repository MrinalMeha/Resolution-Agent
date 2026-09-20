"""Tools the LLM can call.

Design rule: the LLM chooses *which* tool to call; the tool itself enforces policy, identity
scope, idempotency and authority limits. A tool can therefore REJECT a bad call even if the model
gets it wrong - the model cannot talk its way past a guardrail.
"""
from __future__ import annotations

import re
from typing import Any

from . import policy
from .session import Session

ESCALATION_CATEGORIES = {
    "legal_or_formal_complaint": "urgent",
    "compensation_beyond_policy": "normal",
    "fare_difference_waiver": "normal",
    "non_airline_caused_exception": "normal",
    "refund_to_different_payment_method": "normal",
    "identity_verification_failed": "normal",
    "other_out_of_authority": "normal",
}


def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "verify_customer",
            "description": "Verify the customer's identity with their booking reference (PNR) and registered email. "
                           "DO NOT guess these values. If the user hasn't provided BOTH, you must ask them for it first. "
                           "Must succeed before any booking data or action is available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_reference": {"type": "string", "description": "PNR, e.g. SK4821X"},
                    "email": {"type": "string", "description": "Email registered on the booking"},
                },
                "required": ["booking_reference", "email"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_booking",
            "description": "Get the verified customer's profile (name, loyalty tier, history) and all flight segments "
                           "with their live status. Use this before stating any fact about the customer's flights.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entitlements",
            "description": "Ask the policy engine what the customer is entitled to for one flight segment "
                           "(cancellation options, or delay compensation, plus what is NOT covered). "
                           "Always call this before promising anything.",
            "parameters": {
                "type": "object",
                "properties": {"flight": {"type": "string", "description": "Flight id, e.g. SK-204"}},
                "required": ["flight"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rebook_next_available",
            "description": "Free rebooking on the next available flight within 24 hours for an AIRLINE-CANCELLED flight. "
                           "Only call when the customer has chosen rebooking (not refund).",
            "parameters": {
                "type": "object",
                "properties": {"flight": {"type": "string"}},
                "required": ["flight"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "initiate_refund",
            "description": "Initiate a full refund for an AIRLINE-CANCELLED flight. Refunds go to the ORIGINAL payment "
                           "method only. Only call when the customer has chosen a refund (or clearly asked for their money back).",
            "parameters": {
                "type": "object",
                "properties": {
                    "flight": {"type": "string"},
                    "refund_destination": {
                        "type": "string",
                        "enum": ["original_payment_method", "different_method_requested"],
                        "description": "Use 'different_method_requested' ONLY if the customer explicitly insists on a "
                                       "different account/method. 'Cash refund' / 'money back' means original_payment_method.",
                    },
                },
                "required": ["flight"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_delay_compensation",
            "description": "Issue the meal voucher / lounge access / hotel (delayed hours only) that the policy grants for a "
                           "DELAYED flight. Applies exactly what the policy engine allows - nothing more.",
            "parameters": {
                "type": "object",
                "properties": {"flight": {"type": "string"}},
                "required": ["flight"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_fare_difference",
            "description": "Look up the fare difference for a higher-fare alternative flight the customer wants to move to, "
                           "and whether a waiver would need supervisor approval.",
            "parameters": {
                "type": "object",
                "properties": {"flight": {"type": "string", "description": "The customer's current flight segment"}},
                "required": ["flight"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_voluntary_rebooking",
            "description": "Record the customer's VOLUNTARY choice to move to a higher-fare flight. "
                           "ONLY call this after the customer has explicitly said they ACCEPT and will pay the fare difference (e.g. 'yes, I'll pay'). "
                           "Do NOT call this just because the customer asked about moving to another flight. "
                           "First use check_fare_difference to show the cost, then wait for explicit customer confirmation before calling this tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "flight": {"type": "string"},
                    "customer_accepts_fare_difference": {"type": "boolean"},
                },
                "required": ["flight", "customer_accepts_fare_difference"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Hand the case to a human agent/supervisor. MUST be called whenever the customer asks for something "
                           "outside agent authority - this includes: hotel for a delay of 5h or less, full-night hotel stay, "
                           "business class upgrades, extra vouchers beyond policy, fare waiver above ₹1,500, "
                           "exceptions for non-airline-caused issues, refund to another method, or legal threats. "
                           "Refusing in text alone is not enough - you must call this tool. Never promise the outcome of an escalation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(ESCALATION_CATEGORIES)},
                    "summary": {"type": "string", "description": "2-3 sentence handoff note for the human: situation, what has already been done, what is being asked."},
                    "customer_request": {"type": "string", "description": "What the customer is asking for, in their terms."},
                    "flight": {"type": "string", "description": "Related flight segment if any"},
                },
                "required": ["category", "summary", "customer_request"],
            },
        },
    },
]


class Toolbox:
    def __init__(self, session: Session):
        self.s = session

    # ---------------------------------------------------------------- dispatch
    def call(self, name: str, args: dict | None = None, actor: str = "agent") -> dict:
        args = args or {}
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            result: dict[str, Any] = {"status": "error", "reason": f"Unknown tool '{name}'."}
        elif name not in ("verify_customer", "escalate_to_human") and not self.s.verified_pnr:
            result = {"status": "rejected", "reason": "Customer is not verified.",
                      "next_step": "Ask for booking reference + registered email, then call verify_customer."}
        else:
            try:
                result = fn(**args)
            except TypeError as e:
                result = {"status": "error", "reason": f"Bad arguments: {e}"}
        self.s.log_action(name, args, result, actor=actor)
        return result

    # ---------------------------------------------------------------- helpers
    def _segment(self, flight: str | None) -> tuple[dict | None, dict | None]:
        segs = self.s.segments()
        if not flight:
            return None, {"status": "error", "reason": "No flight specified.",
                          "valid_flights": [x["flight"] for x in segs]}
        for seg in segs:
            if _norm(seg["flight"]) == _norm(flight):
                return seg, None
        return None, {"status": "error", "reason": f"Flight '{flight}' is not on this booking.",
                      "valid_flights": [x["flight"] for x in segs]}

    @staticmethod
    def _public(seg: dict) -> dict:
        keys = ("flight", "route", "date", "scheduled_departure", "status", "cause", "delay_minutes", "new_departure")
        return {k: seg[k] for k in keys if seg.get(k) is not None}

    # ---------------------------------------------------------------- tools
    def _t_verify_customer(self, booking_reference: str, email: str) -> dict:
        if self.s.verification_failures >= 3:
            return {"status": "locked", "reason": "Too many failed attempts.",
                    "next_step": "escalate_to_human(category='identity_verification_failed')"}
        pnr = _norm(booking_reference)
        cust = self.s.data["customers"].get(pnr)
        if cust and cust["email"].lower() == (email or "").strip().lower():
            self.s.verified_pnr = pnr
            return {"status": "ok", "customer_name": cust["name"], "loyalty_tier": cust["tier"]}
        self.s.verification_failures += 1
        return {"status": "rejected", "reason": "Booking reference and email do not match our records.",
                "attempts_left": 3 - self.s.verification_failures}

    def _t_get_my_booking(self) -> dict:
        c = self.s.customer
        return {
            "status": "ok",
            "booking_reference": self.s.verified_pnr,
            "customer": {"name": c["name"], "loyalty_tier": c["tier"],
                         "flights_last_12m": c["flights_last_12m"], "prior_complaints": c["prior_complaints"]},
            "segments": [self._public(x) for x in self.s.segments()],
            "today": self.s.now().strftime("%A %d %B %Y"),
        }

    def _t_get_entitlements(self, flight: str) -> dict:
        seg, err = self._segment(flight)
        if err:
            return err
        tier = self.s.customer["tier"]
        out: dict[str, Any] = {"status": "ok", "segment": self._public(seg)}
        if seg["status"] == "CANCELLED":
            out["entitlements"] = policy.cancellation_entitlements(tier, seg["cause"])
            out["already_done"] = self._done_for(seg["flight"])
        elif seg["status"] == "DELAYED":
            out["entitlements"] = policy.delay_entitlements(
                seg["delay_minutes"], seg["scheduled_departure"], seg["new_departure"])
            out["already_done"] = self._done_for(seg["flight"])
            if seg.get("voluntary_alternatives"):
                out["voluntary_alternative_on_file"] = True
        else:
            out["entitlements"] = {"applies": False, "reason": "Segment is unaffected - no disruption entitlements."}
        return out

    def _done_for(self, flight: str) -> list[str]:
        return [f"{k[0]}:{v['id']}" for k, v in self.s.issued.items() if k[1] == flight]

    def _t_rebook_next_available(self, flight: str) -> dict:
        seg, err = self._segment(flight)
        if err:
            return err
        if seg["status"] != "CANCELLED":
            return {"status": "rejected", "reason": f"{seg['flight']} is not cancelled; free rebooking applies only to airline cancellations."}
        ent = policy.cancellation_entitlements(self.s.customer["tier"], seg["cause"])
        if not ent["applies"]:
            return {"status": "rejected", "reason": ent["reason"],
                    "next_step": "escalate_to_human(category='non_airline_caused_exception')"}
        if ("refund", seg["flight"]) in self.s.issued:
            return {"status": "rejected", "reason": "Customer already chose a refund for this flight; rebooking and refund are alternatives."}
        key = ("rebooking", seg["flight"])
        if key in self.s.issued:
            return {"status": "already_done", **self.s.issued[key]}
        rec = {
            "id": self.s.next_id("REB"),
            "flight": seg["flight"],
            "charge_inr": 0,
            "window": f"next available flight within {policy.REBOOK_WINDOW_HOURS} hours",
            "priority_access": self.s.customer["tier"] in policy.PRIORITY_TIERS,
            "assigned_flight": None,
            "note": "Priority rebooking request placed. The data pack has no flight inventory, so no specific flight number "
                    "is assigned here - do NOT name one to the customer.",
        }
        self.s.issued[key] = rec
        return {"status": "ok", **rec}

    def _t_initiate_refund(self, flight: str, refund_destination: str = "original_payment_method") -> dict:
        seg, err = self._segment(flight)
        if err:
            return err
        if refund_destination != "original_payment_method":
            return {"status": "rejected",
                    "reason": "Refunds can only go to the original payment method. An agent cannot process another destination.",
                    "next_step": "Explain this. If the customer still insists, escalate_to_human(category='refund_to_different_payment_method')."}
        if seg["status"] != "CANCELLED":
            return {"status": "rejected", "reason": f"{seg['flight']} is not cancelled by the airline; no refund entitlement under policy.",
                    "next_step": "If the customer wants an exception, escalate_to_human(category='non_airline_caused_exception')."}
        ent = policy.cancellation_entitlements(self.s.customer["tier"], seg["cause"])
        if not ent["applies"]:
            return {"status": "rejected", "reason": ent["reason"],
                    "next_step": "escalate_to_human(category='non_airline_caused_exception')"}
        if ("rebooking", seg["flight"]) in self.s.issued:
            return {"status": "rejected", "reason": "Customer already chose free rebooking for this flight; rebooking and refund are alternatives."}
        key = ("refund", seg["flight"])
        if key in self.s.issued:
            return {"status": "already_done", **self.s.issued[key]}
        rec = {
            "id": self.s.next_id("REF"),
            "flight": seg["flight"],
            "amount": "full amount paid for the cancelled flight",
            "destination": "original payment method",
            "processing_time": f"within {policy.REFUND_SLA_BUSINESS_DAYS} business days",
            "scope_note": "Covers the cancelled segment only. Other segments on the booking (e.g. return flight) are unaffected.",
        }
        self.s.issued[key] = rec
        return {"status": "ok", **rec}

    def _t_apply_delay_compensation(self, flight: str) -> dict:
        seg, err = self._segment(flight)
        if err:
            return err
        if seg["status"] != "DELAYED":
            return {"status": "rejected", "reason": f"{seg['flight']} is not delayed; delay compensation does not apply."}
        ent = policy.delay_entitlements(seg["delay_minutes"], seg["scheduled_departure"], seg["new_departure"])
        if ent["policy_gap"]:
            return {"status": "rejected", "reason": "Delay length falls in a gap of the written policy.",
                    "next_step": "escalate_to_human(category='other_out_of_authority')"}
        issued, existing = [], []
        prefixes = {"meal_voucher": "VCH", "lounge_access": "LNG", "hotel_accommodation": "HTL"}
        for item in ent["entitled"]:
            key = (item["type"], seg["flight"])
            if key in self.s.issued:
                existing.append(self.s.issued[key])
                continue
            rec = {"id": self.s.next_id(prefixes[item["type"]]), "flight": seg["flight"], **item}
            self.s.issued[key] = rec
            issued.append(rec)
        return {
            "status": "ok" if issued else "already_done",
            "issued_now": issued,
            "issued_earlier": existing,
            "not_entitled": ent["not_entitled"],
            "sources": ent["sources"],
        }

    def _t_check_fare_difference(self, flight: str) -> dict:
        seg, err = self._segment(flight)
        if err:
            return err
        alts = seg.get("voluntary_alternatives") or []
        if not alts:
            return {"status": "not_found",
                    "reason": "No alternative flight / fare difference on file for this segment. Do not invent one."}
        alt = alts[0]
        return {"status": "ok", "alternative": alt["description"],
                **policy.fare_difference(alt["fare_difference_inr"])}

    def _t_request_voluntary_rebooking(self, flight: str, customer_accepts_fare_difference: bool) -> dict:
        seg, err = self._segment(flight)
        if err:
            return err
        alts = seg.get("voluntary_alternatives") or []
        if not alts:
            return {"status": "rejected", "reason": "No alternative flight on file for this segment."}
        fd = policy.fare_difference(alts[0]["fare_difference_inr"])
        if not customer_accepts_fare_difference:
            return {"status": "rejected",
                    "reason": "Customer has not agreed to pay the fare difference; agent cannot waive it.",
                    "next_step": "If the customer asks for a waiver: escalate_to_human(category='fare_difference_waiver')."
                    if fd["waiver_requires_supervisor"] else "Explain the fare difference again."}
        key = ("voluntary_rebooking", seg["flight"])
        if key in self.s.issued:
            return {"status": "already_done", **self.s.issued[key]}
        rec = {
            "id": self.s.next_id("VOL"),
            "flight": seg["flight"],
            "fare_difference_inr": fd["amount_inr"],
            "state": "PENDING_CUSTOMER_PAYMENT",
            "note": "Simulated: the switch is confirmed once the customer pays the fare difference via the normal payment flow "
                    "(payment is out of scope of this prototype).",
        }
        self.s.issued[key] = rec
        return {"status": "ok", **rec}

    def _t_escalate_to_human(self, category: str, summary: str, customer_request: str, flight: str | None = None) -> dict:
        if category not in ESCALATION_CATEGORIES:
            return {"status": "error", "reason": f"Unknown category. Use one of {list(ESCALATION_CATEGORIES)}"}
        for case in self.s.escalations:
            if case["category"] == category and case["flight"] == flight:
                case["requests"].append(customer_request)
                return {"status": "updated_existing_case", "case_id": case["case_id"], "category": category,
                        "note": "An open case for this already exists; the new request was added to it."}
        cust = self.s.customer
        case = {
            "case_id": self.s.next_id("CASE"),
            "opened_at": self.s.stamp(),
            "category": category,
            "priority": ESCALATION_CATEGORIES[category],
            "customer": {"pnr": self.s.verified_pnr, "name": cust["name"], "tier": cust["tier"],
                         "contact": {"email": cust["email"], "phone": cust["phone"]}} if cust else None,
            "flight": flight,
            "summary": summary,
            "requests": [customer_request],
            "actions_taken_so_far": [f"{a['tool']} → {a['status']}" for a in self.s.actions
                                     if a["tool"] not in ("verify_customer", "get_my_booking", "get_entitlements", "escalate_to_human")],
            "state": "OPEN - awaiting human agent",
        }
        self.s.escalations.append(case)
        return {"status": "ok", "case_id": case["case_id"], "priority": case["priority"],
                "note": "A human agent will contact the customer directly. Do not promise any outcome."}
