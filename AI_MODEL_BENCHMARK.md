# AI model benchmark

The application supports an Ollama-backed local AI provider. Ollama supports structured JSON output via a JSON schema, which the application now uses for email classification. See the official Ollama structured-output documentation for details.

Recommended candidates:

- `qwen3:8b`
- `llama3.1:8b`
- `mistral:7b`
- existing `qwen2.5:7b`

Install the models on the Ollama host as required:

```powershell
ollama pull qwen3:8b
ollama pull llama3.1:8b
ollama pull mistral:7b
```

Then create an approved/anonymized labeled dataset from representative PDL emails using `tools/benchmark_dataset_template.csv` as the schema. The labels should be reviewed by a human who understands the PDL operation.

Run:

```powershell
python tools\ai_benchmark.py --input tools\benchmark_dataset.csv --output benchmark_results.json
```

The benchmark measures:

- Recognition accuracy
- Escalation accuracy
- Priority accuracy
- Response-required accuracy
- JSON/schema success
- Average latency

Do not select a production model solely on generic benchmarks. Use your own PDL email set, especially difficult cases involving quoted history, courtesy-only thanks, customer frustration, repeated follow-ups, schedule changes, production impact and genuine appreciation.

For production classification, keep temperature at `0.0` and keep thinking disabled unless a benchmark demonstrates a measurable benefit. Qwen3 supports controllable thinking through Ollama, but the application does not expose or store a private reasoning trace; only the structured classification is persisted.
