# V10.13.5 — Sentiment Accuracy, Exact HPE Logo, Dark Mode

- Exact HPE artwork from user-provided reference is rendered as transparent asset; distinctive green E is preserved.
- Dark-mode controls, inputs, selects, dropdowns, tables, cards and topbar controls receive explicit dark surfaces and light text.
- Sentiment now separates customer attitude from technical/operational vocabulary.
- Courtesy words do not imply positive sentiment.
- Technical facts do not imply positive/negative sentiment by themselves.
- Strong deterministic evidence and Ollama model disagreements are surfaced as human-review conflicts.
- Ollama schema now supports sentiment evidence/confidence.
- Ollama output token limit is applied from AI_MAX_TOKENS.
