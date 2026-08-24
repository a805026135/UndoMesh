# UndoMesh 完整项目介绍

## 1. 项目概述

UndoMesh 是一个使用 Python 实现的轻量 AI Coding Agent Harness。项目不绑定单一模型，也不以复刻某个商业 Coding Agent 为目标，而是研究一个模型如何在真实代码仓库中持续理解任务、调用工具、修改文件、管理上下文、委派子任务，并在发生错误时安全恢复。

项目的核心定位是“恢复优先”。普通 Coding Agent 往往把回退理解为反向应用 Diff、执行 Git reset，或者恢复编辑工具保存的临时快照。UndoMesh 将恢复拆成一个完整的工程系统：变更前建立证据、删除前完成备份准入、备份对象具有生命周期、恢复依赖受 Lease 保护、恢复本身写独立 Journal、提交前验证所有目标，崩溃后能够补偿或显式进入冲突状态。

截至 2026-08-24，阶段 0–21 的本地核心与适配层已完成，并补齐三条可现场演示的纵向闭环；离线回归为 201 passed、1 个平台能力相关 skip，固定 Benchmark 为 18/18。外部标准数据集成绩、平台证书签名和未在当前宿主运行的隔离能力不计入完成声明。

## 2. 为什么做这个项目

一个最小 Agent 只需要三步：把问题发给模型、解析 Tool Call、把工具结果发回模型。但进入真实工程后，会立刻遇到更多问题：

1. 模型连续调用工具时，如何保证每一步都能审计和恢复？
2. Tool Result 很大或会话很长时，如何压缩而不破坏 Tool Call/Result 配对？
3. 网页、仓库文档或工具输出中包含 Prompt Injection 时，如何阻止低信任信息触发高风险写操作？
4. 多个子 Agent 并行修改同一仓库时，如何避免写冲突和失控委派？
5. Agent 删除了未提交代码、二进制数据或 10 GiB 文件时，Git 无法恢复怎么办？
6. 如果恢复缓存被清理、密钥丢失、文件被用户再次修改或者恢复中断，系统应如何表达失败？

UndoMesh 的目标不是隐藏这些问题，而是让它们成为有状态、有证据、有失败语义的运行时能力。

## 3. 设计原则

### 3.1 模型不是事实源

会话事实写入 append-only Event Store，模型消息通过 Projection 派生。模型可以推理和选择工具，但不能直接修改审计历史、伪造工具成功或绕过权限控制。

### 3.2 所有副作用汇入统一控制面

Tool、Skill、子 Agent 和客户端最终都经过 ToolExecutor。路径校验、Capability、Permission、Provenance、Timeout、Audit 和结果外置不能散落在每个工具中。

### 3.3 恢复能力必须在破坏前建立

“先删后备份”不是恢复。对于受支持的写操作，Change Transaction 在修改工作区前保存 preimage；超大文件删除只有在流式备份、容量和磁盘余量检查成功后才允许 unlink。

### 3.4 失败状态必须可解释

系统区分 available、unavailable、expired、corrupt 和 conflicted。无法恢复时应说明是依赖被清理、密钥错误、内容损坏还是现场发生了未知修改，而不是只返回一个通用异常。

### 3.5 不把缓存冒充永久备份

2 GiB Warm Store 是有界的本地回退缓存；超大文件 Hot Vault 默认保留 24 小时并设置独立预算。它们都不替代 Git、对象存储或企业备份。

## 4. 总体架构

```text
CLI / Desktop / Non-interactive Command
                 │
                 ▼
        UndoMesh Application
                 │
      ┌──────────┼──────────┐
      ▼          ▼          ▼
 Query Loop   Checkpoint   Provider Settings
      │        Manager       / Diagnostics
      ▼          │
 Event Store ◄───┘
      │
      ▼
 Projection / Replay / Fork / Compaction
      │
      ▼
 ToolExecutor
      │
      ├─ Capability + Path Sandbox
      ├─ Permission + Risk Rules
      ├─ Provenance Source-to-Sink Policy
      ├─ Audit + Timeout + Externalization
      └─ Tool Registry
             │
      ┌──────┼───────────────┐
      ▼      ▼               ▼
 File/Shell  Change Engine   Sub-Agent Scheduler
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
 Warm Chunk Store         Hot Vault
       │                     │
       └──────────┬──────────┘
                  ▼
         Restore Coordinator
```

## 5. Query Loop 与事件运行时

### 5.1 Query Loop

一次用户请求会打开 Turn。每次模型推理是一个 Step；如果响应包含 Tool Call，系统执行工具、记录结果，再发起下一 Step，直到得到最终文本、达到限制、被取消或失败。

为了避免“日志看起来成功，但生命周期不完整”，运行时检查以下关系：

- 每个 Turn 只能有一个开始和一个终态；
- 每个 Step 必须闭合；
- 每个 Tool Call 必须依次出现 requested、started 和唯一终态；
- Tool Result 必须与 Call ID 配对；
- Replay 只重建模型上下文，不重新执行工具。

### 5.2 Event Store

事件可存入内存或 SQLite，包含 session、turn、step、call、时间、Payload 和 Provenance。Fork 复制指定边界之前的事件并写入 lineage 事件；原会话保持不变。

### 5.3 Projection

Projection 把事件折叠为模型可见消息。上下文压缩通过 `CONTEXT_COMPACTED` 事件声明哪些消息被摘要遮蔽，而不是删除历史。这样审计、Replay 和当前模型上下文可以同时成立。

## 6. 上下文与记忆治理

UndoMesh 维护 Token Budget，分别估算系统提示、历史消息、工具 Schema 和保留余量。接近限制时执行结构化压缩；若 Provider 返回 Context Overflow，则从事件历史重新压缩并重试。

关键策略：

- Tool Call 和对应 Tool Result 作为不可拆分组处理；
- 大结果写入内容寻址 Artifact，只在上下文保留稳定占位符；
- 摘要记录 shadowed event IDs，避免同一历史重复进入模型；
- Memory 经过执行、反思、提炼、分类存储和按需检索，不把全部长期记忆注入每轮请求；
- 对缓存友好的稳定前缀和系统提示尽量保持顺序不变。

## 7. Skill 路由与演进

Skill 是高层工作流和领域知识，不直接绕过权限执行副作用。路由分为候选召回和精排：

1. 根据名称、描述、关键词、意图和显式 `$skill` 调用生成候选；
2. 根据适用边界、负向条件、示例和历史证据排序；
3. 命中后才加载完整 Skill 内容，控制 Token 成本；
4. Skill 执行结果记录成功率、Token、Step、耗时和安全失败；
5. 新版本只有通过样本数、成功率、安全和成本门禁才会替代当前版本。

这解决了 Skill 数量增长后“所有说明都塞进 Prompt”和“自动优化直接污染线上版本”两个问题。

## 8. ToolExecutor 与安全体系

ToolExecutor 是统一执行边界：

1. 查找 Tool Definition 并校验参数；
2. 检查 Agent 的 CapabilitySet；
3. 规范化路径并拒绝项目越界；
4. 应用 deny-first 规则和危险操作分类；
5. 根据 Provenance 判断低信任来源是否流向文件写入、Shell 或永久清理等敏感 Sink；
6. 必要时发起不可缓存或一次性人工审批；
7. 执行 Timeout、审计、结果脱敏和大结果外置。

API Key 通过环境或当前进程覆盖值解析，不写入普通配置、Event Store 或审计参数。自定义 Base URL 必须使用 HTTPS，只有 localhost 允许 HTTP。

安全边界必须诚实：当前规则和路径 Sandbox 属于应用层纵深防御，不等于 Landlock、seccomp、AppContainer 或容器级隔离。

## 9. 变更事务

### 9.1 Change Journal

每次受支持的文件变更创建状态机 Journal，记录：

- Transaction ID；
- 目标路径；
- 修改前/后的存在性、大小和 SHA-256；
- 备份 Manifest/Digest；
- staged、committing、committed、conflicted 等状态；
- 错误和恢复信息。

变更先在隔离 staging 中应用并验证，再进入 commit。提交时检查现场仍等于预期 preimage，避免覆盖用户在预检后产生的新修改。

### 9.2 事务化删除

Unified Diff 不适合大二进制文件，因此提供专用删除事务。它逐块读取源文件完成 Hash、Chunk、Compression 和 Encryption，不使用与文件大小成比例的内存；备份 Manifest 原子发布后才删除源文件。

## 10. 分层备份存储

### 10.1 Warm Store

默认上限 2 GiB，用于普通代码和中小文件，保留策略按事务状态、Pin、重要度和时间决定清理资格。

### 10.2 Hot Vault

默认 256 MiB 以上文件进入物理独立的 Hot Vault，默认保留 24 小时，预算 50 GiB，并保留最低磁盘余量。其目的不是长期保存，而是防止 Agent 误删超大且无法由 Warm Store 容纳的文件。

提前永久清理必须：

1. 先做 dry-run；
2. 生成绑定 Transaction IDs、Digests 和 Logical Bytes 的 token；
3. 实际清理时重新计算范围；
4. 发起不可缓存的一次性人工审批。

### 10.3 Chunk Store

- Gear 内容定义分块：默认 16 KiB 最小、64 KiB 平均、256 KiB 最大；
- SHA-256 内容寻址，支持跨文件、跨版本去重；
- 只有压缩后更小时才使用 zlib；
- AES-256-GCM 加密，每块随机 Nonce，Digest 绑定为 AAD；
- 恢复时验证认证标签、解压上限、块大小、块 Hash、文件大小和完整文件 Hash；
- Mark-and-Sweep GC 保护被活跃、Pin、冲突或恢复 Lease 引用的对象。

## 11. Restore Transaction

恢复不是把原事务改成 rolled_back，而是创建独立 Restore Journal。主要流程：

1. Resolve：把一个或多个 Change Transaction 解析为连续闭包；
2. Preflight：检查现场内容、Manifest、Chunk、Key、到期状态和磁盘空间；
3. Lease：锁定本次恢复依赖，阻止 GC；
4. Materialize：所有目标流式恢复到隔离临时文件；
5. Verify：完成认证、大小和完整 Hash 验证；
6. Recheck：进入提交前再次检查现场；
7. Commit：逐路径 sibling temp + fsync + replace；
8. Prove：回读并验证，写完成 Receipt。

若提交中断，系统使用 pre-restore expected 做补偿。启动时现场只有 target 或 expected 才能自动处理；出现第三种未知内容时进入 conflicted 并保留 Lease。即使补偿数据损坏，应用也保持可启动，供用户诊断或显式放弃冲突恢复。

逐文件 replace 不能提供跨多个路径的文件系统全局原子性，因此 Receipt 会保留已提交路径和补偿状态。

## 12. Prompt Checkpoint 与 Rewind

每个 `TURN_STARTED` 都能派生 Prompt Checkpoint。系统从该点之后的成功 Tool Event 收集 Change Transaction，形成代码恢复闭包。

支持四种模式：

- `code`：恢复代码，保留当前会话；
- `conversation`：Fork 到历史边界，保留当前代码；
- `both`：恢复代码并 Fork 会话；
- `summary`：保留事件，用摘要改变模型 Projection。

代码恢复先执行无写入 Preview。任何冲突或缺失依赖都会在第一处工作区写入前终止。`both` 涉及文件与 Event Store 两个持久域，不宣称分布式原子；如果代码恢复已经完成但 Fork 失败，失败事件会记录 Restore ID 和 `code_restore_completed=true`。

相比 Claude Code 公开的 Checkpoint 限制，UndoMesh 的差异在于支持受控写工具和大文件删除事务、明确的缓存生命周期、恢复 Lease、独立补偿 Journal 和恢复健康证明。但它同样不能自动撤销任意网络、数据库或部署副作用。

## 13. 受控多 Agent

主 Agent 负责计划、审批和验收。子任务通过 `TaskContract` 声明：

- Objective 和依赖；
- 可读/可写路径；
- 预期输出；
- 验证命令；
- Token Budget 和 Deadline；
- 是否需要隔离工作区。

Scheduler 按依赖生成执行波次。写集不重叠的任务可以并行，重叠任务串行；失败依赖会阻止下游任务。子 Agent 继续受到 Capability 和 Permission 控制，父 Agent 不因委派而放弃最终质量责任。

调度之外，系统使用 `CausalTaskLedger` 持久记录显式依赖及由读写集推导出的动态依赖。ToolExecutor 会把 Task/Agent/Worktree 身份传播到 Change Journal，并硬性拒绝声明写集之外的修改。候选在真实 Git Worktree 中验证；审核摘要绑定父 HEAD、候选 Commit、完整 Tree、Binary Diff 和验证结果。

当某个已合并子任务需要撤销时，系统先计算传递影响闭包。`cascade` 逆拓扑撤销后代和根任务；`replay` 在撤销闭包后跳过根任务并重放后代候选。整个补偿仍在隔离 Worktree 中准备，最终由 Plan SHA 与 Review SHA 双重审批后确定性合并。冲突、父 HEAD 漂移或候选变化都会终止流程，不修改主工作区。

## 14. 多模型 Provider

桌面和配置层支持 OpenAI、DeepSeek、Anthropic、Gemini、阿里云百炼、Kimi/Moonshot 和自定义 OpenAI-compatible 服务。预置 Provider 给出默认 Base URL 和常用模型，用户可以覆盖模型和端点并执行最小连通性测试。

统一兼容层降低接入成本，但不会假装所有 Provider 的 Native Feature 完全一致。工具调用格式、上下文限制、Prompt Cache、错误码和推理参数仍需要 Provider Harness 验证。

## 15. CLI 与桌面客户端

CLI 支持交互 REPL、直接 Query、Provider 测试、Checkpoint 列表和非交互 Rewind。桌面客户端采用任务侧栏、中央对话区和恢复摘要的三栏布局，提供：

- 新建/切换任务；
- 模型、Key、Base URL 和连通性设置；
- Prompt 输入、运行状态和停止；
- Recovery Center；
- Checkpoint 健康、事务数、冲突和缺失依赖；
- Code/Conversation/Both/Summary 操作；
- 最近 Restore Receipt。

Tk 只运行在 UI 主线程，Asyncio 和 SQLite 在背景线程创建和使用，UI 更新通过 `root.after` 返回主线程。它是低依赖客户端，不是完整 IDE；Diff Editor、任务虚拟化、安装包和自动更新仍是后续方向。

## 16. 测试与评测

测试覆盖：

- Query Loop 生命周期和 Tool Calling；
- Event Replay/Fork/Persistence/Compaction；
- Prompt Injection 与 Provenance Policy；
- Skill 版本门禁；
- Multi-Agent 依赖和写集冲突；
- Provider 设置与 Key 不落盘；
- Change/Restore Transaction 状态机；
- Chunk 加密、压缩、去重、篡改和错误密钥；
- 大文件流式删除、Hot Vault 到期和永久清理审批；
- 恢复 Lease、提交中断、启动补偿和未知现场冲突；
- 四模式 Prompt Rewind；
- CLI 和桌面 ViewModel。
- 分层项目指令、Lifecycle Hook 与可替换 Shell 沙箱后端。

18 套固定离线 Benchmark 用于防止 Harness 组件和纵向接线回归，不代表模型能力排名。真实 Provider Harness 只验证接口、模型名、Key 和 Tool Calling 链路可用。

## 17. 与成熟项目的关系

- 从 Codex 借鉴：项目/任务组织、Skills 渐进披露、权限监督、Worktree 与 Review 思路；
- 从 Claude Code 借鉴：Query Loop、Checkpoint 交互语义、Hook/Skill/Permission 职责分离；
- 从 DeepSeek Harness 借鉴：Event-sourced Session、Capability Seam、可组合 Tool Pipeline 和模型/工具解耦；
- UndoMesh 的自主重点：恢复优先的 Change/Restore 双事务、Warm/Hot 分层、恢复 Lease、健康证明、Provenance source-to-sink 和证据门控 Skill 演进。

详细对标与补充路线见 `docs/COMPETITIVE_ANALYSIS.md`。

## 18. 已知限制

1. 当前应用层 Sandbox 不是 OS 级隔离。
2. 逐文件原子替换不等于跨文件全局原子事务。
3. 外部副作用需要业务级补偿器。
4. 本机 fallback key 不等于企业密钥托管。
5. Pure Python CDC 和 zlib 的超大文件吞吐仍需 Native 优化。
6. SQLite 需要正式 Schema Migration、单写者服务和损坏演练。
7. 多 Agent 隔离副本需要升级为真实 Git Worktree 和确定性合并。
8. 尚未接入 MCP、App Server、OpenTelemetry 和标准 Agent Benchmark。

## 19. 后续路线

下一里程碑应先做生产化加固：生命周期 Hook、分层项目指令、OS Sandbox、App Server/后台任务、Manifest 认证与密钥轮换。随后接入 MCP、OpenTelemetry、真实 Worktree、Diff/Review 工作台和 Native 大文件存储优化。最后再考虑 Plugin 包、SDK、Code Mode 和标准 Benchmark。

## 20. 运行与验证

```powershell
# 安装开发依赖
python -m pip install -e ".[dev]"

# CLI
undomesh

# 桌面客户端
undomesh-desktop
# 或
python -m undomesh --gui

# 全量测试
python -m pytest -q

# 固定离线基准
python scripts/run_offline_benchmarks.py

# 发布门禁
python scripts/release_check.py
```
