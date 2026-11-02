# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Customer-service agent for **Empório da Música**, a fictional musical-instrument store in Campo Grande/MS.
This is a take-home challenge for an AI Engineer role (Artefact). The only hard technical constraint is
**Python as the main language**. Deliverables: a public GitHub repo with real, incremental commit history,
a README justifying every technical decision (framework, LLM, retrieval, prompt strategy, limitations, and
how AI coding assistants were used), and 3–5 example conversations (`.md`/`.txt`), at least one non-trivial
(live data lookup or policy rule applied).

## Project rules

- Read .planning/PLANO_DESAFIO_ARTEFACT.md (plan) and .planning/CASOS_DE_USO_POLITICAS.md (business rules) before any task.
  The original challenge statement is .planning/desafio_tecnico.pdf.
- All code, comments, docs, README and commit messages in ENGLISH.
- The agent itself replies to customers in Brazilian Portuguese.
- Work one phase at a time; stop after each phase for my review.
- Small commits per step (conventional commits). Never force-push. Never commit .env.
- Keep it simple: no LangChain, plain Anthropic SDK tool-use loop.
- Prices, stock, discounts and dates are always computed in code, never by the LLM.

## Inputs

- `data/raw/*.csv` — six tables: `categories`, `customers`, `products`, `orders`, `order_items`, `promotions`.
  File names have the prefix `desafio_tecnico_ai_eng - ` (with spaces). Notable shapes: `products.specs` is a
  JSON string; `promotions.is_active` is `0/1`; `orders` carries `status`, `tracking_code`, `estimated_delivery`.
- `data/raw/políticas_da_loja.pdf` — store policy manual (v2.1). Phase 1 converts it to `data/policies.md`.
- `.planning/` is git-ignored and written in Portuguese; English versions of anything that must ship go in `docs/`.
  `.planning/desafio_tecnico.pdf` is the original challenge statement.

## Intended architecture (from the business-rules spec)

- **Autonomy levels per use case** (UC-02 … UC-10): `AUTO` (bot answers via tool), `TRIAGEM` (bot checks
  eligibility and collects data, a human decides — the bot never approves or denies), `HUMANO` (direct handoff).
- **Handoff** is a tool, `escalate_to_human(use_case, priority, summary, collected_data, attachments)`; in the
  prototype it appends to `data/handoffs.jsonl` and returns a ticket number. It is mandatory for TRIAGEM/HUMANO UCs.
- **Rule numbers live in config, not prompts**: deadlines, installment ranges and shipping fees go in
  `knowledge/use_cases.yaml` and are read by tools. The prompt gets only a summary (id, autonomy, triggers, rules).
- **Cross-cutting rules** the code must enforce:
  - Never state price, stock, deadline or order status without a tool call.
  - Order data is shown only after identity verification (order id + e-mail or phone) — LGPD.
  - Policy not in the manual → don't invent; open a ticket.
  - Promotions always show original price, discount % and final price.
  - Customer images/videos: describe, never diagnose; always hand off.
- **Evals**: at least one scenario per sub-UC; they check that handoff was called and nothing was approved by the LLM.
