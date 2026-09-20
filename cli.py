"""Terminal chat.  python cli.py --scenario 1     (or --scenario 0 to be verified by the agent)"""
import argparse
import json
from dotenv import load_dotenv

load_dotenv()

from agent import LLMClient, LLMError, ResolutionAgent, Session
from agent.scenarios import SCENARIOS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", type=int, default=1, help="1=Priya 2=Arvind 3=Meher, 0=not signed in")
    ap.add_argument("--provider", default=None, help="gemini (default) | groq")
    ap.add_argument("--model", default=None)
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()

    try:
        llm = LLMClient(args.provider, model=args.model)
        if args.list_models:
            print("\n".join(llm.list_models()))
            return
    except LLMError as e:
        raise SystemExit(str(e))

    s = Session()
    if args.scenario:
        sc = SCENARIOS[args.scenario - 1]
        s.preverify(sc["pnr"])
        print(f"\n[Scenario {sc['id']}] {sc['title']}\n{sc['summary']}\nSuggested customer turns:")
        for t in sc["turns"]:
            print(f"  - {t}")
    else:
        print("\n[Not signed in] The agent will ask for booking reference + email. Demo: SK4821X / priya.nair@example.com")
    print("\nCommands: /log  /cases  /save  /quit\n")

    agent = ResolutionAgent(s, llm)
    while True:
        try:
            text = input("Customer > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/log":
            for a in s.actions:
                print(f"  {a['id']} [{a['actor']}] {a['tool']}({json.dumps(a['args'], ensure_ascii=False)}) → {a['status']}")
            continue
        if text == "/cases":
            print(json.dumps(s.escalations, indent=2, ensure_ascii=False))
            continue
        if text == "/save":
            with open("conversation_record.json", "w", encoding="utf-8") as f:
                json.dump(s.export(), f, indent=2, ensure_ascii=False)
            print("saved conversation_record.json")
            continue
        print(f"\nAgent    > {agent.respond(text)}\n")


if __name__ == "__main__":
    main()
