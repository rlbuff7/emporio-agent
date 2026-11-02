# Model choice

**Decision: `claude-haiku-5-5`**, with `claude-sonnet-5-5` one environment variable away
(`EMPORIO_MODEL`). Both passed every scenario. Haiku is faster and about 25x cheaper.

## How it was chosen

1. **Provider.** The challenge allows any provider. I used Anthropic because the agent depends on
   reliable tool calling: every price, stock level and order status must come from a tool, never
   from the model. The provider is isolated in `src/emporio/agent.py`, so switching providers
   means rewriting that one loop.
2. **Candidates.** I compared the two cheapest current models from that provider. The first plan
   named "Haiku or Sonnet". Opus was not tested: the task is short customer-service turns, not
   long reasoning, and Opus costs twice as much as Sonnet.
3. **Measurement.** `evals/run_evals.py` runs 18 scripted conversations against the real API and
   grades them in code. Nobody judges the replies by eye.
   - **7 functional scenarios.** Catalog search with filters, promotion plus the PIX rule, an
     expired promotion, a late order with a complaint ticket, the return window, scope and hours,
     and an order lookup with the wrong e-mail.
   - **11 adversarial scenarios.** Role swap, a fake manager approving a return, requests for
     another customer's data, orders looked up by name, e-mail brute force, a request to add two
     prices, a fake `<system>` tag announcing 90% off, a wrong claim about consumer law, a
     prompt-leak attempt, a competitor price match, and a mixed off-topic question.
   - **What the checks assert.** The right tools ran, the right prices appear, prices that would
     be wrong never appear (e.g. PIX on top of a promotion, a "30% off" price), nothing is
     approved by the bot, and no other customer's data appears.

## Results (2026-11-02, one run each)

| Model | Passed | Avg. time per customer message | Tokens (output) | Estimated cost of the full suite |
|---|---|---|---|---|
| `claude-haiku-5-5` | **18 / 18** | **3.0 s** | 11.3k | **US$ 0.02** |
| `claude-sonnet-5-5` | 18 / 18 | 4.3 s | 8.9k | US$ 0.56 |

Raw results, including every reply: `evals/results/<model>.json`. Costs are estimates from token
counts and list prices (cache reads at 0.1x and cache writes at 1.25x the input price). The system
prompt (manual and rules, about 5k tokens) is cached, so most input tokens are cache reads.

## Why Haiku

- **Same pass rate** on everything this agent must get right: tool use, prices, LGPD and refusals.
- **Faster:** about 3 s per reply. That matters in a chat channel; the manual sets a 5-minute
  response target for humans, and customers expect much less from a bot.
- **About 25x cheaper**, which matters for a store answering repetitive questions all day.
- **The guardrails do not depend on the model.** Identity checks, price validation and the
  lockout run in code, so a cheaper model does not weaken them.

## Limitations of this comparison

- 18 scenarios with one run each is a smoke test, not a benchmark. Model output varies between
  runs, and a larger suite with repeated runs would be the next step.
- Tone and helpfulness are not scored; the checks only cover correctness and safety. Haiku wrote
  about 25% more output tokens for the same conversations (it is more verbose); both kept the
  store's informal, polite voice in the transcripts.
- **Free providers were not tested.** I evaluated Google Gemini (free tier) and Groq on paper. Both
  need a different SDK, so the agent loop would have to be rewritten. Groq's free limits
  (roughly 6–12k tokens per minute) fit about one request per minute with this 7k-token prompt.
  Gemini's free tier is viable but rate-limited, and Google may use free-tier data for training.
  Given the cost above (cents), a rewrite did not pay off for this prototype.
