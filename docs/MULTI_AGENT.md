# Constrained Multi-Agent Scheduling

Each `TaskContract` declares objective, dependencies, readable/writable paths, expected outputs, verification commands, token budget and deadline. Dependency-ready tasks form waves. Non-overlapping write sets can run concurrently; overlapping sets serialize. Write tasks may use isolated copies.

The parent owns acceptance and merge. Failed dependencies skip descendants, token/deadline violations fail the task, and cancelling the parent cancels running children. Child tool access is still checked by normal capabilities and permissions.

The deterministic scheduling comparison is `benchmarks/stage7_scheduler_report.json`. It demonstrates concurrency behavior and its conflict-avoidance cost, not end-to-end model superiority.
