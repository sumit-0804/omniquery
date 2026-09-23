import sys

from langchain_core.messages import HumanMessage

from agents.data_agent import data_agent


def ask(question: str) -> str:
    result = data_agent.invoke({"messages": [HumanMessage(content=question)]})
    return result["messages"][-1].content


def main() -> int:
    if len(sys.argv) > 1:
        print(ask(" ".join(sys.argv[1:])))
        return 0

    print("OmniQuery. Ask a question, or press Ctrl-C to quit.")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question:
            print(ask(question))


if __name__ == "__main__":
    raise SystemExit(main())
