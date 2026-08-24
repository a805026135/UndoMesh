# 多 Agent 因果回退

## 为什么普通 Undo 不够

多个 Agent 并行或串行工作时，后续任务可能读取前序任务的输出。只撤销被点名 Agent 的文件，会让依赖它的代码留在一个逻辑上不成立的状态。单纯按文件、时间或 Git Commit 回退也无法回答三个问题：谁依赖谁、应按什么顺序补偿、审批后待合并内容是否发生变化。

MiniClaude 为每个 `TaskContract` 建立持久因果记录。显式依赖与读写集重叠共同形成依赖边；事务 Journal 同时记录 Session、Turn、Step、Agent、Task、Worktree、声明读写集和依赖任务，使文件变更能够回溯到产生它的子任务。

## 闭环

```text
TaskContract
  │ 依赖 + 声明读写集
  ▼
独立 Git Worktree ── ToolExecutor 强制写集边界
  │
  ├─ Change Journal：事务 ID + Agent/Task/Worktree 身份
  └─ Candidate：verification + commit + tree + diff
                         │
                         ▼
审批 Review Envelope：parent HEAD + candidate commit/tree
                         │
                         ▼
确定性合并；任一摘要或 HEAD 漂移即拒绝

指定任务回退
  │
  ├─ 计算传递影响闭包
  ├─ 逆拓扑撤销后代和根任务（cascade）
  └─ 或撤销根任务后重放仍可成立的后代（replay）
                         │
                         ▼
隔离 Worktree 验证 → Plan SHA + Review SHA 双重审批 → 合并
```

## 两种策略

- `cascade`：逆拓扑撤销完整影响闭包。它最保守，适合接口、Schema、生成物等强依赖发生错误时。
- `replay`：先逆序撤销闭包，再跳过根任务重放后代候选。若补丁已不适用或产生冲突，Git 会让准备阶段失败，不会触碰主工作区。

## 审批不变量

候选审核不是只记一个 Diff 摘要。Review Envelope 同时绑定：

- Worktree 的 baseline 与审核时父仓库 HEAD；
- candidate commit 与完整 Git tree；
- binary diff 的 SHA-256；
- 验证命令及退出码。

回退还额外绑定影响闭包、撤销顺序、重放集合、关联事务与 Merge Commit 的 `plan_sha256`。批准与执行之间，只要父 HEAD、候选分支、Tree 或 Diff 任意一项变化，合并就会被拒绝。

## 失败边界

- 不自动补偿数据库、部署、网络调用等外部副作用；此类任务需要业务补偿器。
- 依赖图质量受 TaskContract 读写集精度影响，因此 ToolExecutor 会硬性阻止声明写集之外的文件修改。
- 多进程共同写同一因果 Ledger 时仍应由 App Server 的单写者队列统一调度。
- `replay` 只承诺“冲突即停止”，不承诺所有后代都能在新基线上继续成立。

## 无 Key 验证

```powershell
python scripts/demo_causal_rollback.py
python scripts/demo_causal_rollback.py --ui
python -m pytest -q tests/test_causal_rollback.py
```

演示会真实创建两个 Git Worktree 候选：`client-agent` 依赖 `schema-agent`。随后从根任务生成影响闭包，按后代优先顺序回退，并验证 Schema 恢复、依赖文件消失以及两项任务进入 `rolled_back` 状态。
