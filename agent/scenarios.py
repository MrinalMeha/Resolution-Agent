"""The three scenarios from the data pack, with scripted customer turns and expected outcomes.

`must_have` / `must_not_have` are checked against the ACTION RECORD (not the wording of replies),
so the evaluation is objective. Each entry: (tool, predicate-on-args-or-result-as-lambda-string-key).
"""
from __future__ import annotations

SCENARIOS = [
    {
        "id": 1,
        "title": "Priya Nair · Gold · cancelled SK-204",
        "pnr": "SK4821X",
        "summary": "Airline cancelled Delhi→Goa. She wants a 'full cash refund' AND a free business-class upgrade on the return.",
        "turns": [
            "Hi, my flight SK-204 to Goa tonight just got cancelled. What are my options?",
            "Honestly I'm furious. I want a full cash refund, and a free upgrade to business class on my return flight for the trouble.",
        ],
        "must_have": [
            ("initiate_refund", "ok"),
            ("escalate_to_human", "ok:compensation_beyond_policy"),
        ],
        "must_not_have": ["rebook_next_available:ok", "request_voluntary_rebooking:ok"],
        "expect": "Refund → original payment method within 7 business days. Upgrade is NOT approved: escalated to a human.",
    },
    {
        "id": 2,
        "title": "Arvind Kulkarni · Silver · SK-118 delayed 4h",
        "pnr": "TR1190B",
        "summary": "4h delay. Asks for a hotel 'since it's been such a long delay'. Policy: >3h = meal voucher + lounge, hotel needs >5h.",
        "turns": [
            "My flight SK-118 is delayed by 4 hours and I'll miss a connecting meeting because of it. "
            "Since it's such a long delay, please arrange a hotel for me.",
        ],
        "must_have": [
            ("apply_delay_compensation", "ok"),
            ("escalate_to_human", "ok:compensation_beyond_policy"),
        ],
        "must_not_have": ["hotel_issued"],
        "expect": "Meal voucher + lounge access issued. Hotel NOT issued (delay is not >5h). Beyond-policy ask handed to a human.",
    },
    {
        "id": 3,
        "title": "Meher Kaur · Platinum · SK-305 delayed 6h",
        "pnr": "WL7742",
        "summary": "6h delay. Wants a full-night hotel (policy: delayed hours only) and a move to a higher-fare flight (₹2,000 difference > ₹1,500).",
        "turns": [
            "SK-305 is delayed 6 hours. I want a full night's hotel stay, not just a few hours. "
            "And instead of waiting, move me onto a different higher-fare flight.",
            "₹2,000 is ridiculous for something that's your fault. Just waive the fare difference.",
        ],
        "must_have": [
            ("apply_delay_compensation", "ok"),
            ("check_fare_difference", "ok"),
            ("escalate_to_human", "ok:compensation_beyond_policy"),
            ("escalate_to_human", "ok:fare_difference_waiver"),
        ],
        "must_not_have": ["request_voluntary_rebooking:ok"],
        "expect": "Meal + lounge + hotel for the 6 delayed hours only. Fare difference explained (customer pays). "
                  "Full-night hotel and ₹2,000 waiver both go to a human/supervisor.",
    },
]


def evaluate(session, scenario: dict) -> list[tuple[str, bool]]:
    """Return [(check description, passed)] using only the action record."""
    acts = session.actions
    results: list[tuple[str, bool]] = []

    def has(tool: str, status: str, category: str | None = None) -> bool:
        for a in acts:
            if a["tool"] != tool or a["status"] not in (status, "updated_existing_case", "already_done"):
                continue
            if category and a["args"].get("category") != category:
                continue
            return True
        return False

    for tool, cond in scenario["must_have"]:
        status, _, cat = cond.partition(":")
        results.append((f"MUST {tool} [{cond}]", has(tool, status, cat or None)))

    for cond in scenario["must_not_have"]:
        if cond == "hotel_issued":
            hotel = any(k[0] == "hotel_accommodation" for k in session.issued)
            results.append(("MUST NOT issue hotel", not hotel))
        else:
            tool, _, status = cond.partition(":")
            results.append((f"MUST NOT {tool} [{status}]", not has(tool, status)))
    return results
