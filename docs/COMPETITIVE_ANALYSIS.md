# UndoMesh 与成熟 Coding Agent Harness 对标分析

更新日期：2026-08-22

## 1. 分析边界

本文只依据公开且可核对的产品文档或开源代码，不推测 Codex、Claude Code 的私有实现。DeepSeek Harness 当前官方定位仍是 Developer Preview，因此它适合作为架构参考，不应被描述为已经稳定的生产标准。

主要资料：

- Codex：[OpenAI Developers](https://developers.openai.com/)、[AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、[Skills](https://learn.chatgpt.com/docs/build-skills)、[MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Security](https://learn.chatgpt.com/docs/security)
- Claude Code：[扩展体系](https://code.claude.com/docs/en/features-overview)、[Hooks](https://code.claude.com/docs/en/hooks)、[Checkpointing](https://code.claude.com/docs/en/checkpointing)、[Subagents](https://code.claude.com/docs/en/sub-agents)、[Sessions](https://code.claude.com/docs/en/sessions)
- DeepSeek Harness：[官方网站](https://www.deepseek.com/harness/en/)、[源码仓库](https://github.com/deepseek-ai/deepseek-harness)、[架构说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md)、[Core](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/core.md)、[Tool Pipeline](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md)

## 2. 总体结论

UndoMesh 已经覆盖 Coding Agent 的核心执行闭环，而且在“可恢复性证明”上形成了足够清晰的差异：它不是只记录模型消息或 Git Diff，而是把文件变更、备份依赖、恢复动作、会话回退和清理策略统一纳入可审计事务。

当前主要差距不在 Query Loop 本身，而在成熟产品经过长期迭代形成的外围能力：扩展协议、项目级指令发现、确定性 Hook、MCP 生态、应用服务协议、OS 级隔离、可观测性、评测规模和产品化交互。

因此不建议重写现有核心，也不建议为了追求“万物皆插件”而一次性迁移架构。更合适的路线是在现有事件运行时和 ToolExecutor 周围建立稳定的 Capability/Hook 接口，再逐步添加外部适配器。

## 3. 逐项对比

### 3.1 执行循环与持久化

Codex、Claude Code 和 DeepSeek Harness 都把模型推理、工具调用、结果回填组织成长任务循环。DeepSeek Harness 明确把 append-only SessionEvent 日志作为事实源，再从日志投影模型历史；UndoMesh 已采用同类事件溯源思路，支持 SQLite 持久化、投影、Replay、Fork、压缩和运行时不变量校验。

UndoMesh 的优势：

- 模型可见历史能够从事件重新投影，Replay 不重复工具副作用；
- Turn、Step、Tool Call 有明确闭合不变量；
- Provenance 与事件绑定，而不只是普通日志字段；
- Prompt Checkpoint 直接复用事件边界和变更事务闭包。

需要补充：

- 事件 Schema 版本和正式迁移机制；
- SQLite 单写者队列、备份、损坏恢复演练；
- 可取消、可恢复的后台 Command/Job 抽象；
- 面向 UI 的增量 Projection Cache，避免长会话每次重放完整日志。

### 3.2 指令、Skill 与扩展发现

Codex 支持从全局到项目子目录的 `AGENTS.md` 指令链，并规定明确的覆盖顺序；Skill 使用渐进披露，只在初始上下文暴露名称和描述，命中后读取完整内容。Claude Code 使用 `CLAUDE.md`、Skills、Subagents、Hooks、MCP 和 Plugins 分担不同类型的扩展需求。

UndoMesh 已有二阶段 Skill 路由、显式调用、元信息、评估证据和候选版本晋升门禁，但还缺少成熟的项目扩展分发和诊断体验。

建议补充：

1. 分层项目指令加载：用户级、仓库级、子目录级、临时 override，并显示实际加载来源。
2. 标准化 Skill 包：`SKILL.md + scripts + references + assets`，保持渐进披露。
3. `/doctor`、`/skills`、`/instructions` 诊断命令，解释未命中、冲突和上下文占用。
4. Plugin Manifest：把 Skill、Hook、MCP 配置和可选 UI 元数据组成可版本化分发单元。

不建议让 Skill 本身变成绕过 ToolExecutor 的执行入口。Skill 应提供工作流和知识，实际副作用仍必须经过能力、权限、来源和事务审查。

### 3.3 Tool Calling、Hooks 与 Code Mode

Claude Code 的 Hook 在固定生命周期事件上确定触发，可运行命令、HTTP、Prompt、Agent 或 MCP Tool；它把“模型应该遵守的指导”和“系统必须执行的约束”分开。DeepSeek Harness 的工具流水线提供 pre-execute、单调 Guard、around execute、post-execute、finalize 和 result 通知，并支持 native、code、both 三种工具呈现方式。

UndoMesh 的 ToolExecutor 已统一执行能力检查、路径规范化、deny-first 权限、Provenance source-to-sink 策略、超时、结果外置和审计，但扩展点目前主要是代码内注册。

建议补充：

1. 生命周期 Hook Bus：`session_start`、`turn_start`、`pre_tool`、`post_tool`、`tool_failure`、`context_compacted`、`restore_*`、`session_end`。
2. 阻断型 Hook 只能收紧权限；结果型 Hook 可以附加上下文，但不能把失败改写为成功。
3. Hook 来源、执行耗时、输入摘要和结果进入事件日志，敏感字段先脱敏。
4. 在沙箱成熟后实验 Code Mode，让模型生成受限程序批量编排只读或低风险工具；写操作仍走正常审批和事务层。

### 3.4 MCP、SDK 与应用服务

Codex 和 Claude Code 均通过 MCP 连接外部工具。Codex 的公开配置包含 STDIO、Streamable HTTP、OAuth、启动/调用超时、工具 allow/deny 和每工具审批策略。DeepSeek Harness 同时提供 Web、Headless、ACP、JSON-RPC 和 Python SDK 形态。

UndoMesh 当前只支持内置 Tool Registry 和模型 Provider Adapter，桌面端直接调用本地应用对象。

建议补充：

1. MCP Client：先支持 STDIO，再支持 Streamable HTTP 和 OAuth；远程工具默认 `prompt`。
2. 对 MCP Tool Schema 做延迟发现和按需加载，避免工具数量增长吞噬上下文。
3. UndoMesh App Server：以 JSON-RPC 或本地 HTTP 暴露 session、prompt、cancel、events、approval、checkpoint、restore。
4. Python SDK 只封装 App Server 协议，不复制运行时逻辑。
5. Tool provenance 标记服务器、认证主体和调用链，外部结果默认低信任。

### 3.5 多 Agent 与隔离

Codex 和 Claude Code 提供子 Agent、并行任务与 Worktree 工作流；DeepSeek Harness 通过可替换 Subagent Provider、Agent Scope、Goal 和 Workflow 组合多种执行方式。

UndoMesh 已有 `TaskContract`、依赖 DAG、读写集、Token/Deadline、冲突波次调度、父 Agent 验收和隔离副本，但仍是受控实验实现。

建议补充：

- 使用真实 Git Worktree/分支替代目录复制，并记录基线提交；
- 子 Agent 只能提交候选 PatchSet，父 Agent 负责验证和合并；
- 增加同一文件语义冲突、失败依赖传播和取消风暴测试；
- 为 Agent 之间的消息建立类型化协议和大小预算；
- UI 展示 DAG、写集、成本、当前状态、审批等待和最终验收证据。

### 3.6 Sandbox、权限与 Prompt Injection

成熟 Harness 普遍区分提示指导、权限审批和系统隔离。Claude Code 文档明确建议把必须保证的规则放到 Hook/Permission，而不是只写进 `CLAUDE.md`。DeepSeek Harness 把文件系统、子进程和 Sandbox 设计为可替换能力，并提供 Landlock 相关原生实现。

UndoMesh 已有路径越界防护、CapabilitySet、deny-first 规则、危险命令分类、一次性审批、凭证脱敏和 Provenance source-to-sink 控制，这比只做关键词拦截更完整；但它仍不是 OS 容器。

建议补充：

- Linux Landlock/seccomp、Windows AppContainer/受限 Token 或容器后端；
- CPU、内存、进程数、运行时间、文件描述符和网络出口限制；
- 网络域名 allowlist 与 DNS rebinding 防护；
- MCP/Hook/外部文件统一进入来源追踪；
- 公开 Prompt Injection、路径穿越和恢复攻击基准。

### 3.7 Checkpoint、Rewind 与恢复

Claude Code 每个用户 Prompt 形成 Checkpoint，可恢复代码、会话、二者或摘要；公开限制是只跟踪 Write/Edit/NotebookEdit，Bash 和外部改动不在 Checkpoint 范围，并建议继续使用版本控制。

UndoMesh 在这个方向上的主要改进：

- 写工具默认通过 Change Transaction，而不是只做临时编辑快照；
- 大文件删除在 unlink 前完成流式备份准入；
- Warm/Hot 存储提供压缩、内容定义分块、去重、AES-GCM 和引用安全 GC；
- Restore 是独立补偿事务，带 Lease、全量物化、二次冲突检查、哈希复核和崩溃补偿；
- 未知现场内容拒绝覆盖；恢复能力被清理后明确显示 unavailable/expired；
- 会话历史通过 append-only Fork/Projection 处理。

仍需补充：

- 多路径文件系统全局原子性无法由逐文件 rename 保证，需要明确 Receipt；
- 文本冲突可增加可选三方合并；
- Shell、数据库、网络和部署副作用需要专门补偿适配器；
- Manifest 整体认证、密钥轮换、Keyring/DPAPI 和外部不可变 T3 存储；
- 1/10/50 GiB 实际吞吐、磁盘压力和断电测试。

### 3.8 可观测性、评测与产品化

DeepSeek Harness 提供可插拔 Session Telemetry 和 OpenTelemetry 后端，Codex/Claude Code 都有诊断、任务、Diff、Review、后台执行和多终端产品体验。

UndoMesh 已有 201 个离线测试、18/18 固定 Benchmark，并增加恢复后台任务、可信 Plugin 激活和 Worktree 摘要审批三条纵向闭环；当前 Benchmark 仍是工程回归而非真实世界 Coding Agent 排名。

建议补充：

- OpenTelemetry Trace/Metric/Log，默认脱敏并可关闭；
- Provider 延迟、首 Token、工具成功率、重试、Token/费用、压缩率、恢复吞吐指标；
- SWE-bench Verified 子集、Terminal-Bench、自建破坏性恢复集和 Prompt Injection 集；
- 对照实验固定模型、温度、权限、工具集和 Prompt，报告置信区间；
- 客户端增加 Diff/Review、事件时间线、后台任务、取消和恢复进度。

## 4. 建议补充路线

### P0：最值得立即补充

1. 生命周期 Hook Bus 与审计协议。
2. 分层项目指令和诊断命令。
3. OS 级 Sandbox Backend。
4. App Server、增量事件流和可取消后台任务。
5. Manifest 整体认证和密钥轮换。

### P1：形成生态接入能力

1. MCP STDIO/HTTP/OAuth 和按需 Tool Schema。
2. OpenTelemetry 可观测性。
3. 真 Git Worktree 多 Agent 合并流程。
4. 桌面 Diff/Review/审批工作台。
5. 大文件 Native CDC/Zstandard/Reflink 优化。

### P2：扩大验证与分发

1. SDK、ACP/JSON-RPC 兼容层和 Headless Server。
2. Plugin Manifest 和可安装扩展包。
3. Code Mode 实验通道。
4. 标准 Agent Benchmark 与故障注入矩阵。
5. 签名客户端安装包、自动更新和崩溃报告。

## 5. 不应盲目复制的部分

- 不要因为 DeepSeek Harness 采用 Cordis 就重写全部 Python Runtime。UndoMesh 当前规模下，稳定的接口和测试比通用插件内核更重要。
- 不要让 MCP、Hook、Skill 或 Code Mode 绕过 ToolExecutor；所有副作用必须汇入同一控制面。
- 不要把真实模型的一次成功冒烟写成模型质量结论。
- 不要宣称能回滚所有 Shell 或外部系统副作用。
- 不要用功能数量稀释恢复系统。恢复事务、证据和失败语义仍应是项目主线。

## 6. 最终定位

> UndoMesh 是一个面向长任务与高风险文件操作的、事件溯源且恢复优先的轻量 Coding Agent Harness。它吸收成熟项目在 Query Loop、Skill、权限、多 Agent 和客户端交互上的通用做法，但把主要创新集中在变更事务、分层备份、恢复租约、补偿事务和 Prompt Rewind 的可验证闭环。
