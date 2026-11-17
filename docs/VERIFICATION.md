# 验证总览（测试开发视角）

> 本项目的验收方式是**不变量断言 + 故障注入**：不是"功能跑起来了"，而是"无论怎么操作、无论怎么失败，某条事实不能被破坏"。
> 简历口径「不变量断言与故障注入验收」的每一条，在本页都能追到代码与测试入口。

## 1. 数字基线

- 测试：`pytest -q` 全量通过，基线记录 **204 passed / 1 skipped**（skip 不计入通过）。
- 离线 Benchmark：**18/18** 全过，报告见 [benchmarks/offline_report.json](../benchmarks/offline_report.json)，复现命令 `python scripts/run_offline_benchmarks.py`。
- CI：[.github/workflows/ci.yml](../.github/workflows/ci.yml) 每次推送跑全量测试。

## 2. 不变量断言

把恢复语义写成**可断言规则**，违反即测试失败：

| 不变量 | 含义 | 代码 / 测试入口 |
| --- | --- | --- |
| 事件生命周期合法 | Turn 必须恰好一个终态事件、事件序列唯一且递增、tool-call 必须配对闭合 | [undomesh/runtime/invariants.py](../undomesh/runtime/invariants.py)（`validate_lifecycle`，违反抛 `LifecycleInvariantError`） |
| 先备份后变更 | 任何破坏性写入前，必须先完成可恢复性准入（有备份、有空间、有校验） | [tests/test_durable_transactions.py](../tests/test_durable_transactions.py)、[docs/DURABLE_CHANGE_TRANSACTIONS.md](DURABLE_CHANGE_TRANSACTIONS.md) |
| 未知现场不覆盖 | 恢复时如果现场与预期不一致（被改过、摘要不符），默认冲突并停止，不"覆盖了事" | [tests/test_backup_store.py](../tests/test_backup_store.py)（`test_authenticated_encryption_detects_chunk_tampering`）、[tests/test_hot_vault_recovery.py](../tests/test_hot_vault_recovery.py)（`audit_recovery` 判 `health == "corrupt"`） |
| 审批绑定对象摘要 | 批的必须是某个具体 revert/合并的摘要，不是"批准这个 Agent 随便干" | [undomesh/provenance/](../undomesh/provenance/)、[tests/test_provenance_security.py](../tests/test_provenance_security.py) |

不变量比功能更难更改：实现换了，不变量还在，测试用例就不用重写。

## 3. 故障注入用例

| 注入的故障 | 预期行为 | 测试锚点 |
| --- | --- | --- |
| 恢复过程被中断 | 重启后走 Journal 补偿，检查是全部回滚还是半成品状态 | [tests/test_restore_transactions.py](../tests/test_restore_transactions.py)（`simulated restore interruption` + `test_startup_recovers_interrupted_restore_to_expected_state`） |
| 事务提交中途被打断 | pre-commit 残留事务被弃置，根状态不被触碰 | [tests/test_durable_transactions.py](../tests/test_durable_transactions.py)（`simulated disk interruption`、`test_interrupted_precommit_transaction_is_abandoned_without_touching_root`） |
| 备份内容被篡改 / 哈希不一致 | 校验失败，拒绝覆盖，恢复审计给出 corrupt 健康度 | [tests/test_backup_store.py](../tests/test_backup_store.py)、[tests/test_hot_vault_recovery.py](../tests/test_hot_vault_recovery.py)（`corrupt.health == "corrupt"`、`unavailable_paths`） |
| 恢复审计 | 可用 / 冲突 / 损坏三态可查询 | [tests/test_hot_vault_recovery.py](../tests/test_hot_vault_recovery.py)（`test_recovery_audit_reports_available_conflicted_and_corrupt`） |

核心断言是**"拒绝"而不是"成功"**——异常路径的正确行为往往是什么都不做。

## 4. 并发与依赖回退

- 并发修改以版本校验断言拒绝（不静默覆盖）。
- 撤销上游 Agent 时按任务依赖与读写文件算出受影响范围，先撤下游、后撤上游，在独立 worktree 验证后才合并：[undomesh/agents/worktree.py](../undomesh/agents/worktree.py)、[tests/test_causal_rollback.py](../tests/test_causal_rollback.py)，语义见 [CAUSAL_MULTI_AGENT_ROLLBACK.md](CAUSAL_MULTI_AGENT_ROLLBACK.md)。

## 5. 复现命令

```bash
pip install -r requirements.txt
pytest -q                          # 全量测试
python scripts/run_offline_benchmarks.py   # 离线 Benchmark
```

更完整的面试演示脚本（无需模型 Key）：[docs/INTERVIEW_DEMO.md](INTERVIEW_DEMO.md)。
