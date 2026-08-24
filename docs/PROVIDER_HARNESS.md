# Provider Harness

Stage 9 turns BYOK connectivity into a repeatable compatibility check. Provider presets declare endpoint, suggested models, key environment aliases, tool-calling expectation and compatibility tier. `diagnose_connection()` maps authentication, quota, model, rate-limit and network failures to redacted structured results.

Run the real Tool Calling smoke test with a provider key in its environment:

```powershell
$env:DASHSCOPE_API_KEY="..."
python scripts/provider_harness.py --provider dashscope --model qwen3.7-flash
```

The harness reads only `benchmarks/fixtures/provider_smoke.txt`, uses temporary event/Skill databases, requires a completed `read_file` event and checks that the model returns the marker. `--output report.json` writes machine-readable evidence. It never serializes the API key.

The checked-in `stage9_qwen37_flash_report.json` records one successful live smoke run. A single run proves protocol compatibility at that moment, not model quality, uptime, cost or benchmark superiority.
