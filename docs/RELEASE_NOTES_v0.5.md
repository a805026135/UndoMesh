# v0.5 Release Notes

This release adds replayable runtime events, provenance-aware tool authorization, event-backed context compaction, evidence-gated Skills, contract scheduling and BYOK provider configuration.

Stage 9 additionally adds redacted connection diagnostics and a safe-fixture Provider Harness. A live qwen3.7-flash run completed a two-iteration `read_file` tool loop successfully.

The v0.6 foundation adds credential-scrubbed subprocess environments, optional OS Keyring persistence, explicit sandbox capability reporting, validated Git patches, repeatable offline suites and cross-platform CI. Process-level controls are not described as a full OS sandbox.

Verification is offline and deterministic. It does not claim SWE-bench performance, real-model quality improvement, cost reduction or production sandbox isolation. Replay modes do not re-execute external calls, workspace copies are not automatically merged, compatibility APIs omit provider-native features, and GUI keys are session-only.
