"""Run all three scenarios end-to-end with the real LLM, check the ACTION RECORD against expectations,
and write transcripts to ./outputs/ (useful evidence for the demo video / slides).

    python -m scripts.run_scenarios            # all three
    python -m scripts.run_scenarios --only 3
"""
import argparse
import json
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from agent import LLMClient, LLMError, ResolutionAgent, Session
from agent.scenarios import SCENARIOS, evaluate

OUT = Path("outputs")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int)
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between turns (free-tier rate limits)")
    args = ap.parse_args()

    try:
        llm = LLMClient(args.provider, model=args.model)
    except LLMError as e:
        raise SystemExit(str(e))

    OUT.mkdir(exist_ok=True)
    summary = []
    for sc in SCENARIOS:
        if args.only and sc["id"] != args.only:
            continue
        s = Session()
        s.preverify(sc["pnr"])
        agent = ResolutionAgent(s, llm)
        print(f"\n=== Scenario {sc['id']}: {sc['title']} ===")
        md = [f"# Scenario {sc['id']}: {sc['title']}", "", sc["summary"], ""]
        for turn in sc["turns"]:
            print(f"\nCUSTOMER: {turn}")
            reply = agent.respond(turn)
            print(f"AGENT:    {reply}")
            md += [f"**Customer:** {turn}", "", f"**Agent:** {reply}", ""]
            time.sleep(args.pause)
        checks = evaluate(s, sc)
        print("\nChecks against action record:")
        for desc, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
        md += ["## Action record", ""] + [
            f"- `{a['id']}` ({a['actor']}) **{a['tool']}** {json.dumps(a['args'], ensure_ascii=False)} → {a['status']}" for a in s.actions]
        md += ["", "## Escalations", ""] + [f"- `{c['case_id']}` {c['category']} ({c['priority']}): {c['summary']}" for c in s.escalations]
        md += ["", "## Checks", ""] + [f"- {'✅' if ok else '❌'} {d}" for d, ok in checks]
        (OUT / f"scenario_{sc['id']}.md").write_text("\n".join(md), encoding="utf-8")
        (OUT / f"scenario_{sc['id']}_record.json").write_text(json.dumps(s.export(), indent=2, ensure_ascii=False), encoding="utf-8")
        summary.append((sc["id"], all(ok for _, ok in checks)))

    print("\n=== Summary ===")
    for i, ok in summary:
        print(f"Scenario {i}: {'PASS' if ok else 'FAIL (LLM behaviour varies - re-run or inspect outputs/)'}")


if __name__ == "__main__":
    main()
