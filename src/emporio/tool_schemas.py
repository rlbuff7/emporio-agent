"""JSON schemas for the tools in ``emporio.tools``, in the Anthropic Messages API format.

Descriptions are written for the model: what the tool returns and when to use it.
"""

CATEGORIES = [
    "Guitarras",
    "Baixos",
    "Baterias e Percussão",
    "Teclados e Pianos",
    "Violões",
    "Instrumentos de Sopro (Madeiras)",
    "Instrumentos de Sopro (Metais)",
    "Cordas Orquestrais",
    "Ukuleles",
]

TOOL_SCHEMAS = [
    {
        "name": "search_products",
        "description": (
            "Search the store catalog. Use for open questions such as 'violões até R$ 1000' or "
            "'ukulele com captação'. Returns up to 10 products sorted by final price, each with "
            "original price, active promotion, final price, PIX price, max interest-free installments, "
            "stock and availability. Price filters apply to the final price. By default only products "
            "that can be sold now are returned."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": CATEGORIES},
                "query": {
                    "type": "string",
                    "description": "Words to match in the product name or specs, e.g. a brand ('Yamaha') or wood ('cedro').",
                },
                "min_price": {"type": "number", "description": "Minimum final price in BRL."},
                "max_price": {"type": "number", "description": "Maximum final price in BRL."},
                "strings": {
                    "type": "string",
                    "enum": ["nylon", "steel"],
                    "description": "String type, for violões and ukuleles ('aço' = steel).",
                },
                "electronics": {
                    "type": "boolean",
                    "description": "True for electro-acoustic (with pickup), false for purely acoustic.",
                },
                "only_available": {
                    "type": "boolean",
                    "description": "Default true. Set false only to look up items that are out of stock or discontinued.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product",
        "description": (
            "Look up one product by name (partial names work, e.g. 'Takamine GD20') or numeric id. "
            "Returns specs, pricing (original, promotion, final, PIX, installments), stock, availability, "
            "promotions that have ended for it, and similar available alternatives when it cannot be sold. "
            "If the name matches several products, returns candidates: ask the customer which one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name_or_id": {"type": "string"}},
            "required": ["name_or_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_active_promotions",
        "description": (
            "List the promotions active right now, each with original price, discount percent and final "
            "price. Use when the customer asks what is on sale, or asks about a specific campaign "
            "(e.g. Black Friday) to check whether it is active."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"category": {"type": "string", "enum": CATEGORIES}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_order_status",
        "description": (
            "Look up an order. Requires the order number AND the e-mail or phone registered on that order; "
            "returns a generic error if they do not match. Returns status, items, total charged, payment "
            "method, tracking code, estimated delivery, delay information, and for delivered orders the "
            "return/exchange/warranty windows already computed against today's date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "email_or_phone": {"type": "string", "description": "As given by the customer."},
            },
            "required": ["order_id", "email_or_phone"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_current_datetime",
        "description": (
            "Current date, time and weekday in Campo Grande/MS, whether the store is open now, today's "
            "hours, holiday name if any, and the next opening when closed. Use for 'vocês estão abertos?', "
            "'abre amanhã?' and before telling a customer when the team will reply."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_opening_hours",
        "description": (
            "Whether the store opens on a specific date and its hours that day, taking weekends and "
            "holidays into account (national, Good Friday and the Mato Grosso do Sul state holiday). "
            "Use for questions about a future or past date, e.g. 'abre dia 31/12?' or 'abre na Sexta-feira "
            "Santa?'. Pass the next occurrence of the date the customer means; if unsure of the year, call "
            "get_current_datetime first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Open a ticket for the human team and get a ticket number. Use for complaints, return/exchange "
            "or warranty requests, damage on delivery, freight quotes for other cities or large "
            "instruments, data deletion requests, discount negotiation, and any question the manual does "
            "not answer. Do not use it for questions you can answer with the other tools or the manual."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [
                        "complaint",
                        "return_regret",
                        "exchange_defect",
                        "exchange_preference",
                        "warranty",
                        "delivery_damage",
                        "freight_quote",
                        "data_privacy",
                        "discount_request",
                        "policy_question",
                        "other",
                    ],
                },
                "summary": {
                    "type": "string",
                    "description": "What the customer needs and what was already checked, in Portuguese, for the team.",
                },
                "order_id": {"type": "integer"},
                "customer_contact": {"type": "string", "description": "E-mail or phone the customer gave."},
            },
            "required": ["reason", "summary"],
            "additionalProperties": False,
        },
    },
]
