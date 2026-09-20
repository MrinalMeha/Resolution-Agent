"""Deterministic policy engine.

The LLM never decides *what a customer is entitled to*. It calls tools; the tools call
this module. Every number and rule below comes from Section 3 / 4 of the data pack.
"""
from __future__ import annotations

PRIORITY_TIERS = {"Gold", "Platinum"}          # Loyalty Tier Rule
AIRLINE_CAUSED = {"operational"}               # causes we treat as airline-caused
MEAL_VOUCHER_UNDER_3H_INR = 500                # Delay Compensation Rule (<3h)
FARE_WAIVER_SUPERVISOR_ABOVE_INR = 1500        # Fare Difference Rule
REFUND_SLA_BUSINESS_DAYS = 7                   # Refund Processing Rule
REBOOK_WINDOW_HOURS = 24                       # Cancellation Rebooking Rule

RULE_SOURCES = {
    "cancellation": "Service Rules → Cancellation Rebooking Rule",
    "delay": "Service Rules → Delay Compensation Rule",
    "refund": "Service Rules → Refund Processing Rule",
    "fare_difference": "Service Rules → Fare Difference Rule",
    "loyalty": "Service Rules → Loyalty Tier Rule",
    "allowed_prohibited": "Allowed vs. Prohibited Actions for the Agent",
}


def cancellation_entitlements(tier: str, cause: str | None) -> dict:
    if cause not in AIRLINE_CAUSED:
        return {
            "applies": False,
            "reason": "Cancellation is not recorded as airline-caused, so the free rebooking/refund rule does not apply.",
            "must_escalate": "non_airline_caused_exception",
            "sources": [RULE_SOURCES["allowed_prohibited"]],
        }
    return {
        "applies": True,
        "customer_chooses_one_of": [
            {
                "option": "free_rebooking",
                "detail": f"Next available flight within {REBOOK_WINDOW_HOURS} hours at no charge.",
            },
            {
                "option": "full_refund",
                "detail": (
                    f"Full refund processed within {REFUND_SLA_BUSINESS_DAYS} business days, "
                    "to the ORIGINAL payment method only."
                ),
            },
        ],
        "priority_rebooking": tier in PRIORITY_TIERS,
        "tier_note": (
            "Gold/Platinum get first access to next-available seats. "
            "No additional compensation beyond standard policy for any tier."
        ),
        "not_covered": [
            "Upgrades (e.g. business class) - not part of policy; needs a human agent",
            "Refund to a different payment method - needs a human agent",
            "Any compensation beyond the policy - needs a human agent",
        ],
        "sources": [RULE_SOURCES["cancellation"], RULE_SOURCES["refund"], RULE_SOURCES["loyalty"]],
    }


def delay_entitlements(delay_minutes: int, scheduled: str, new_departure: str) -> dict:
    hours = delay_minutes / 60
    hours_txt = int(hours) if float(hours).is_integer() else round(hours, 1)
    entitled: list[dict] = []
    not_entitled: list[dict] = []
    policy_gap = False

    if hours < 3:
        entitled.append({"type": "meal_voucher", "amount_inr": MEAL_VOUCHER_UNDER_3H_INR})
    elif hours == 3:
        # Rule says "under 3" and "more than 3" - exactly 3 is not covered.
        policy_gap = True
    else:
        entitled.append({
            "type": "meal_voucher",
            "amount_inr": None,
            "note": "Amount for the >3h tier is not specified in the policy; issue the standard meal voucher.",
        })
        entitled.append({"type": "lounge_access"})

    if hours > 5:
        entitled.append({
            "type": "hotel_accommodation",
            "hours_covered": hours_txt,
            "covers": f"{scheduled} → {new_departure} (delayed hours only, not a full night)",
        })
    else:
        not_entitled.append({
            "type": "hotel_accommodation",
            "reason": f"Hotel only applies to delays of MORE than 5 hours; this delay is {hours_txt}h.",
        })

    not_entitled.append({
        "type": "full_night_hotel_stay",
        "reason": "Hotel cover is for the delayed hours only, never a full night's stay.",
    })

    return {
        "delay_hours": hours_txt,
        "entitled": entitled,
        "not_entitled": not_entitled,
        "policy_gap": policy_gap,
        "tier_note": "Loyalty tier gives no extra compensation on delays.",
        "assumption": "Delay tiers are treated as cumulative thresholds (a 6h delay is also 'more than 3h', so lounge access applies).",
        "sources": [RULE_SOURCES["delay"], RULE_SOURCES["loyalty"]],
    }


def fare_difference(amount_inr: int) -> dict:
    needs_supervisor = amount_inr > FARE_WAIVER_SUPERVISOR_ABOVE_INR
    return {
        "amount_inr": amount_inr,
        "customer_pays_by_default": True,
        "waiver_requires_supervisor": needs_supervisor,
        "agent_may_waive": False if needs_supervisor else None,
        "note": (
            f"Voluntary move to a higher-fare flight: customer pays the difference. "
            f"Waivers above ₹{FARE_WAIVER_SUPERVISOR_ABOVE_INR:,} need supervisor approval."
        ),
        "sources": [RULE_SOURCES["fare_difference"]],
    }
