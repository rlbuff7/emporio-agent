from emporio.chat import for_terminal


def test_markdown_bold_is_removed():
    assert for_terminal("- **Tagima Memphis**: R$ 429,90", width=80) == "- Tagima Memphis: R$ 429,90"


def test_long_lines_wrap_with_bullet_indent():
    text = "- Giannini GN-15 Nylon Cedro Natural: R$ 689,00 à vista, ou R$ 654,55 no PIX, em até 6x"
    lines = for_terminal(text, width=41).splitlines()
    assert all(len(line) <= 40 for line in lines)
    assert lines[0].startswith("- Giannini") and lines[1].startswith("  ")


def test_blank_lines_are_kept():
    assert for_terminal("Oi!\n\nTudo bem?", width=80) == "Oi!\n\nTudo bem?"
