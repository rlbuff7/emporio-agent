# Empório da Música — customer-service agent

A text-chat agent for **Empório da Música**, a fictional musical-instrument store in Campo Grande/MS.
It answers customers in Brazilian Portuguese, looks up products, prices, promotions and orders in
the store's data, applies the store's policy manual, opens tickets for the human team when a
decision is not its to make, and refuses what is out of scope.

Built for the Artefact AI Engineer technical challenge. Python, the Anthropic SDK called directly
(no agent framework), SQLite, and a command-line chat.

```text
Você: Quanto custa o Crafter HT-100? Tem desconto no PIX?
  [tool] get_product({"name_or_id": "Crafter HT-100"})
Empório da Música: O Crafter HT-100 Folk Aço Natural está em promoção "Desconto Exclusivo":
- Preço original: R$ 2.399,00
- Desconto: 18%
- Preço final: R$ 1.967,18
- Parcelamento: até 12x de R$ 163,93 sem juros
Sobre o PIX: o desconto de 5% não se aplica a produtos que já estão em promoção...
```

---

## Quickstart

Requirements: Python 3.11+ and an Anthropic API key ([console.anthropic.com](https://console.anthropic.com)).

```bash
git clone https://github.com/rlbuff7/emporio-agent.git
cd emporio-agent
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env              # then set ANTHROPIC_API_KEY in .env

python -m emporio.etl             # builds data/emporio.db from the CSVs (the chat also does this if missing)
python -m emporio.chat            # talk to the agent
python -m emporio.chat --debug    # ...and see every tool call, its result and guardrail events
pytest                            # 118 offline tests, no API key needed
```

Inside the chat: `/reset` starts a new conversation, `/sair` quits. `--save file.md` writes the
transcript to Markdown on exit (that is how `examples/` was produced).

Optional:

```bash
python evals/run_evals.py                          # 18 live scenarios graded in code (~US$ 0.02 with Haiku)
EMPORIO_MODEL=claude-sonnet-5-5 python -m emporio.chat          # another Anthropic model
pip install -e ".[free]"                                        # free providers, see docs/model_choice.md
EMPORIO_MODEL=gemini:gemini-3.5-flash python -m emporio.chat    # needs GEMINI_API_KEY
EMPORIO_FALLBACK_MODEL=gemini:gemini-3.1-flash-lite,groq:openai/gpt-oss-20b python -m emporio.chat  # Claude → Gemini → Groq
```

---

## Architecture

```text
 customer message
        │
        ▼
 ┌──────────────────────┐   too long → polite refusal, model never called
 │ 1. input guardrail   │   injection pattern → one-turn system reminder
 └──────────┬───────────┘
            ▼
 ┌──────────────────────┐   system prompt = persona + hard rules + full policy manual
 │   LLM (Claude Haiku) │◄─────────────── (static, cached across turns)
 └──────────┬───────────┘
   tool_use │  ▲ tool results (JSON)
            ▼  │
 ┌──────────────────────┐   read-only SQLite · identity check in code · lockout after
 │ 2. tools (Python)    │   3 failed verifications · 3 tickets/session · whitelist
 └──────────────────────┘
            │ final text
            ▼
 ┌──────────────────────┐   every "R$" must come from a tool, the customer or the manual;
 │ 3. output guardrail  │   no system-prompt leak → else regenerate once → else safe reply
 └──────────┬───────────┘
            ▼
       reply shown
```

| File | Role |
|---|---|
| `src/emporio/etl.py` | CSVs → SQLite with typing, NULLs, validated JSON and an `active_promotions` view |
| `src/emporio/tools.py` | The seven tools. All arithmetic lives here: promotion and PIX prices, installments, return and warranty windows, opening hours |
| `src/emporio/tool_schemas.py` | Tool definitions as the model sees them |
| `src/emporio/prompts.py` | System prompt: persona, rules, resolved contradictions in the manual, and the manual itself |
| `src/emporio/agent.py` | The loop. `BaseAgent` holds the provider-independent flow and the guardrails; `Agent` is the Anthropic adapter; `FallbackAgent` hands a turn to a second provider when Claude is down |
| `src/emporio/guardrails.py` | Input and output checks |
| `src/emporio/compat.py` | Adapter for OpenAI-compatible providers (Gemini, Groq...), used for the model comparison and as the fallback |
| `src/emporio/chat.py` | CLI |
| `data/policies.md` | The policy PDF converted to Markdown, verbatim |

**Tools.**

| Tool | Purpose |
|---|---|
| `search_products` | Catalog search with category, price, strings and pickup filters; sorted by final price |
| `get_product` | One product by name or id, with fuzzy matching, specs, ended promotions and alternatives when unavailable |
| `get_active_promotions` | Original price, discount and final price |
| `get_order_status` | Requires the order number **and** that order's e-mail or phone |
| `get_current_datetime` | Campo Grande time, open or closed, holiday, next opening |
| `get_opening_hours` | Whether the store opens on any date (weekends, national, state and Campo Grande holidays) |
| `escalate_to_human` | Opens a ticket in `data/handoffs.jsonl` and returns its number |

---

## Technical decisions

| Decision | Choice | Why |
|---|---|---|
| Agent approach | **Function calling for data, full manual in the system prompt for policies** | Prices, stock and orders are tabular and need exact queries, never generated text. The manual is ~8 pages (~4k tokens): it fits in context, so retrieval would add a failure mode (a missed chunk) and buy nothing. If the manual grew, the next step would be RAG with one chunk per numbered section. |
| Framework | **None**: a small loop on the Anthropic SDK | The loop is small, readable and easy to debug: one place shows exactly what goes to the model and what comes back. A framework would hide the parts the challenge evaluates. |
| Model | **`claude-haiku-5-5`** | Measured, not assumed, on 18 graded scenarios. Haiku 5.5 scored 18/18 at 3.0 s per reply for US$ 0.02 per suite. Sonnet 5.5 also scored 18/18, but slower and ~25x the cost. The free models (Groq `gpt-oss`, Gemini Flash) missed facts customers ask for and were slowed to 6–130 s by rate limits. Details: [`docs/model_choice.md`](docs/model_choice.md). |
| Where numbers come from | **Code only** | The model never computes a price, discount, installment or deadline. Tools return final values, and the output guardrail rejects any `R$` amount that no tool returned. |
| Database | **SQLite generated from the CSVs** | Zero infrastructure, real SQL, reproducible with one command. The tools open it read-only. |
| Interface | **CLI** | The focus is the agent. `--debug` shows tool calls, which makes the behaviour auditable. |
| Conversation history | **In memory, per session** | Needed for multi-turn flows such as identity verification. Persisting it would mean storing personal data, with no benefit for a prototype. |
| Human handoff | **Tool that writes a ticket** | The policy spec splits use cases into AUTO / TRIAGE / HUMAN. The bot never approves or denies a return, exchange, warranty or discount: it checks eligibility, collects data and opens a ticket. |
| Prompt strategy | Static system prompt (persona → rules → resolved contradictions → manual), cached | Static content caches well: most input tokens are cache reads. Anything time-dependent comes from a tool, never from the prompt. |
| Safety | Three layers: input heuristics, tools that enforce rules in code, output validation | Prompt rules alone can be argued with. The layers that matter (identity, prices) are in code, so a successful injection still cannot read another customer's order or invent a discount. |
| Availability | **Optional fallback chain** (`EMPORIO_FALLBACK_MODEL`): Claude → Gemini Flash Lite → Groq `gpt-oss-20b` | If Claude returns 429, 5xx or a network error, that turn goes to the next provider in the chain (free tiers), with the same tools and guardrails; if that one is down too, the next one. The following turn goes back to Claude. I chose this over rotating models per message, which changes the tone mid-conversation and saves almost nothing at these prices. |

---

## Data handling

Full report: [`docs/data_quality.md`](docs/data_quality.md). It lists 15 findings, the decision for
each, and every data finding is pinned by a test. Highlights:

- **Descriptions contradict names and specs** for basses, drums and keyboards: a "Music Man" bass is
  described as a Fender, and an "88-key" description sits on a 61-key spec. The agent uses name,
  specs and price, and does not quote those descriptions.
- **Promotions have no dates**, only an `is_active` flag and a campaign label. Today is November,
  Black Friday month, yet the Black Friday rows are inactive. The flag wins, and the agent never
  infers a discount from the calendar.
- **Order totals differ from current prices** for orders 3 and 20, and `order_items` has no unit
  price. The recorded total is shown as the amount charged.
- **Two customers share an e-mail.** Identity is verified per order (order number plus that
  order's e-mail or phone), never by e-mail alone.
- **The manual contradicts itself** on the phone number, the e-mail spelling and the installment
  minimums. Each choice is documented and stated in the prompt.

---

## Assumptions

- **"Today"** is the real date in Campo Grande (UTC-4, no daylight saving).
- **Return windows count from receipt**, but the data has no receipt date. `estimated_delivery` is
  used as an estimate, and the agent asks the customer to confirm it before stating eligibility.
- **Holidays:** national, Mato Grosso do Sul and Campo Grande holidays close the store (Good Friday is
  computed from Easter). Carnival, Corpus Christi and the 24/12 and 31/12 eves are "pontos
  facultativos" in Decreto Municipal 16.469, so the store opens. The two municipal holidays come
  from the press and the TJMS calendar; the law creating them was not located.
- **Phone vs. WhatsApp:** (67) 3341-4444 is the phone and (67) 3321-4500 the WhatsApp, because the
  manual's section 7 is specifically about the channel.
- **Installments:** section 3.1's tiers are used. "No minimum, except below R$ 50" is read as a
  R$ 50 minimum installment for up to 3x; the cheapest product is R$ 159.90, so this never changes
  an answer.
- **Shipping to other cities** needs CEP, weight and dimensions that the data does not have. The
  agent opens a quote ticket instead of guessing.
- **The customers are Brazilian**, so the agent replies in Portuguese, and everything else in the
  repository is in English, as requested.

---

## Example conversations

Generated with the CLI in `--debug` mode, so each shows the tools the agent called.

1. [Catalog search with filters](examples/01_catalog_search.md): "violões até R$ 1000", then nylon for a beginner.
2. [Price, promotion and the PIX rule](examples/02_price_promotion_pix.md): the discounts do not stack.
3. **Non-trivial:** [Late order and complaint](examples/03_late_order_complaint.md). Identity check,
   a 247-day delay computed in code, a complaint ticket, and the store closed for a holiday.
4. **Non-trivial:** [Return request outside the window](examples/04_return_policy.md). The policy
   is applied to real dates, and nothing is approved by the bot.
5. [Out of scope, store info and prompt injection](examples/05_out_of_scope_and_hours.md).

---

## Testing

- **118 offline tests** (`pytest`, no key, no network). They cover the ETL, every data-quality finding,
  every tool rule (pricing, PIX, installments, LGPD, lockout, windows, hours), the agent loop with
  scripted fake clients, both adapters, the fallback, and the guardrails.
- **18 live scenarios** (`evals/run_evals.py`) graded in code:
  - **7 functional:** catalog, promotion and PIX, an expired promotion, a late order, the return
    window, scope and hours, and a lookup with the wrong e-mail.
  - **11 adversarial:** role swap, a fake manager, data exfiltration, orders looked up by name,
    e-mail brute force, a fake `<system>` tag, a wrong claim about consumer law, a prompt leak, a
    competitor price match, a request to add prices, and a mixed off-topic question.

  Results per model are in `evals/results/`.
- **Manual testing** in the CLI found problems the scripted tests did not: expired options listed
  as still available, terminal display issues, a date question answered with a ticket. Every
  finding and the commit that fixed it is in [`docs/testing.md`](docs/testing.md).

---

## Known limitations

- **18 live scenarios with one run each** is a smoke test, not a benchmark: model output varies
  between runs.
- **The agent will not add prices together** ("os dois juntos dão quanto?"). It lists each price,
  because the output guardrail allows only amounts that a tool returned. A `quote_total` tool would
  fix this properly.
- **Fuzzy product search is substring-based.** Typos such as "Takamini" are not matched.
- **The injection heuristic is a list of patterns.** It only adds a reminder; the real protection is
  in the tools and the output check.
- **Single process, single conversation, in-memory state.** Tickets go to a local JSONL file. The
  verification lockout is per session, so a restart resets it; a real channel would key it by phone
  number.
- **No real channel:** no WhatsApp integration, and no out-of-hours auto-reply flow beyond what the
  agent says.

## What I would do with more time

- **A paid fallback.** The current fallback uses the Gemini free tier, which in testing hit its
  per-minute limit after about 8 requests. A paid fallback would make availability predictable.
- **A bigger eval set** with repeated runs and an LLM judge for tone, run in CI on prompt changes.
- **A `quote_total` tool** for carts and proposals (UC-03 in the spec): totals, PIX and installments,
  computed in code.
- **Persistence and channel:** conversations and tickets in a real database, the WhatsApp Business
  API, and an out-of-hours queue.
- **Observability:** tracing of every tool call and guardrail event, and dashboards for handoff
  rate and failed verifications.
- **RAG with one chunk per manual section,** if the manual grows past what fits comfortably in
  context.
- **Payments (deliberately out of scope).** A payment gateway behind an adapter, PIX or a payment
  link generated by code, an idempotent webhook to confirm, stock deducted only after payment, and
  humans only for exceptions.

---

## How I used an AI coding assistant

I used **Claude Code** as a pair programmer, with this workflow:

1. **Plan before code.** Before any code, I prepared a working plan and a business-rules
   spec that maps every manual section to a use case with an autonomy level. Both are in
   Portuguese and kept out of the repository. The repo's `CLAUDE.md` holds the standing rules: English
   deliverables, Portuguese replies, no framework, numbers only in code, one phase at a time, small
   commits.
2. **One phase at a time, with my review between phases.** Claude Code proposed each phase's plan
   (ETL → tools → agent → guardrails → examples → README). I reviewed it, changed what I disagreed
   with, then let it execute and commit. The commit history follows those phases.
3. **My decisions:**
   - CLI over a WhatsApp mock, to keep it simple.
   - SQLite over a database server.
   - A **three-layer guardrail**: I asked for checks at the start, in the middle and at the end of
     each turn.
   - Live adversarial testing.
   - Measuring models instead of picking one.
   - Testing **free providers** too.
   - Haiku as the default.
   - A **fallback** to Gemini instead of rotating models.
4. **What the assistant did:** profiled the data and found most of the 15 issues, wrote the code
   and tests, ran the live scenarios, and fixed what they exposed. Examples:
   - The bot opened a ticket on a holiday without saying the store was closed.
   - Gemini 3 needs its `thought_signature` echoed back.
   - An injection phrasing slipped past the input heuristic.
5. **How I validated it:** read the diffs, ran the CLI myself, checked the transcripts, and kept
   `pytest` green on every commit. One rule I enforced: every claim in the docs had to be backed by
   a test or a measurement.
