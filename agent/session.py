"""Session state: verified identity, conversation transcript, action record, escalations."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
# The exercise is set on Wed 23 Sep 2026; the simulated clock starts at 09:30 IST and ticks in real time.
SIM_START = datetime(2026, 9, 23, 9, 30, tzinfo=IST)
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "data_pack.json"


def load_data_pack(path: Path | str | None = None) -> dict:
    with open(path or DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Session:
    data: dict = field(default_factory=load_data_pack)
    started: float = field(default_factory=time.time)
    verified_pnr: str | None = None
    verification_failures: int = 0
    transcript: list = field(default_factory=list)   # user-visible conversation
    actions: list = field(default_factory=list)      # every tool call, incl. rejected ones
    escalations: list = field(default_factory=list)
    issued: dict = field(default_factory=dict)       # idempotency: (kind, flight) -> record
    turn: int = 0
    _counters: dict = field(default_factory=dict)

    # ---- clock / ids -------------------------------------------------
    def now(self) -> datetime:
        return SIM_START + timedelta(seconds=time.time() - self.started)

    def stamp(self) -> str:
        return self.now().strftime("%a %d %b %Y %H:%M:%S IST")

    def next_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]:04d}"

    # ---- data access (always scoped to the verified customer) ---------
    @property
    def customer(self) -> dict | None:
        return self.data["customers"].get(self.verified_pnr) if self.verified_pnr else None

    def segments(self) -> list[dict]:
        return [s for s in self.data["segments"] if s["pnr"] == self.verified_pnr]

    def preverify(self, pnr: str) -> None:
        """Simulates an already-authenticated app/web session (used by the demo UI)."""
        self.verified_pnr = pnr
        self.log_action("verify_customer", {"booking_reference": pnr, "method": "pre-authenticated session"},
                        {"status": "ok", "note": "Simulated logged-in customer"}, actor="system")

    # ---- records -------------------------------------------------------
    def add_message(self, role: str, content: str) -> None:
        self.transcript.append({"ts": self.stamp(), "turn": self.turn, "role": role, "content": content})

    def log_action(self, tool: str, args: dict, result: dict, actor: str = "agent") -> dict:
        rec = {
            "id": self.next_id("ACT"),
            "ts": self.stamp(),
            "turn": self.turn,
            "actor": actor,            # agent | guardrail | system
            "tool": tool,
            "args": args,
            "status": result.get("status", "ok"),
            "result": result,
        }
        self.actions.append(rec)
        return rec

    def export(self) -> dict:
        return {
            "exported_at": self.stamp(),
            "customer": {"pnr": self.verified_pnr, **({k: self.customer[k] for k in ("name", "tier")} if self.customer else {})},
            "conversation": self.transcript,
            "actions": self.actions,
            "escalations": self.escalations,
        }
