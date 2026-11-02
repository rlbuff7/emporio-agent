"""Draw the model comparison chart for the README from the eval results.

    python evals/plot_comparison.py

Writes docs/model_comparison_light.svg and docs/model_comparison_dark.svg (the README
picks one by the reader's theme). Standard library only.

Three panels share the model rows: scenarios passed, seconds per reply, and cost of one
full eval run. They are separate panels because the three measures have different units.
Scenarios that hit a free-tier rate limit are excluded from that model's counts, not
scored as failures. Gemini is left out: only one scenario ran before its free quota ran
out, which is too few to plot.
"""

import json
from html import escape
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
DOCS = Path(__file__).parent.parent / "docs"

MODELS = [
    # (label, results file, chosen?)
    ("Claude Haiku 5.5", "claude-haiku-5-5.json", True),
    ("Claude Sonnet 5.5", "claude-sonnet-5-5.json", False),
    ("Groq gpt-oss-120b", None, False),
    ("Groq gpt-oss-20b", "groq__openai_gpt-oss-20b.json", False),
]

# The gpt-oss-120b run crashed before saving its JSON (see docs/testing.md, finding 5).
# These figures come from its run log: 16 scenarios ran, 12 passed, 732 s over 24 messages.
GPT_OSS_120B = {"passed": 12, "ran": 16, "seconds_per_reply": 732.0 / 24, "cost": 0.0}

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
              "axis": "#c3c2b7", "chosen": "#2a78d6", "other": "#898781"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
             "axis": "#383835", "chosen": "#3987e5", "other": "#898781"},
}


def summarize(filename: str) -> dict:
    data = json.loads((RESULTS / filename).read_text(encoding="utf-8"))
    ran = [r for r in data["results"] if not any(f.startswith("api error") for f in r["failures"])]
    messages = sum(len(r["replies"]) for r in ran)
    return {
        "passed": sum(r["passed"] for r in ran),
        "ran": len(ran),
        "seconds_per_reply": sum(r["seconds"] for r in ran) / messages,
        "cost": data["summary"]["estimated_cost_usd"] or 0.0,
    }


def rows() -> list[dict]:
    out = []
    for label, filename, chosen in MODELS:
        stats = summarize(filename) if filename else GPT_OSS_120B
        out.append({"label": label, "chosen": chosen, **stats})
    return out


def cost_text(d: dict) -> str:
    return "free" if d["cost"] == 0 else f'US$ {d["cost"]:.2f}'


def bar(x: float, y: float, length: float, height: float, color: str) -> str:
    """Horizontal bar anchored at x, with only its data end rounded (4px)."""
    if length <= 0:
        return ""
    r = min(4.0, length, height / 2)
    return (f'<path d="M{x:.1f},{y:.1f} h{length - r:.1f} a{r},{r} 0 0 1 {r},{r} '
            f'v{height - 2 * r:.1f} a{r},{r} 0 0 1 {-r},{r} h{-(length - r):.1f} z" fill="{color}"/>')


def render(data: list[dict], theme: dict) -> str:
    width, label_w, panel_w, gap = 820, 150, 200, 24
    top, row_h, bar_h = 74, 34, 14
    height = top + row_h * len(data) + 46
    panels = [
        ("Scenarios passed", "of those that ran", lambda d: d["passed"] / d["ran"], 1.0,
         lambda d: f'{d["passed"]}/{d["ran"]}'),
        ("Seconds per reply", "lower is better", lambda d: d["seconds_per_reply"],
         max(d["seconds_per_reply"] for d in data), lambda d: f'{d["seconds_per_reply"]:.1f} s'),
        ("Cost of one eval run", "US$, lower is better", lambda d: d["cost"],
         max(d["cost"] for d in data), cost_text),
    ]
    font = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Model comparison on the eval suite</title>',
        '<desc id="desc">' + escape("; ".join(
            f'{d["label"]}: {d["passed"]} of {d["ran"]} passed, '
            f'{d["seconds_per_reply"]:.1f} s per reply, {cost_text(d)} per eval run'
            for d in data)) + '</desc>',
        f'<rect width="{width}" height="{height}" fill="{theme["surface"]}"/>',
        f'<text x="0" y="22" {font} font-size="16" font-weight="600" fill="{theme["ink"]}">'
        'Same tools and guardrails, different models</text>',
    ]
    for i, (title, subtitle, value, scale, text) in enumerate(panels):
        px = label_w + i * (panel_w + gap)
        bar_max = panel_w - 64  # room for the value label
        svg.append(f'<text x="{px}" y="50" {font} font-size="13" font-weight="600" fill="{theme["ink"]}">{title}</text>')
        svg.append(f'<text x="{px}" y="65" {font} font-size="11" fill="{theme["muted"]}">{subtitle}</text>')
        svg.append(f'<line x1="{px}" y1="{top - 4}" x2="{px}" y2="{top + row_h * len(data) - 8}" '
                   f'stroke="{theme["axis"]}" stroke-width="1"/>')
        for j, d in enumerate(data):
            y = top + j * row_h
            length = bar_max * value(d) / scale if scale else 0
            color = theme["chosen"] if d["chosen"] else theme["other"]
            svg.append(bar(px, y, length, bar_h, color))
            svg.append(f'<text x="{px + length + 6:.1f}" y="{y + bar_h - 2}" {font} font-size="12" '
                       f'fill="{theme["ink2"]}">{text(d)}</text>')
    for j, d in enumerate(data):
        y = top + j * row_h + bar_h - 2
        weight = "600" if d["chosen"] else "400"
        svg.append(f'<text x="0" y="{y}" {font} font-size="12" font-weight="{weight}" '
                   f'fill="{theme["ink"]}">{escape(d["label"])}</text>')
    svg.append(f'<text x="0" y="{height - 14}" {font} font-size="11" fill="{theme["muted"]}">'
               'One run each, 2026-11-02. Groq ran on its free tier; scenarios cut off by rate limits are '
               'excluded. Haiku: 19 scenarios; Sonnet: earlier 18-scenario suite.</text>')
    svg.append("</svg>")
    return "\n".join(svg)


def main() -> None:
    data = rows()
    for name, theme in THEMES.items():
        out = DOCS / f"model_comparison_{name}.svg"
        out.write_text(render(data, theme), encoding="utf-8")
        print(f"wrote {out}")
    for d in data:
        print(f'  {d["label"]:<20} {d["passed"]}/{d["ran"]}  {d["seconds_per_reply"]:.1f} s  ${d["cost"]:.3f}')


if __name__ == "__main__":
    main()
