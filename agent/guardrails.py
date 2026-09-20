"""Deterministic guardrails that run BEFORE the LLM.

Data pack, Section 4: threats of legal action or formal complaints "must be escalated immediately".
That is too important to leave to model judgement, so a regex backstop escalates first and tells
the LLM to only acknowledge and hand over.
"""
from __future__ import annotations

import re

_LEGAL_PATTERNS = [
    r"\blegal (action|notice|steps|proceedings)\b",
    r"\b(my |our )?(lawyer|attorney|advocate)s?\b",
    r"\bsue\b|\bsuing\b|\blawsuit\b",
    r"\bconsumer (court|forum|commission|protection)\b",
    r"\b(in|to|at) court\b|\bcourt case\b|\btake (you|this|it) to court\b",
    r"\b(formal|official|written) complaint\b",
    r"\b(file|filing|lodge|lodging|raise|register)\b.{0,25}\bcomplaint\b",
    r"\bcomplaint (against|to|with) (the )?(dgca|airline|ombudsman|regulator|authority|authorities)\b",
    r"\b(dgca|ombudsman|airsewa)\b",
]
_LEGAL_RE = re.compile("|".join(_LEGAL_PATTERNS), re.IGNORECASE)


def detect_legal_or_formal_complaint(text: str) -> bool:
    return bool(_LEGAL_RE.search(text or ""))
