# UndoMesh 简历项目描述

## 推荐版本

**UndoMesh · 可回放、可恢复的 AI Coding Agent Harness　2026.02–2026.03**
**技术栈：** Python / Event Sourcing / Query Loop / Tool Calling / AES-GCM / SQLite / Multi-Agent / Tkinter

**项目简介：** 独立设计并实现轻量 Coding Agent Runtime，围绕 Query Loop、事件溯源和统一 ToolExecutor 构建模型推理—工具执行闭环；在借鉴 Codex、Claude Code 与 DeepSeek Harness 通用架构的基础上，将“可证明恢复”作为核心差异，覆盖长任务上下文、Skill 路由、权限审查、受控多 Agent、分层备份及 Prompt Rewind。

- **事件溯源与上下文治理：** 将 Session/Turn/Step/Model/Tool 生命周期写入 append-only 事件流，通过 Projection 实现 Replay、Fork 和会话恢复；结合 Token Budget、大结果外置、结构化压缩、Tool Call/Result 配对保护与超限重试，降低长任务上下文漂移。
- **恢复优先的变更事务：** 将写文件和删除统一收敛到带 pre/post hash 的 Change Journal；实现 2 GiB Warm Store 与 24 小时超大文件 Hot Vault，采用内容定义分块、跨版本去重、条件压缩和 AES-256-GCM，恢复前完成依赖租约、全量物化、冲突复核及完整哈希验证。
- **Prompt Checkpoint 与安全 Rewind：** 每个用户 Prompt 派生恢复点，支持仅代码、仅会话、代码+会话和摘要四种模式；恢复作为独立补偿事务执行，原变更和会话历史保持可审计，崩溃时自动补偿，未知现场内容拒绝覆盖。
- **统一安全控制面：** ToolExecutor 集中执行 Capability、规范路径、deny-first 权限、危险命令审批、Prompt Injection 来源追踪和 source-to-sink 策略；API Key 仅驻留进程，不进入普通配置、事件或审计日志，永久清理热备必须绑定精确范围并逐次人工确认。
- **多 Agent 因果回退：** 记录子任务显式依赖、读写集和变更事务，回退时计算传递影响闭包，在隔离 Git Worktree 中逆拓扑补偿，或撤销根任务后重放仍成立的后代；审批绑定 Plan、父 HEAD、候选 Commit、完整 Tree Hash、Binary Diff 与验证结果，状态漂移即拒绝合并。
- **完整产品链路：** Skill 使用二阶段召回与证据门控晋升；Restore/Rewind/Audit 进入持久单写者任务队列，并提供 MCP、Plugin/Hook 信任链、SDK/JSON-RPC、CLI、桌面 Recovery/Review Center 和无需 Key 的纵向演示。

## 一页简历压缩版

**UndoMesh · AI Coding Agent Harness** — Python / Event Sourcing / Tool Calling / AES-GCM / Multi-Agent

- 基于 append-only 事件流实现 Query Loop、Replay/Fork、上下文压缩和 Tool Call/Result 配对保护，统一多模型 Provider 与受控工具执行。
- 设计恢复优先的 Change/Restore 双事务：内容定义分块、压缩去重、AES-GCM、2 GiB Warm Store、24 小时大文件 Hot Vault、恢复租约、冲突检测和崩溃补偿。
- 实现四模式 Prompt Rewind、Provenance source-to-sink 权限控制、证据门控 Skill 演进，以及基于依赖闭包和 Git Worktree 的多 Agent 因果回退。
- 提供 CLI 与桌面 Recovery/Review Center、分层项目指令、Lifecycle Hook、MCP、Plugin、SDK/JSON-RPC 及无需 Key 的恢复演示。

## 30 秒口述版

我做了一个恢复优先的轻量 AI Coding Agent Harness，把 Query Loop、工具执行、上下文、权限、多 Agent 和文件恢复做成统一运行时。删除前进入分层加密备份，恢复以独立补偿事务执行，未知用户内容拒绝覆盖。进一步解决多 Agent 的逻辑一致性：系统记录任务依赖和读写集，回退错误任务时计算影响闭包，在真实 Git Worktree 中逆拓扑撤销或重放后代，再用 Plan、父 HEAD、Commit、Tree 和验证结果绑定人工审批。整条链路可用无 Key 演示现场复现。

## 简历使用边界

- 可以写“参考公开架构/能力”，不要写“复刻 Codex 或 Claude Code 源码”。
- 可以写“真实 Provider 冒烟通过”，不要把单次 qwen3.7-flash 调用描述为质量 Benchmark。
- 可以写“受控多 Agent 调度”，不要写成已经达到成熟产品的云端大规模 Agent 编排。
- 可以写“安全回退/恢复优先”，不要写“任意操作均可回滚”或“文件系统全局原子”。
- 201 项测试和 18/18 Benchmark 是离线工程回归，不等同于 SWE-bench/Terminal-Bench 成绩；Windows Job Object 只提供进程和资源限制，不代表文件或网络隔离。
