# Manual test: opening hours on 31/12 (before the fix)

Saved with `python -m emporio.chat --debug --save` during manual testing on 2026-11-02. This is the
original evidence for finding 9 in [`docs/testing.md`](../../docs/testing.md): the agent only knew
today's hours, so it offered a ticket. Fixed in `a71105f` (`get_opening_hours`) and `57bfdc2`
(Campo Grande holidays); the `hours_on_date` eval scenario now covers it.

---

**Cliente:** gostaria de saber se a loja abre dia 31/12?

```text
  [tool] get_current_datetime({})
         -> {"date": "2026-11-02", "time": "16:12", "weekday": "segunda-feira", "holiday": "Finados", "today_hours": "fechado", "is_open_now": false, "next_opening": {"date": "2026-11-03", "weekday": "terça-feira", "opens_at": "09:00"}}
```

**Empório da Música:** Olá! Não tenho o horário específico do dia 31/12 aqui. O padrão da loja é de segunda a sexta, das 9h às 18h, e sábado, das 9h às 13h. Domingo e feriados ela fica fechada. Em datas especiais o horário pode mudar, e os avisos saem nas redes sociais e no WhatsApp.

Para confirmar se a loja abre no dia 31/12, posso abrir um chamado para a equipe e te retornar. Quer que eu faça isso? Se preferir, também pode ligar para (67) 3341-4444 ou chamar no WhatsApp (67) 3321-4500.

Posso ajudar com mais alguma coisa?
