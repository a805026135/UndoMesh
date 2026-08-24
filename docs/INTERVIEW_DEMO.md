# UndoMesh 面试现场演示手册

## 一条命令

```text
python scripts/demo_interview.py
```

不需要模型 Key、网络、Docker 或预置数据。脚本在临时目录建立两个真实 Git 仓库，运行三条纵向闭环并输出 JSON 证据，退出后自动清理。

## 三分钟讲法

### 1. 后台恢复闭环

连续提交 `zero → one → two` 两个 Change Transaction。恢复前先计算完整闭包并返回 `available`，随后把 Restore 作为持久 Job 提交给单写者队列。Job 完成后文件回到 `zero`，Restore Receipt 保存在 Job Journal，并产生不含内容的 `job.restore` Telemetry Span。

面试重点：回退不是反向 Diff，而是新的补偿事务；原事务状态不被篡改，后台任务中断也不会自动重复副作用。

### 2. 并发冲突拒绝

Agent 将 `zero` 改为 `agent` 后，模拟用户继续编辑为 `newer-user-content`。恢复预检返回 `conflicted`，用户内容保持不变。

面试重点：系统宁可明确失去自动恢复能力，也不以“恢复”为名覆盖未知新内容。

### 3. Worktree Review 闭环

从固定 baseline 创建真实 Git Worktree，候选修改先运行 `git diff --check`，通过后提交候选并生成 Diff SHA-256。批准合并时必须提交同一摘要，系统再次计算候选 Diff；摘要不一致或父工作区脏都会拒绝合并。

面试重点：解决 TOCTOU——Reviewer 看到的内容必须就是最终合并内容。

## 建议现场顺序

1. 先运行脚本，让面试官看到三段 JSON 均成功。
2. 打开 `undomesh/execution/recovery.py` 讲完整预检、Lease 和补偿。
3. 打开 `undomesh/runtime/jobs.py` 讲为什么重启后标记 `interrupted` 而不是自动重跑。
4. 打开 `undomesh/execution/worktrees.py` 讲候选验证与 Diff 摘要审批。
5. 最后主动说明边界：外部副作用不普遍可逆、多文件提交不是文件系统全局原子、标准 Benchmark 尚未实跑。

## 高频追问

### 为什么不直接用 Git reset？

Git 无法恢复未提交文件、删除前二进制内容或仓库外已授权文件，也不能表达恢复依赖被 GC、恢复中断补偿和未知现场冲突。Git 仍用于 Worktree 候选隔离，但不是恢复事实源。

### 为什么后台任务不在重启后自动继续？

模型请求和恢复提交可能不是幂等操作。进程退出时把 `running` 标记为 `interrupted`，由用户显式恢复，比静默重复副作用安全。恢复事务自身另有 Journal，可判断是补偿、冲突还是继续诊断。

### Diff 摘要是不是多余？

不是。验证完成到人工批准之间，候选分支仍可能被修改。摘要把审批绑定到精确候选字节，避免 Review 与 Merge 之间的 TOCTOU。

### 201 个测试说明什么？

只说明当前固定工程不变量通过，不说明模型能力领先。面试 Demo 的价值是证明测试、状态机和真实 Git/文件系统链路一致。
