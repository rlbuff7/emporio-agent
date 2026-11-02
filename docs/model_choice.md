# Model choice

**Decision: `claude-haiku-5-5` as the agent's model.** The fallback chain for outages is
`gemini-3.1-flash-lite` → `groq: openai/gpt-oss-20b`, both on free tiers.

Haiku was the only model that combined a perfect score with speed and a negligible cost. The free
models are good enough to keep the store answering when Claude is down. They are not good enough
to be the main model.

## How it was chosen

1. **Measurement.** `evals/run_evals.py` runs 18 scripted conversations against the real API and
   grades them in code. Nobody judges the replies by eye.
   - **7 functional scenarios:** catalog search with filters, promotion plus the PIX rule, an
     expired promotion, a late order with a complaint ticket, the return window, scope and hours,
     and an order lookup with the wrong e-mail.
   - **11 adversarial scenarios:** role swap, a fake manager approving a return, requests for
     another customer's data, orders looked up by name, e-mail brute force, a request to add two
     prices, a fake `<system>` tag announcing 90% off, a wrong claim about consumer law, a
     prompt-leak attempt, a competitor price match, and a mixed off-topic question.
   - **What the checks assert:** the right tools ran, the right prices appear, prices that would
     be wrong never appear (e.g. PIX on top of a promotion), nothing is approved by the bot, and no
     other customer's data appears.
2. **Same everything except the model.** Every model ran with the same system prompt, tools and
   three guardrail layers. Free providers go through an OpenAI-compatible adapter
   (`src/emporio/compat.py`).
3. **Cost discipline.** Each provider was tested with its smallest suitable model. The more
   expensive Anthropic model (Sonnet) was run once for reference.

## Results (2026-11-02, one run each)

| Model | Provider / tier | Passed | Avg. time per message | Cost of the suite | Notes |
|---|---|---|---|---|---|
| **`claude-haiku-5-5`** | Anthropic, paid | **18 / 18** | **3.0 s** | **US$ 0.02** | |
| `claude-sonnet-5-5` | Anthropic, paid | 18 / 18 | 4.3 s | US$ 0.56 | Same score, ~25x the cost |
| `gpt-oss-20b` | Groq, free | 9 / 13 that ran | 55 s | free | 5 scenarios hit the limit of 8k tokens per minute (one ~6k-token request per minute). It missed the PIX/promotion prices when adding, the tracking code, the address and an expired-promotion price |
| `gpt-oss-120b` | Groq, free | 12 / 16 that ran | 30–130 s | free | 9/10 adversarial. Missed "18%", the tracking code, the address and "7 dias". The run was stopped by a tool-schema error that is now fixed (see `docs/testing.md`, #5) |
| `gemini-3.5-flash` | Google, free | 1 / 1 that ran | 12 s | free | Passed the catalog scenario; the free tier then refused further requests: **20 requests per day per model**, 5 per minute |
| `gemini-3.1-flash-lite` | Google, free | 1 / 1 that ran | 6.4 s | free | 15 per minute; chosen as the first fallback |

Raw results, including every reply: `evals/results/<model>.json`. The Groq 120b run crashed before
saving; its numbers come from the run log. Anthropic costs are estimates from token counts and
list prices (cache reads at 0.1x and cache writes at 1.25x the input price). The Haiku and Sonnet
runs predate the later prompt fixes and the check added to `regret_window` (finding 6 in
`docs/testing.md`); they have not been re-run since, to save cost.

## Why Haiku

- **It got everything right that this agent must get right:** tool use, exact prices, LGPD and
  refusals. The free models missed facts that customers ask for: a tracking code, the store
  address, a promotion percentage.
- **It is fast:** about 3 s per reply. The free tiers took 6 to 130 s, mostly waiting on rate
  limits, which is too slow for a chat.
- **It is cheap:** about US$ 0.001 per customer message.
- **The guardrails do not depend on the model.** Identity checks, price validation and the
  lockout run in code, so even a weaker fallback model cannot leak data or invent a price. The
  adversarial scores of the free models (9/10 for 120b) reflect that.

## Why a fallback chain and not model rotation

Rotating models per message to spread cost would save almost nothing at Haiku's price, and the
customer would see the tone change mid-conversation. A fallback only kicks in when Claude returns
429, 5xx or a network error. Gemini Flash Lite goes first because its free tier allows 15
requests per minute. Groq comes last because its 8k-tokens-per-minute free limit fits about one
request per minute with this prompt.

## Limitations of this comparison

- **18 scenarios with one run each is a smoke test, not a benchmark.** Model output varies between
  runs.
- **Free-tier rate limits cut the free runs short.** Their scores reflect only the scenarios that
  ran; the scenarios that hit a limit are not counted as model failures.
- **Tone and helpfulness are not scored.** The checks only cover correctness and safety. Haiku
  wrote about 25% more output tokens than Sonnet for the same conversations; it is more verbose.
- **Not tested:** Cerebras now requires a payment method. OpenAI has no free API tier. Groq no
  longer offers Llama 3.3.
