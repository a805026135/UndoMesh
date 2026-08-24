# Skill Evaluation

`SkillVersionStore` records candidate content hashes and outcomes: success, tokens, steps, duration and safety failure. Promotion requires enough samples, no safety regression, success rate at least the baseline and token cost within the configured regression allowance. Rejected content never replaces active content. Versions can be deprecated or rolled back.

The deterministic reference evidence is `benchmarks/stage6_skill_report.json`; it validates the gate, not model quality. Real evaluations must hold task set, provider/model, temperature and tool permissions constant and retain the no-Skill and previous-version baselines.
