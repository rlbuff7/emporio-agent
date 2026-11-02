# Data quality report

What I found in the store's data (`data/raw/*.csv`) and policy manual (`data/raw/políticas_da_loja.pdf`),
and what the agent does about each finding. Every data finding below is asserted in
`tests/test_data_quality.py`, so this report fails the test suite if the data changes under it.

## What was clean

- All six CSVs are UTF-8 and parse without errors.
- No duplicate primary keys and no orphan foreign keys (every product has a valid category, every
  order a valid customer, every order item a valid order and product, every promotion a valid product).
- No required field is empty. The only empty values are optional order fields
  (`tracking_code`, `estimated_delivery`, `notes`), which become `NULL` in SQLite.
- `products.specs` is valid JSON in every row.

## Cleaning applied by the ETL (`src/emporio/etl.py`)

| Column | Raw | Stored as |
|---|---|---|
| ids, quantities, stock, `is_active` | text | `INTEGER` |
| `price_brl`, `total_brl` | text (`599.9`, `11499`) | `REAL`; money arithmetic in the tools uses `Decimal`, rounded to cents |
| `order_date`, `estimated_delivery`, `created_at` | text | ISO `YYYY-MM-DD` text, validated on load |
| `products.specs` | JSON string with escaped quotes | validated JSON text, queryable with `json_extract` |
| empty strings | `""` | `NULL` |
| `promotions.description` | free text | renamed `campaign`: it is a campaign label, not a description or a period |

The database is rebuilt from scratch on every run, so the CSVs remain the single source of truth.

## Findings and decisions

### Catalog

| # | Finding | Decision |
|---|---|---|
| 1 | Names, descriptions and specs disagree in basses (135 to 138), drums (139 to 141) and keyboards (142 to 144). The description often names a different brand or model ("Music Man Bass 1X" is described as a Fender Jazz Bass; "Korg Synth 1 Pro" as a Roland with 88 keys while its specs say 61; "Yamaha Bass 3X" as a 5-string Ibanez while its specs say 4 strings). Specs in these categories look templated (every bass is Ash/Maple, 4 strings). | Name, category, price and specs are the source of truth. The agent does not quote descriptions for these three categories. |
| 2 | Products that cannot be sold right now: 113 (discontinued, stock 0), 136 (discontinued, stock 13), 130 (coming soon, stock 2), 96 (active, stock 0). | "Available" means `status = 'active' AND stock_quantity > 0`. Status wins over stock: a discontinued item with stock is still not offered. The agent says so and suggests similar available items (manual 7.3). |
| 3 | Empty categories: 6 (woodwinds), 7 (brass) and 8 (orchestral strings) have no products. | The agent says there are no items in the catalog right now in that category, without inventing any. |
| 4 | The manual says the catalog has "more than 300 instruments"; the CSV has 65 products. | The CSV is the source of truth. The agent never quotes catalog size from the manual. |

### Promotions

| # | Finding | Decision |
|---|---|---|
| 5 | No validity dates. The `description` column holds a campaign name ("Black Friday", "Aniversário da Loja", ...), not a period. Today (November) is Black Friday month in the manual, yet all three "Black Friday" rows are inactive. | `is_active` is the only signal (manual 7.3: check whether the promotion "is active in the system"). The agent never infers a discount from the calendar. |
| 6 | Several rows for the same product: 93, 96 and 99 have two promotions each (all inactive). Product 113 (discontinued) and 96 (out of stock) carry promotions too. | All rows are loaded, so the agent can tell a customer a promotion has ended (manual 7.3). The `active_promotions` view exposes at most one active promotion per product: the highest discount. Only 4 promotions are active today (products 90, 94, 121, 127). |
| 7 | Data and manual disagree on campaigns. "Promoção de Páscoa", "Liquidação de Inverno", "Oferta Especial" and "Desconto Exclusivo" are not in the manual; "Volta às Aulas" is in the manual but not in the data. Some discounts fall outside the manual's ranges: Black Friday at 10% (promotion 1; manual says 15 to 30%) and Aniversário da Loja at 30% (promotion 17; manual says 10 to 25%). | The data wins: the percentages in the system are what the customer is charged. The manual's ranges are treated as marketing guidance, and the affected rows are inactive anyway. |

### Orders

| # | Finding | Decision |
|---|---|---|
| 8 | Product 130 is `coming_soon` but appears in order 3 (delivered in 2025) and order 14 (pending). | Logged as an anomaly. The agent reports the order status as recorded. |
| 9 | Order totals do not match current prices: order 3 is 2 × 1749 = 3498 but `total_brl` is 3450; order 20 is 799 + 689 = 1488 but `total_brl` is 1400. `order_items` has no unit price, so the price paid cannot be reconstructed. | `total_brl` is the amount actually charged and is what the agent shows. The difference is likely a price change or an undocumented discount. |
| 10 | Order 8 is late: `shipped`, estimated delivery 2026-02-28. Orders 9 to 16 have been `confirmed`/`pending` since February to March 2026. | The agent acknowledges the delay, shows the tracking code and offers to log a complaint (24 business-hour response, manual 7.3). |
| 11 | No actual delivery date, only `estimated_delivery`. The 7-day return window (manual 4.1) counts from receipt. | `estimated_delivery` is used as the best estimate of receipt, and the agent asks the customer to confirm the date before stating eligibility. |

### Customers

| # | Finding | Decision |
|---|---|---|
| 12 | Shared e-mail: customers 11 and 43 ("Bruno Carvalho Martins" and "Bruno Martins") both use `bruno.martins@jmail.com`, with different phones. | E-mail is not a unique key. Identity is verified as order number + the e-mail or phone of that order's customer, which is unambiguous. Lookup by e-mail alone is never offered. |

### Policy manual

| # | Finding | Decision |
|---|---|---|
| 13 | Two phone numbers: the cover and section 1.2 list (67) 3341-4444 as "Telefone / WhatsApp"; section 7 says the WhatsApp channel is (67) 3321-4500. | Assumption: phone = 3341-4444, WhatsApp = 3321-4500, because section 7 is the one specifically about the channel. |
| 14 | E-mail spelled two ways: `contato@emporiodamusica.com.br` (cover) and `contato@empóriodamusica.com.br` (section 1.2). | The unaccented spelling is used: it is the valid domain form. |
| 15 | Installment rules contradict each other. The payment table says "minimum installment R$ 100" for credit cards; section 3.1 gives tiers (up to 3x: no minimum "except below R$ 50"; 4 to 6x: R$ 80; 7 to 12x: R$ 100). The 3x rule is ambiguous. | Section 3.1 is followed because it is more specific. "Except below R$ 50" is read as "each installment must be at least R$ 50". This reading never changes an answer in practice: the cheapest product is R$ 159.90, so 3 installments are always above R$ 50. |

## Corrections to the working plan

- The plan counted 66 products; there are 65 (66 is the line count including the header).
- The name/description mismatch (finding 1) also contradicts specs, not just names, which is why
  specs, not descriptions, are treated as the source of truth.
