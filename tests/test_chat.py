from emporio.chat import Colors, debug_lines, for_terminal, store_reply


def test_colors_off_leave_text_plain():
    assert store_reply("Oi!", Colors(False), width=80) == "Empório da Música: Oi!"
    assert debug_lines(["  [tool] x"], Colors(False)) == "  [tool] x"


def test_colors_on_paint_only_the_labels():
    reply = store_reply("Oi! R$ 10,00", Colors(True), width=80)
    assert reply.startswith("\033[1;34mEmpório da Música:\033[0m")
    assert reply.endswith(" Oi! R$ 10,00")  # the reply text itself is never colored


def test_debug_lines_use_their_own_colors():
    out = debug_lines(["  [guardrail] input_suspicious", "  [tool] get_product({})"], Colors(True)).splitlines()
    assert out[0].startswith("\033[33m") and out[1].startswith("\033[2;37m")


def test_no_color_env_and_pipes_disable_colors(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert Colors.for_stdout().enabled is False
    monkeypatch.delenv("NO_COLOR")
    assert Colors.for_stdout().enabled is False  # pytest captures stdout: not a terminal


def test_markdown_bold_is_removed():
    assert for_terminal("- **Tagima Memphis**: R$ 429,90", width=80) == "- Tagima Memphis: R$ 429,90"


def test_long_lines_wrap_with_bullet_indent():
    text = "- Giannini GN-15 Nylon Cedro Natural: R$ 689,00 à vista, ou R$ 654,55 no PIX, em até 6x"
    lines = for_terminal(text, width=41).splitlines()
    assert all(len(line) <= 40 for line in lines)
    assert lines[0].startswith("- Giannini") and lines[1].startswith("  ")


def test_blank_lines_are_kept():
    assert for_terminal("Oi!\n\nTudo bem?", width=80) == "Oi!\n\nTudo bem?"
