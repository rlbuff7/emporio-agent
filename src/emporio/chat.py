"""Command-line chat with the store's agent.

    python -m emporio.chat            # talk to the agent
    python -m emporio.chat --debug    # also print every tool call and its result
    python -m emporio.chat --save examples/conversa.md   # write the transcript on exit

Commands inside the chat: /reset (new conversation), /sair (quit).
"""

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic

from emporio.agent import Agent, Reply
from emporio.etl import build_database
from emporio.paths import DB_PATH, PROJECT_ROOT

GREETING = "Empório da Música: Oi! Sou o assistente virtual da Empório da Música. Como posso te ajudar?"


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines); real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def format_tool_calls(reply: Reply) -> list[str]:
    lines = [f"  [guardrail] {flag}" for flag in reply.flags]
    for call in reply.tool_calls:
        result = json.dumps(call.result, ensure_ascii=False)
        if len(result) > 300:
            result = result[:300] + "..."
        lines.append(f"  [tool] {call.name}({json.dumps(call.arguments, ensure_ascii=False)})")
        lines.append(f"         -> {result}")
    return lines


def save_transcript(path: Path, turns: list[tuple[str, str, list[str]]]) -> None:
    out = ["# Conversation transcript", ""]
    for user, answer, tools in turns:
        out += [f"**Cliente:** {user}", ""]
        if tools:
            out += ["```text", *tools, "```", ""]
        out += [f"**Empório da Música:** {answer}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the Empório da Música agent.")
    parser.add_argument("--debug", action="store_true", help="print tool calls and results")
    parser.add_argument("--save", type=Path, help="write the transcript to this Markdown file on exit")
    args = parser.parse_args()

    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")  # accents on the Windows console
    load_dotenv()
    if not DB_PATH.exists():
        build_database()

    agent = Agent()
    turns: list[tuple[str, str, list[str]]] = []
    print(GREETING)
    print("(comandos: /reset para recomeçar, /sair para encerrar)\n")
    try:
        while True:
            try:
                user = input("Você: ").strip()
            except EOFError:
                break
            if not user:
                continue
            if user.lower() in ("/sair", "/exit", "/quit"):
                break
            if user.lower() == "/reset":
                agent.reset()
                turns.clear()
                print("(nova conversa)\n")
                continue
            try:
                reply = agent.send(user)
            except anthropic.AuthenticationError:
                sys.exit("Invalid API key. Check ANTHROPIC_API_KEY in .env.")
            except TypeError as exc:
                if "authentication" not in str(exc):
                    raise
                sys.exit("No API key found. Copy .env.example to .env and set ANTHROPIC_API_KEY.")
            except anthropic.APIConnectionError:
                print("(sem conexão com a API; tente de novo)\n")
                continue
            except anthropic.APIStatusError as exc:
                print(f"(erro da API {exc.status_code}: {exc.message})\n")
                continue
            tool_lines = format_tool_calls(reply)
            if args.debug:
                print("\n".join(tool_lines))
            print(f"Empório da Música: {reply.text}\n")
            turns.append((user, reply.text, tool_lines))
    except KeyboardInterrupt:
        print()
    finally:
        if args.save and turns:
            save_transcript(args.save, turns)
            print(f"Transcript saved to {args.save}")


if __name__ == "__main__":
    main()
