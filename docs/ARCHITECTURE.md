# UndoMesh v0.5 Architecture

UndoMesh is a lightweight Python harness runtime, not a clone of any one coding agent. The Query Loop emits immutable session/turn/step/model/tool events. A projection derives the model-visible conversation, so recovery, fork and compaction do not need to repeat side effects.

The execution boundary is `ToolExecutor`: capability checks, canonical path validation, deny-first permission rules, provenance source-to-sink policy, timeout and audit run before or around every tool. Large values can be content-addressed as artifacts.

Skills provide instructions rather than executing tools directly. Routing combines textual relevance, explicit `$skill-name` selection and evaluation evidence. Candidate versions are promoted only after deterministic gates. Multi-agent work enters through `TaskContract`; the scheduler considers dependencies, write sets, deadline and token budget before invoking constrained `SubAgent` instances.

All supported hosted providers currently use OpenAI-compatible Chat Completions. Native-only provider features are intentionally outside v0.5.
