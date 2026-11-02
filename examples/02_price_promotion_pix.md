# Price, promotion and PIX rule

Promotion shown as original price + % + final price (manual 6.2). The 5% PIX discount is offered only on the item without a promotion, because discounts do not stack.

Generated with `python -m emporio.chat --debug --save` on 2026-11-02 (a national holiday, store closed), model `claude-haiku-5-5`. Tool lines show what the agent called; long results are truncated. The conversation is in Portuguese because the store's customers are Brazilian.

---

**Cliente:** Quanto custa o Crafter HT-100? Tem desconto no PIX?

```text
  [tool] get_product({"name_or_id": "Crafter HT-100"})
         -> {"product_id": 90, "name": "Crafter HT-100 Folk Aço Natural", "category": "Violões", "status": "active", "stock_quantity": 3, "available": true, "original_price_brl": 2399.0, "promotion": {"campaign": "Desconto Exclusivo", "discount_percent": 18}, "final_price_brl": 1967.18, "pix_price_brl": null, "...
```

**Empório da Música:** O Crafter HT-100 Folk Aço Natural está em promoção "Desconto Exclusivo":

- Preço original: R$ 2.399,00
- Desconto: 18%
- Preço final: R$ 1.967,18
- Parcelamento: até 12x de R$ 163,93 sem juros

Sobre o PIX: o desconto de 5% não se aplica a produtos que já estão em promoção, então esse não tem preço especial no PIX. O estoque atual é de 3 unidades.

Posso ajudar com mais alguma coisa?

**Cliente:** E o Takamine GD20, sai mais barato no PIX?

```text
  [tool] get_product({"name_or_id": "Takamine GD20"})
         -> {"product_id": 95, "name": "Takamine GD20 Dreadnought Natural", "category": "Violões", "status": "active", "stock_quantity": 5, "available": true, "original_price_brl": 2199.0, "promotion": null, "final_price_brl": 2199.0, "pix_price_brl": 2089.05, "max_installments": 12, "installment_value_brl": 18...
```

**Empório da Música:** Sim, no PIX o Takamine GD20 Dreadnought Natural fica mais barato:

- Preço normal: R$ 2.199,00
- Preço no PIX: R$ 2.089,05 (5% de desconto)
- Ou no cartão: até 12x de R$ 183,25 sem juros

Ele não está em promoção no momento, então o desconto do PIX vale sobre o preço normal. Estoque: 5 unidades disponíveis.

Mais alguma coisa que posso ajudar?
