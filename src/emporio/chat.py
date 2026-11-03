"""Command-line chat with the store's agent.

    python -m emporio.chat            # talk to the agent
    python -m emporio.chat --debug    # also print every tool call and its result
    python -m emporio.chat --save examples/conversa.md   # write the transcript on exit

Commands inside the chat: /reset (new conversation), /sair (quit).
"""

import argparse
import json
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

import anthropic

from emporio.agent import ProviderError, Reply, make_agent
from emporio.etl import build_database
from emporio.paths import DB_PATH, PROJECT_ROOT

STORE = "Empório da Música:"
YOU = "Você:"
GREETING = f"{STORE} Oi! Sou o assistente virtual da Empório da Música. Como posso te ajudar?"


class Colors:
    """ANSI colors for the terminal, off when output is not a terminal or NO_COLOR is set
    (https://no-color.org), so --save transcripts and piped output stay plain."""

    CODES = {"you": "1;32", "store": "1;34", "tool": "2;37", "guardrail": "33", "info": "2", "error": "31"}

    def __init__(self, enabled: bool):
        self.enabled = enabled

    @classmethod
    def for_stdout(cls) -> "Colors":
        wanted = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        return cls(wanted and _enable_windows_ansi())

    def paint(self, text: str, role: str) -> str:
        return f"\033[{self.CODES[role]}m{text}\033[0m" if self.enabled else text


def _enable_windows_ansi() -> bool:
    """Turn on ANSI escape handling in the classic Windows console (already on elsewhere)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # stdout
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        return False


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines); real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def for_terminal(text: str, width: int | None = None) -> str:
    """Plain text wrapped to the terminal width.

    Markdown emphasis (**x**, __x__) is removed: a chat terminal shows it as literal asterisks.
    Long lines are wrapped because the Windows console misplaces the input cursor after a
    line the terminal had to wrap itself.
    """
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    width = (width or shutil.get_terminal_size().columns) - 1
    wrapped = []
    for line in text.splitlines():
        bullet = re.match(r"\s*(?:[-*•]|\d+[.)])\s+", line)
        indent = " " * len(bullet.group(0)) if bullet else ""
        wrapped.append(textwrap.fill(line, width=width, subsequent_indent=indent) if line.strip() else "")
    return "\n".join(wrapped)


def format_tool_calls(reply: Reply) -> list[str]:
    lines = [f"  [guardrail] {flag}" for flag in reply.flags]
    for call in reply.tool_calls:
        result = json.dumps(call.result, ensure_ascii=False)
        if len(result) > 300:
            result = result[:300] + "..."
        lines.append(f"  [tool] {call.name}({json.dumps(call.arguments, ensure_ascii=False)})")
        lines.append(f"         -> {result}")
    return lines


def store_reply(text: str, colors: Colors, width: int | None = None) -> str:
    """The agent's reply wrapped for the terminal, with the store's name colored."""
    wrapped = for_terminal(f"{STORE} {text}", width)
    return wrapped.replace(STORE, colors.paint(STORE, "store"), 1)


def debug_lines(lines: list[str], colors: Colors) -> str:
    return "\n".join(colors.paint(line, "guardrail" if "[guardrail]" in line else "tool") for line in lines)


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

    try:
        agent = make_agent()  # EMPORIO_MODEL picks Anthropic (default) or a free provider
    except ProviderError as exc:
        sys.exit(str(exc))
    colors = Colors.for_stdout()
    turns: list[tuple[str, str, list[str]]] = []
    print(GREETING.replace(STORE, colors.paint(STORE, "store"), 1))
    print(colors.paint("(comandos: /reset para recomeçar, /sair para encerrar)", "info") + "\n")
    try:
        while True:
            try:
                user = input(colors.paint(YOU, "you") + " ").strip()
            except EOFError:
                break
            if not user:
                continue
            if user.lower() in ("/sair", "/exit", "/quit"):
                break
            if user.lower() == "/reset":
                agent.reset()
                turns.clear()
                print(colors.paint("(nova conversa)", "info") + "\n")
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
                print(colors.paint("(sem conexão com a API; tente de novo)", "error") + "\n")
                continue
            except anthropic.APIStatusError as exc:
                print(colors.paint(f"(erro da API {exc.status_code}: {exc.message})", "error") + "\n")
                continue
            except agent.provider_errors as exc:  # free providers (OpenAI-compatible SDK)
                print(colors.paint(f"(erro da API: {exc})", "error") + "\n")
                continue
            tool_lines = format_tool_calls(reply)
            if args.debug and tool_lines:
                print(debug_lines(tool_lines, colors))
            print(store_reply(reply.text, colors) + "\n")
            turns.append((user, reply.text, tool_lines))
    except KeyboardInterrupt:
        print()
    finally:
        if args.save and turns:
            save_transcript(args.save, turns)  # always plain text, never colored
            print(colors.paint(f"Transcript saved to {args.save}", "info"))


if __name__ == "__main__":
    main()
