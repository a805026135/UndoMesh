# UndoMesh 改进规划与执行基线

> 文档状态：执行基线（Baseline）  
> 适用范围：UndoMesh v0.2–v0.5
> 创建日期：2026-08-22  
> 最后更新：2026-08-22  
> 维护规则：任何影响核心目标、事件模型、安全边界、阶段顺序或验收标准的修改，必须先更新本文档或新增 ADR。

---

## 1. 文档目的

本文档用于约束 UndoMesh 后续改进，避免开发过程中出现以下问题：

- 为追求功能数量而偏离核心研究问题；
- 重复实现 Codex、Claude Code 或 DeepSeek Harness 已成熟解决的基础能力；
- README 声明的功能没有真正接入主执行链路；
- 先实现复杂多 Agent，再补安全、事件和可恢复性底座；
- 只有单元测试，没有能够证明系统能力的端到端证据；
- 无法判断改进来自模型、Prompt、工具还是 Harness；
- 项目描述、代码实现、测试结果和简历表述不一致。

后续每个开发任务都应能映射到本文档中的目标、阶段和验收标准。无法映射的需求默认不进入当前开发范围。

---

## 2. 项目定位

### 2.1 定位声明

UndoMesh 是一个面向 Coding Agent Harness 研究的轻量 Python Runtime。它不以复刻完整 Claude Code、Codex 或 DeepSeek Harness 为目标，而是集中解决以下四类问题：

1. **可回放执行**：以 Turn–Step–Event 模型记录 Agent 的模型请求、工具调用、权限决策和上下文变化，支持恢复、分叉和策略对照回放。
2. **来源感知安全**：跟踪用户输入、仓库文件、网页、工具结果、记忆和子 Agent 输出的来源与信任等级，在敏感工具执行前进行 source-to-sink 风险判断。
3. **可验证 Skill 演化**：根据任务成功率、测试结果、token 成本和失败类别评估 Skill，不允许未经验证的反思结果直接晋升为长期策略。
4. **约束驱动多 Agent**：根据任务依赖、文件读写集合、能力边界和验证契约调度子 Agent，而不是只依赖固定角色 Prompt。

### 2.2 一句话介绍

> UndoMesh 是一个可回放、来源感知、可评测的轻量 Coding Agent Runtime，用于量化不同 Harness 策略对任务成功率、安全性和执行成本的影响。

### 2.3 项目成功标准

项目成功不以代码行数或功能数量衡量，而以下列结果为准：

- 所有核心能力真实接入统一执行链路；
- 安全策略在执行端强制实施，不能只依赖 Prompt 或工具 schema；
- 同一任务可以在不同 Harness 配置下分叉与回放；
- Skill 和多 Agent 策略具有可重复的对照评测结果；
- README、技术文档、测试和简历描述能够相互验证；
- 核心测试无需真实模型、真实 API key 或公网即可运行。

---

## 3. 明确非目标

以下内容不是 v0.2–v0.5 的主要目标。除非成为核心路线的必要依赖，否则不得优先投入：

- 复刻 Claude Code/Codex 的完整终端 UI；
- 建设插件市场、账号系统或云端同步平台；
- 自行实现新的模型 SDK、MCP 协议或 tokenizer；
- 自行实现容器、操作系统沙箱或 Git 版本控制；
- 扩充大量固定角色子 Agent；
- 增加与核心评测无关的 GUI 动画和视觉效果；
- 用 LLM 风险分类代替确定性权限和 OS 沙箱；
- 追求兼容所有模型供应商，而没有稳定的核心协议；
- 让 Agent 在无隔离评测和人工确认的情况下修改自己的正式 Skill；
- 宣称能够“完全防御 Prompt Injection”。

如果出现上述需求，应先判断能否复用成熟组件，并说明它服务于哪个核心目标。

---

## 4. 设计原则

### 4.1 单一执行入口

任何模型工具调用、子 Agent 工具调用和系统内部工具调用，都必须经过统一 Tool Execution Pipeline。禁止绕过执行器直接调用工具实现。

### 4.2 执行端强制安全

Prompt、工具描述和模型自检只能帮助模型做出正确选择，不能构成安全边界。安全边界必须在以下位置强制实施：

- schema 校验；
- capability 校验；
- 路径规范化；
- 权限策略；
- 人工审批；
- OS 级沙箱；
- 审计日志。

### 4.3 完整事实与模型上下文分离

完整事件日志是事实来源；模型上下文只是事件日志的一个投影。上下文压缩只能替换模型可见表面，不得删除原始事件。

### 4.4 先可观察，再自动优化

在实现 Skill 自适应或多 Agent 自动调度前，必须先能记录和解释当前行为。无法测量的优化不得进入自动晋升流程。

### 4.5 所有自动决策均可解释、可取消、可回放

权限、Skill 路由、上下文压缩和多 Agent 调度必须记录输入、决策依据、结果和关联事件。

### 4.6 默认最小权限

Agent 和子 Agent 只获得完成当前任务所需的最小工具、路径、网络和预算能力。能力不能由子 Agent 自行扩大。

### 4.7 渐进式交付

每个版本必须独立可运行、可测试和可演示。不得为了未来架构一次性重写全部模块。

### 4.8 复用成熟轮子

优先复用：

- 模型协议：官方 OpenAI/Anthropic SDK 或稳定适配层；
- MCP：官方 MCP Python SDK；
- 数据校验：Pydantic/JSON Schema；
- Git：Git CLI 与标准 patch；
- 沙箱：Docker、bubblewrap、Windows Sandbox 或其他成熟 runtime；
- 检索：SQLite FTS5、FAISS 或成熟 embedding；
- 遥测：OpenTelemetry；
- Token：供应商 usage 与成熟 tokenizer。

UndoMesh 自身重点负责生命周期、策略、provenance、调度、事件投影、回放与评测。

---

## 5. 当前基线与已知问题

### 5.1 当前主链路

当前 Query Loop 的有效路径基本为：

```text
User Message
  → LLM Request
  → Tool Calls
  → ToolExecutor
  → Tool Results
  → LLM Request
  → Final Text
```

### 5.2 已初始化但未完整接线的模块

- SkillRouter；
- MemoryRetriever/Extractor/Compressor；
- ContextManager；
- PermissionManager；
- SecurityAuditor。

### 5.3 已确认的关键风险

- ToolExecutor 接收 config 但未使用权限管理器；
- PermissionManager 没有保护实际主工具执行；
- 子 Agent 只过滤发送给模型的 schema，执行时仍使用完整 registry；
- 路径边界存在字符串前缀判断和绝对路径处理问题；
- glob/grep 搜索目录缺少统一边界检查；
- 上下文压缩和大结果外置未进入 Query Loop；
- token 计数首次运行可能隐式访问网络；
- 缺少 Turn/Step 状态、持久化事件、恢复和回放；
- 主要是模块级单元测试，缺少主链路集成测试；
- 固定最大迭代次数不能替代超时、取消、token 和成本预算。

### 5.4 测试基线

截至本文档创建时：

- 原有测试总数：42；
- 在当前仓库强制导入后：41 通过，1 失败；
- 失败原因：`tiktoken` 首次加载 fallback encoding 时尝试联网下载；
- 环境中存在另一个 editable-install 的 UndoMesh，需要防止测试误导入其他目录。

2026-08-22 第一开发迭代重新确认后的基线：

- 已增加当前仓库导入路径断言；
- 已增加 tokenizer 无本地缓存时的确定性离线估算；
- 已增加 ScriptedLLMClient 和 Query Loop 离线集成测试；
- 当前测试结果：79 项全部通过；
- 默认测试运行不需要真实模型、API key 或公网。

2026-08-22 阶段 1 实施结果：

- 已实现不可变 `AgentEvent`、基础 `EventType` 与 session 内单调 sequence；
- 已实现同步 `LifecycleDispatcher`，支持按事件类型订阅、全量订阅、取消订阅，以及 `log`/`raise` 两种监听器失败策略；
- 已实现生命周期不变量校验，覆盖 Turn/Step 闭合、事件顺序和 Tool Call/Result 配对；
- Query Loop 的完成、模型失败、工具失败、取消和最大迭代路径均产生明确终态；
- 现有 UI callback 暂时保留兼容，后续 UI 可逐步迁移为事件消费者；
- 阶段 1 验收完成，下一阶段进入统一工具执行管线和 capability 控制。

2026-08-22 阶段 2 实施结果：

- 已实现 `CapabilitySet` 与 `ToolExecutionContext`，工具、Shell、读写根目录和子 Agent 能力均由执行上下文约束；
- 已实现统一路径规范化，覆盖相似前缀目录、相对路径、绝对路径和项目边界；
- 已将 PermissionManager、SecurityAuditor、ContextManager 和大结果外置接入真实 ToolExecutor；
- 已增加工具参数 schema 校验、通用工具超时、Shell 进程树取消和规范化审批缓存；
- 子 Agent 使用真实受限 capability 和 PermissionManager，不再仅过滤发送给模型的 schema；
- Query Loop 在模型请求前执行上下文预算检查，并记录 `context.compacted`；
- REPL 已支持一次性人工审批；没有交互审批提供者时默认拒绝；
- 当前仍以 capability/path/process-group 提供运行时隔离，产品化 OS sandbox 需要后续接入外部成熟 sandbox adapter，不宣称现有实现等同于内核级隔离。

2026-08-22 阶段 3 实施结果：

- 已实现带 schema migration 的 SQLite append-only EventStore，并作为正式 App 的默认运行时事件存储；
- 已实现从事件投影 OpenAI-compatible 消息历史、进程重启后的 Context 恢复接口和安全回放结果；
- 已实现按 sequence 边界 Fork，分支事件保留原始 event ID 来源；
- 已实现 `dry-run`、`recorded-tools` 和 `recorded-model` 的无副作用投影模式；当前版本不自动重新调用真实模型；
- 已实现 SHA-256 内容寻址 ArtifactStore 和读取时完整性校验；
- 已增加旧数据库 provenance 字段迁移、重新打开数据库、Fork 边界和 Artifact 篡改测试。

2026-08-22 阶段 4 实施结果：

- 已实现内容块级 `Provenance`、来源类型、信任等级和父来源链；
- 用户输入、模型响应、工作区文件、网页/MCP、记忆和子 Agent 结果具有明确来源模型；
- 已实现 source-to-sink 策略，覆盖 Shell、文件写入、敏感读取、网络发送、MCP 写入、记忆持久化和 Agent 委派；
- 权限拒绝已从普通工具失败中拆分为 `tool.denied` 事件，并记录拒绝原因；
- SQLite 落盘前对常见 key、token、Bearer、密码和 secret 进行递归脱敏；安全审计日志对 secret 和文件内容做摘要/脱敏；
- 已增加“恶意 README 诱导读取 `.env`”的真实 Query Loop 回归测试，敏感内容未进入 Tool Result；
- 当前 provenance 为块级近似，不宣称 token 级精确污点追踪，也不宣称能够识别所有 Prompt Injection。

---

## 6. 目标架构

### 6.1 运行时主链路

```text
User/Input Queue
  → Turn Start
  → Input Claim
  → Context Projection
  → Lifecycle: before_model_request
  → Step Start
  → Model Request
  → Model Response
  → Tool Request Validation
  → Capability Guard
  → Provenance Policy
  → Permission / Approval
  → Sandboxed Tool Execution
  → Result Externalization
  → Tool Result Event
  → Context Projection Update
  → Step End
  → Continue or Turn End
```

### 6.2 分层结构

```text
UI / API
  └─ Application Service
      └─ Agent Runtime
          ├─ Lifecycle Dispatcher
          ├─ Query Driver
          ├─ Context Projection
          ├─ Tool Execution Pipeline
          ├─ Permission & Provenance
          ├─ Session Event Store
          ├─ Replay/Fork Engine
          ├─ Skill Evaluation
          └─ Multi-Agent Scheduler
```

### 6.3 建议目录

```text
undomesh/
├── runtime/
│   ├── models.py
│   ├── events.py
│   ├── lifecycle.py
│   ├── dispatcher.py
│   ├── store.py
│   ├── projection.py
│   ├── replay.py
│   └── errors.py
├── execution/
│   ├── pipeline.py
│   ├── capabilities.py
│   ├── validation.py
│   ├── cancellation.py
│   └── runner.py
├── provenance/
│   ├── models.py
│   ├── policy.py
│   ├── propagation.py
│   └── sinks.py
├── evaluation/
│   ├── tasks.py
│   ├── runner.py
│   ├── metrics.py
│   └── reports.py
└── agents/
    ├── contracts.py
    ├── scheduler.py
    └── workspace.py
```

目录应随实际重构渐进建立，不要求在第一阶段创建全部空文件。

---

## 7. 核心领域模型

### 7.1 Session、Turn、Step

- **Session**：一段可恢复、可分叉的完整 Agent 会话。
- **Turn**：由一次用户输入、steering 或系统唤醒触发的一段工作。
- **Step**：一次模型请求及其产生的零个或多个工具调用。
- **Event**：运行时已经发生的不可变事实。
- **Artifact**：不适合直接保存在事件 payload 中的大型或二进制结果。
- **Projection**：从事件日志派生的模型上下文、UI 状态或审计视图。

### 7.2 AgentEvent 草案

```python
@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    session_id: str
    turn_id: str | None
    step_id: str | None
    sequence: int
    event_type: EventType
    timestamp: datetime
    payload: dict[str, Any]
    provenance: Provenance | None = None
```

首批事件类型：

```text
session.started
session.resumed
session.forked
turn.started
turn.ended
turn.failed
turn.cancelled
step.started
step.ended
step.failed
model.requested
model.responded
model.failed
tool.requested
tool.approved
tool.denied
tool.started
tool.completed
tool.failed
context.compaction_started
context.compacted
context.compaction_failed
artifact.created
agent.spawned
agent.completed
agent.cancelled
```

### 7.3 事件不变量

- 同一 Session 中 sequence 必须单调递增且唯一；
- 每个 `turn.started` 必须最终对应 ended、failed 或 cancelled；
- 每个 `step.started` 必须最终对应 ended 或 failed；
- 每个 `tool.requested` 使用唯一 `call_id`；
- tool started/completed/failed 必须关联已存在的 requested；
- denied 的工具调用不得出现 started；
- replay 不能修改原始事件；
- compaction 不得删除原始事件；
- artifact 内容通过 hash 校验；
- 敏感字段不得以明文进入普通日志。

### 7.4 ToolExecutionContext 草案

```python
@dataclass
class ToolExecutionContext:
    session_id: str
    turn_id: str
    step_id: str
    agent_id: str
    user_input: str
    project_root: Path
    capability_set: CapabilitySet
    cancellation: CancellationToken
```

### 7.5 CapabilitySet 草案

```python
@dataclass(frozen=True)
class CapabilitySet:
    allowed_tools: frozenset[str]
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    network_enabled: bool
    shell_enabled: bool
    max_processes: int = 1
```

Capability 由父级授予。子 Agent 只能获得父级能力的子集。

---

## 8. 分阶段实施计划

## 8.1 阶段 0：冻结当前行为与离线基线

### 目标

建立稳定、离线、可重复的测试基础，防止后续重构缺少行为参照。

### 工作项

- 修复测试导入路径，确保导入当前工作区代码；
- 修复 tokenizer fallback 的隐式联网问题；
- 实现 Scripted/Fake LLM Client；
- 实现 Fake Tool、Fake Approval Provider 和临时项目 fixture；
- 增加 Query Loop 端到端测试；
- 增加取消、超时、未知工具和最大循环测试；
- 建立最小评测任务格式；
- 记录当前行为、测试数量和已知限制。

### 必测场景

1. 模型直接返回文本；
2. 单次工具调用后返回文本；
3. 一次响应包含多个工具调用；
4. 工具返回失败；
5. 工具抛出异常；
6. 模型调用未知工具；
7. 达到最大 Step 数；
8. 取消正在执行的 Turn；
9. 工具超时；
10. 大工具结果；
11. 上下文接近限制；
12. 测试环境禁止网络时仍能运行。

### 完成定义

- 所有测试无需真实 API key；
- 所有测试无需公网；
- `undomesh.__file__` 指向当前仓库；
- 原有测试全部通过；
- 新增 Query Loop 集成测试稳定通过；
- 测试结果和基线写入文档。

### 禁止提前做

- SQLite 事件库；
- provenance 传播；
- Skill 自动演化；
- 复杂多 Agent 调度。

---

## 8.2 阶段 1：Turn–Step–Event 生命周期

### 目标

让 Query Loop 产生完整、结构化、可验证的运行事件，同时保持现有功能可用。

### 工作项

- 实现 EventType 与 AgentEvent；
- 实现内存 EventStore；
- 实现 Session/Turn/Step ID；
- 实现生命周期 Dispatcher；
- Query Loop 发出基础事件；
- 为异常、取消和循环上限建立终态；
- UI 回调逐步改为消费事件；
- 增加事件顺序和不变量测试。

### 生命周期扩展点

```text
before_turn
before_model_request
after_model_response
before_tool_execute
after_tool_execute
on_request_error
after_turn
```

### 完成定义

- 一次工具任务产生完整 Turn/Step/Model/Tool 事件；
- 文本任务同样产生闭合 Turn/Step；
- 异常和取消不会留下未闭合状态；
- 事件 sequence 单调递增；
- REPL 与桌面 UI 的基本功能不退化；
- 事件监听器失败策略有明确测试。

---

## 8.3 阶段 2：统一工具执行管线与能力控制

### 目标

让所有工具调用进入同一个不可绕过的安全执行入口。

### 标准管线

```text
Tool Request
  → Tool Existence Check
  → Input Schema Validation
  → Capability Check
  → Path Canonicalization
  → Static Policy
  → Provenance Policy（阶段 4 完善）
  → User/Auto Approval
  → Sandboxed Runner
  → Result Externalization
  → Audit Event
```

### 工作项

- ToolExecutor 保存并使用执行依赖；
- 接入 PermissionManager；
- PermissionManager 不再自行依赖 UI callback，而通过 ApprovalProvider 抽象请求审批；
- 统一文件路径解析；
- read/write/glob/grep/shell 共用边界策略；
- 子 Agent 使用真实受限 registry/capability；
- 工具执行支持取消和超时；
- shell 取消能够终止进程树；
- 大结果在写入模型上下文前完成外置；
- 审批缓存改用规范化参数和策略作用域。

### 路径安全要求

必须覆盖：

- `..` 路径；
- Windows 盘符与大小写；
- UNC 路径；
- 相似目录前缀；
- 不存在目标文件的父目录；
- symlink/junction；
- Git worktree；
- 多个 readable/writable roots；
- 读权限与写权限分别判断。

禁止用字符串 `startswith` 作为最终目录边界判断。

### 完成定义

- 所有工具调用只能通过统一管线；
- 未授权工具即使由模型直接返回也不能执行；
- denied 调用没有副作用；
- 子 Agent 无法扩大工具和路径权限；
- 所有边界场景有自动化测试；
- 大结果不会未经处理进入下一次模型请求；
- 取消后没有遗留子进程。

---

## 8.4 阶段 3：持久化事件日志、恢复、分叉与回放

### 目标

将内存事件迁移到持久化事实日志，并建立可重复的 Agent 调试能力。

### 存储方案

第一版使用 SQLite，至少包含：

```text
sessions
events
artifacts
replay_runs
schema_migrations
```

事件 payload 使用带版本号的 JSON。大型文本、二进制和敏感内容使用 ArtifactStore。

### 投影

- Model Context Projection；
- UI State Projection；
- Audit Projection；
- Metrics Projection。

### 回放模式

- `live`：模型与工具真实运行；
- `recorded-tools`：复用工具结果，重新调用模型；
- `recorded-model`：复用模型响应，重新验证策略；
- `dry-run`：仅计算工具与权限决策，不产生副作用。

### Fork

允许从已完成事件边界分叉，并覆盖：

- model；
- Skill 版本；
-权限策略；
-上下文压缩策略；
-多 Agent 调度策略。

### 完成定义

- 进程重启后能够恢复 Session；
- 能从历史事件创建新分支；
- replay 默认不会重新执行有副作用的工具；
- 原始事件不可被 replay 修改；
- artifact 有 hash 校验；
- schema migration 有测试；
- 能生成两种配置的行为差异报告。

---

## 8.5 阶段 4：Provenance 来源感知安全

### 目标

在普通危险命令判断之上，增加“数据从哪里来、要流向哪里”的风险分析。

### 第一版粒度

第一版采用内容块级和工具参数级 provenance，不实现 token 级动态污点追踪。

### 信任等级

```text
trusted_system
trusted_user
workspace
generated
external
untrusted
```

### 来源类型

```text
system_instruction
user_input
workspace_file
generated_file
tool_result
web_content
mcp_content
memory
skill
subagent
```

### 敏感 Sink

```text
shell.execute
filesystem.write
filesystem.read_sensitive
network.send
mcp.write
memory.persist
agent.delegate
```

### 初始策略示例

```text
external  → shell.execute              ask
external  → filesystem.read_sensitive  deny
external  → network.send               deny/ask
workspace → in-scope filesystem.write  allow
memory    → shell.execute               ask
subagent  → out-of-scope write         deny
```

### 敏感资源

第一版至少识别：

- `.env*`；
- SSH/GPG 密钥；
- 云服务凭证目录；
- Git credential；
- API key 配置；
-浏览器/应用凭证目录；
-项目外用户文件。

### 完成定义

- 每个外部工具结果具有来源标签；
- 工具请求记录主要输入来源；
- 不可信内容触发敏感 Sink 时能够阻止或升级审批；
- 审批信息能够解释 source-to-sink 路径；
- 恶意 README、网页、MCP 结果和记忆污染具有自动化场景；
- 不宣称绝对防御，只报告已覆盖的威胁模型。

---

## 8.6 阶段 5：事件驱动的上下文治理

**状态：已完成（2026-08-22，离线回归验证）**

### 目标

将 ContextManager 接入真实执行链路，并以事件投影方式完成安全压缩。

### 压缩流程

```text
Measure Surface
  → Prune Old Tool Results Without Model
  → Measure Again
  → Select Balanced Region
  → Generate Structured Summary
  → Validate Summary/Pairing
  → Append Compaction Events
  → Replace Model Surface Region
```

### 摘要结构

必须尽量保留：

- 用户目标；
- 已确认约束；
- 关键决策；
- 已修改文件；
- 重要工具结果；
- 失败尝试；
- 未完成任务；
- 必须保留的 call/artifact/event ID。

### 不变量

- Tool Call/Result 必须成对保留或成对压缩；
- 原始事件不得删除；
- 压缩失败不得破坏当前 Surface；
- context overflow 仅在压缩确实推进后重试；
- 压缩重试存在上限；
- 外置结果使用稳定 Artifact ID；
- provider usage 优先于本地估算。

### 完成定义

- 大结果不会占满模型上下文；
- 压缩后不存在孤立 Tool Result；
- 可从事件日志查看压缩前内容；
- overflow recovery 有端到端测试；
- 压缩前后 token 和结果质量被记录。

---

## 8.7 阶段 6：执行证据驱动的 Skill 演化

**状态：已完成（2026-08-22，候选晋升/拒绝/禁用/回滚均有固定测试）**

### 目标

从静态关键词路由升级为可评测、可版本化、可回滚的 Skill 生命周期。

### Skill 元数据

```text
skill_id
version
content_hash
status: candidate | active | deprecated
applicable_models
evaluation_count
success_rate
average_tokens
average_steps
average_duration
failure_categories
created_from_event_ids
```

### 生命周期

```text
Observed Failure/Reflection
  → Candidate Skill
  → Isolated Evaluation
  → Compare Against No-Skill and Previous Version
  → Safety Regression Check
  → Approve
  → Active
  → Monitor
  → Deprecate/Roll Back
```

### 路由输入

- 文本相关性；
- 标签和适用边界；
- 相似任务历史成功率；
- 当前模型适配度；
- token 成本；
- 近期失败惩罚；
- 用户显式 `$skill-name` 选择。

### 晋升要求

- 必须有无 Skill 基线；
- 必须与前一正式版本对照；
- 成功率不能显著下降；
- 安全场景不能回归；
- token/耗时退化必须在配置阈值内；
- 自动生成内容不得未经审核直接覆盖正式 Skill。

### 完成定义

- Skill 可版本化、禁用和回滚；
- 路由决策有可解释记录；
- 至少一组固定任务能够展示候选 Skill 的晋升或拒绝；
- 评测结果可重复；
- README 中的性能数字来自生成的报告。

---

## 8.8 阶段 7：约束驱动多 Agent

**状态：已完成（2026-08-22，依赖、冲突、并发、预算和 deadline 均有固定测试）**

### 目标

将固定角色委派升级为基于任务契约、依赖和文件冲突的可验证调度。

### TaskContract 草案

```python
@dataclass
class TaskContract:
    objective: str
    dependencies: list[str]
    readable_paths: list[str]
    writable_paths: list[str]
    expected_outputs: list[str]
    verification_commands: list[str]
    token_budget: int
    deadline_seconds: int
```

### 调度规则

- 只读任务允许共享工作区并发；
- 写集合明确不相交时允许并行；
- 写集合相交时串行或使用独立 Git worktree；
- 子 Agent 只能获得父级 capability 的子集；
- 子 Agent 默认返回 patch、报告和验证证据；
- 主 Agent 负责验收与合并；
- 审批请求必须传播到主 Agent/UI；
- 支持状态查询、steering、取消、deadline 和并发上限；
- 禁止无限递归委派。

### 对照实验

对同一任务比较：

1. 单 Agent；
2. 固定角色多 Agent；
3. 约束驱动多 Agent。

记录：

- 任务成功率；
- 总 token；
- 执行时间；
- 文件冲突次数；
- 返工次数；
- 子任务失败率；
- 人工审批次数。

### 完成定义

- 调度器能够检测明显写集合冲突；
- 每个子任务有独立状态和事件；
- 取消父任务能够终止其子任务；
- 子 Agent 不得绕过权限执行；
- 至少一个基准能证明约束调度相对固定角色的优势或明确代价。

---

## 8.9 阶段 8：评测、文档与发布

**状态：已完成（2026-08-22，94 项离线测试、演示脚本及发布文档已验证）**

### 目标

让项目能力可以被第三方复现和验证。

### 交付物

- 架构文档；
- 威胁模型；
- 事件协议说明；
- 回放使用指南；
- Skill 评测说明；
- 多 Agent 调度说明；
- Benchmark 任务集；
- 机器可读结果；
- 人类可读报告；
- 演示脚本；
- README 更新；
- 简历项目描述。

### 完成定义

- 新环境可以按文档运行离线测试；
- 示例任务能展示记录、分叉、回放和策略对照；
- 安全演示能展示恶意内容被追踪和阻止；
- 所有性能数字可追溯到评测文件；
- 简历表述不超过实际实现；
- 发布说明列出限制和未覆盖威胁。

---

## 8.10 阶段 9：多 Provider 兼容性与真实 Harness 评测

**状态：已完成（2026-08-22，qwen3.7-flash 真实 Tool Calling 冒烟通过，96 项离线测试通过）**

### 目标

将阶段 8 的 BYOK 配置升级为可诊断、可重复、不会外传任意项目内容的 Provider 兼容性 Harness。

### 工作项

- Provider 预设声明端点、模型建议、Key 环境变量、工具调用能力和兼容层级；
- 连接错误分类为鉴权、额度、模型、限流、网络和 Provider 错误；
- 对外错误报告脱敏，不回显账户、组织或原始 Provider 详情；
- 使用固定无敏感夹具验证两轮 Query Loop 和 Tool Calling；
- 真实测试使用临时事件/Skill 数据库，不持久化 API Key；
- 机器报告明确单次冒烟测试的声明边界。

### 完成定义

- qwen3.7-flash 在百炼中国区端点完成 `read_file → tool result → final response`；
- Harness 能输出结构化 JSON 并以退出码表达成功/失败；
- API Key 不出现在报告、普通配置和事件中；
- 离线测试覆盖诊断分类与脱敏；
- 不把单次 Provider 冒烟测试描述为质量或性能基准。

---

## 8.11 阶段 10：凭据与执行隔离强化

**状态：已完成（2026-08-22）**

目标是在不虚构 OS 沙箱能力的前提下，减少模型驱动子进程接触宿主凭据的机会。Shell 环境采用 allowlist，API Key、Token、密码和认证变量不向子进程继承；沙箱探测明确区分应用层进程组与可用 OS 后端。凭据提供内存后端和可选系统 Keyring 后端，普通配置继续禁止存储 Key。

完成证据：环境净化、沙箱能力报告、内存/Keyring 后端均有离线测试。当前 Windows 默认仍是应用层控制，不宣称内核文件系统或网络隔离。

## 8.12 阶段 11：Git/Patch 验收工作流

**状态：已完成（2026-08-22）**

统一 PatchSet 会解析触及路径、拒绝绝对路径和 `..` 越界、计算内容哈希，并在应用前执行 `git apply --check --whitespace=error-all`。应用结果返回 Patch ID、触及文件和验证命令退出码，便于主 Agent 验收。

完成证据：临时 Git 仓库中完成 check、apply、diff 验证；路径逃逸和畸形 patch 被拒绝。自动跨工作区合并仍不在本阶段范围。

## 8.13 阶段 12：可复现 Coding Benchmark 与发布自动化

**状态：已完成（2026-08-22，6/6 离线套件、103 项测试通过）**

BenchmarkRunner 使用无 shell 的参数数组运行固定任务，记录每项退出码、耗时和成功状态并生成 JSON。发布检查验证必需文档、全量 pytest 和 compileall；GitHub Actions 覆盖 Windows/Linux 与 Python 3.11/3.13。

完成证据：`benchmarks/offline_report.json` 为 6/6 套件通过，`scripts/release_check.py` 返回 `ready: true`。这不是 SWE-bench，也不代表真实模型编码成功率。

## 8.14 阶段 13：事务化 ChangeSet 工具链

**状态：已完成（2026-08-22，106 项测试通过）**

目标是将阶段 11 的 Patch API 接入真实 Tool Registry、Capability、Permission 与 Provenance 链路。`apply_patch_set` 同时验证旧/新文件路径、敏感目标、Patch 大小和可写根；仅允许 pytest、compileall 以及只读 Git diff/status 作为验证命令，并以无 Shell 参数数组执行。

Patch 应用前保存触及文件快照。任一验证失败时恢复旧文件并删除新建文件，返回 `rolled_back` 证据；成功时返回 Patch ID、触及路径和验证退出码。外部或不可信来源影响 Patch 时仍由 source-to-sink 审批策略控制。

完成证据：真实 ToolExecutor 集成测试覆盖成功应用、敏感 `.env` 拒绝、Shell 操作符拒绝和验证失败回滚。自动提交、推送和跨分支合并仍不属于该工具权限。

## 8.15 阶段 14：Durable Change Transaction 核心协议

**状态：已完成（2026-08-22，122 passed、1 skipped；8/8 离线 Benchmark）**

目标是把回退从 Patch 失败处理提升为 UndoMesh 默认代码修改协议。每个变更在隔离工作区应用和验证；旧/新路径以 SHA-256 pre/postimage 持久化，原子 Journal 记录 `proposed → prepared → staged → verified → committing → committed` 全部状态。跨进程提交锁把目标复检和写入放在同一临界区，乐观冲突检测优先保留用户的新修改。

进入 `committing` 后发生中断时，应用启动恢复全部 preimage；提交后可通过事务 ID 显式回退，但 postimage 不匹配时拒绝覆盖新工作。创建、删除、重命名、文件模式和主机允许的符号链接进入同一协议。默认 Registry 关闭直接 write/edit/shell 旁路，并提供 `apply_patch_set`、`rollback_change_set`、`change_transaction_status` 三个用户可见接口。

完成证据：故障注入覆盖多文件提交中断、删除恢复、新文件清理、重命名复原、验证副作用隔离、并发修改冲突、启动恢复、恢复期间未知新内容保护、凭据净化、非法事务 ID 和符号链接父路径越界。多文件替换是崩溃可恢复而非全局原子；网络、数据库、包管理器副作用、ACL、xattr、硬链接身份和完整 OS 沙箱明确不在本阶段保证范围。

## 8.16 阶段 15：回退数据保留与引用安全清理

**状态：已完成（2026-08-22，131 passed、1 skipped；9/9 离线 Benchmark）**

目标是为内容寻址备份建立有界、可解释且不会误删共享数据的完整生命周期。已提交事务默认保留 30 天，拒绝/冲突/已回退事务保留 7 天；活跃、恢复中、恢复冲突和显式固定事务永不自动过期。用户可以验收事务使其立即具备清理资格，也可以固定重要回退点。

清理采用跨进程锁保护的 Mark-and-Sweep：先根据所有受保护 Journal 标记仍可达的 SHA-256，再将不可达且度过孤儿宽限期的 blob 原子移动到 trash，经过第二个宽限期才解除链接。Journal 保留审计哈希并标记 `rollback_available=false`，不会把已失效回退伪装成仍然可用。dry-run 提供事务、blob、空间和预算影响；验收、解除固定和真实清理进入人工确认链。

2 GiB 默认硬预算同时计算 blobs 与 trash。系统不会为了容量提前清除未到期、活跃、冲突或固定数据；空间无法安全回收时，新事务在修改工作区前失败。并发 blob 查重、预算判断和落盘使用同一锁，避免并行 Agent 合计超限。

完成证据：测试覆盖 dry-run 无副作用、两阶段回收、共享 blob 引用保护、固定/解除固定、活跃事务保护、容量 fail-closed 和人工确认。当前只实现整文件去重且备份未加密；unlink 不等于 SSD、快照、云同步或外部备份中的取证级安全擦除。

## 8.17 阶段 16：压缩、分块去重与备份加密

**状态：已完成（2026-08-22，144 passed、1 skipped；10/10 离线 Benchmark）**

目标是将 2 GiB 回退区从整文件副本升级为兼容旧格式的内容仓库。文件使用 Gear 内容定义边界切为 16 KiB 最小、64 KiB 平均、256 KiB 最大的块；块以 SHA-256 跨文件和跨版本去重，只有体积变小时才采用 zlib level-6 压缩，随后以 AES-256-GCM、随机 nonce 和绑定块摘要的 AAD 加密。

恢复读取原子 Manifest，逐块验证路径摘要、认证标签、解压上限、声明大小和块哈希，重组后再验证完整文件大小与 SHA-256。格式头携带版本、压缩/加密标志、key ID 和 nonce；旧 `blobs/` 原始快照继续可读。默认生成 owner-only 本地密钥，也支持环境提供 32 字节 hex/base64 密钥。

容量按照压缩加密后的 chunks、manifests、legacy blobs 和 trash 计算。自动重要度只为已到期候选排序：低重要度优先、同级更旧优先，不能越过 active/recovery-conflicted/pinned 或未到期保护。启动和每六小时执行策略清理，`transaction_storage_status` 提供逻辑/物理容量、压缩率、去重收益和剩余空间。

完成证据：测试覆盖密文无明文、压缩收益、中间插入后的内容定义块复用、相同内容去重、篡改认证失败、错误密钥失败、旧格式读取、空文件、压缩后配额、恶意 Manifest 路径、共享块 GC 和重要度排序。本地密钥与密文同机存放、Manifest 元数据/哈希未加密，密钥轮换与 Keyring 托管仍不在本阶段保证范围。

## 8.18 阶段 17A：流式恢复与超大文件热备

**状态：已完成（2026-08-22，151 passed、1 skipped；11/11 离线 Benchmark）**

目标是落实分层恢复设计中最先影响数据安全的部分。普通代码文件继续使用 2 GiB、7–30 天的 warm store；默认 256 MiB 以上文件进入物理独立的 hot vault，保留 24 小时并设置 50 GiB 上限与 2 GiB 磁盘余量。任一准入检查、分块写入、认证或 Manifest 发布失败，事务都会在删除工作区文件前拒绝执行。

新增 `delete_files_transactionally` 作为无法用 unified diff 表达的大型二进制删除入口。源文件直接流式经过内容定义分块、条件压缩和 AES-GCM，内存上限不随文件大小增长；恢复同样逐块写入 sibling temp，fsync、原子替换后重新读取完整目标并验证 SHA-256。`audit_transaction_recovery` 在无写入条件下验证所有 preimage 并区分 `available`、`conflicted`、`expired`、`corrupt`。

热备到期由启动/周期保留任务清除，活跃、恢复中和显式固定事务受保护。提前执行永久清理必须先 dry-run，预览把精确 transaction IDs、content digests 和 logical bytes 绑定为 `purge_token`；真实执行重新计算范围并通过不可缓存的一次性人工审批。共享块仍按引用保留。“永久”仅代表 UndoMesh 解除引用并 unlink 本地对象，不承诺 SSD、快照、云同步或 Git 中的取证级擦除。

完成证据：覆盖不依赖源文件 `read_bytes()` 的流式路径、超大文件删除与字节级一致恢复、容量 fail-closed、24 小时到期、Pin 延期、可用/冲突/损坏健康审计、错误清理 token、无审批拒绝和永久清理。当前 Python Gear 循环尚未针对 10 GiB 吞吐优化；Patch staging 仍会复制工作区，超大二进制删除应使用专用事务工具；恢复租约、独立补偿事务和多事务统一预检留到 17B。

## 8.19 阶段 17B：恢复租约与独立补偿事务

**状态：已完成（2026-08-22，169 passed、1 skipped；13/13 离线 Benchmark）**

恢复现在创建独立 Journal，不再把原事务改写为 `rolling_back/rolled_back`。状态机为 `requested → preflighting → ready → materializing → verified → committing → completed`，并实现 `unavailable/conflicted/corrupt/failed/recovering/aborted` 异常终态。系统先计算多事务连续闭包与每个路径的最终目标，对 warm/hot Manifest、Chunk、Key、现场冲突和完整临时空间建立恢复租约；所有目标完成物化和哈希后才进入提交，每个路径只写一次。

提交中断时，启动恢复只接受现场等于 target 或 pre-restore expected 两种状态，随后补偿回 expected；未知内容进入 `conflicted` 并保留租约。`abort_restore_transaction` 是人工审查后释放冲突租约的显式入口。旧 `rollback()` 和 `rollback_change_set` 均已委托给单源补偿事务，原 Change Journal 保持 committed。详细协议见 `docs/RESTORE_TRANSACTIONS.md`。

## 8.20 阶段 18：Prompt Checkpoint 与安全 Rewind

**状态：已完成（2026-08-22，169 passed、1 skipped；13/13 离线 Benchmark）**

参考公开 checkpoint 语义而非私有源码，每个 `TURN_STARTED` 现在都是用户提示恢复点，成功 ChangeSet 的 transaction ID 从后续 `TOOL_COMPLETED` 事件组成恢复闭包。已提供仅代码、仅会话、代码与会话、从此处摘要四种动作。

代码回退先对恢复点之后的全部事务做无写入预览，再交给 17B 的统一恢复事务；任何未知新内容在第一处文件修改前中止。会话回退通过 append-only Fork 表达，摘要通过 `CONTEXT_COMPACTED` 投影表达，均不删除历史事件。Checkpoint 显示 `available/conflicted/unavailable`、事务数量与缺失依赖；代码实际恢复进一步完成逐块认证。REPL、非交互 CLI 和桌面 Recovery Center 均已接入，详见 `docs/PROMPT_CHECKPOINTS.md` 与 `docs/DESKTOP_CLIENT.md`。

## 8.21 阶段 19：生产控制面补强

**状态：核心实现完成（2026-08-24）；Windows 文件/网络隔离与平台实机验证保留为明确边界**

目标是在不重写 Query Loop 和恢复核心的前提下补齐成熟 Harness 的确定性扩展与运行隔离：

- [已完成首版] Lifecycle Hook Bus：pre/post/failure/runtime-event 生命周期，阻断决策只能单调收紧并写审计事件；
- [已完成] 用户级、仓库级、子目录级与 override 项目指令链，并提供来源和字节预算诊断；
- [已完成] App Server、单写者 Command Queue、增量状态流、持久进度、取消和显式中断恢复；
- [部分完成] 可替换 Sandbox Backend；Linux Bubblewrap 已支持项目写边界、宿主只读与默认断网，Windows 原生隔离及 CPU/内存额度仍待实现；
- [已完成核心] Manifest 整体认证、Key ID、轮换、显式导出和旧 Key 可读性审计；企业 KMS/HSM 托管属于部署适配；
- [已完成] 外部声明式 Hook 摘要信任和非可执行策略 Schema。

当前证据：Hook、指令、持久任务中断/恢复、Manifest 篡改/轮换、声明式 Hook 信任、Bubblewrap/Windows Job Object 适配均有离线测试。Windows 文件/网络隔离、企业密钥托管和实机 seccomp 策略仍按能力报告明确降级。

## 8.22 阶段 20：生态协议与工程可观测性

**状态：核心实现完成（2026-08-24）；外部 MCP/SSE/OAuth 兼容矩阵和 1/10/50 GiB 实机数据待运行**

- MCP Client：STDIO、Streamable HTTP、OAuth、按工具审批和 Schema 延迟加载；
- OpenTelemetry Trace/Metric/Log，可关闭且默认脱敏；
- 真 Git Worktree、基线提交、候选 PatchSet、父 Agent 验证和确定性合并；
- 桌面 Diff/Review/Permission/Receipt 一体化工作台；
- Native FastCDC/Zstandard/Reflink 后端与 1/10/50 GiB 性能矩阵。

完成门槛：MCP 不得绕过 ToolExecutor/Provenance，Telemetry 不落盘凭证，Worktree 冲突测试通过，大文件性能结果可复现且保留失败边界。

## 8.23 阶段 21：分发、SDK 与标准评测

**状态：核心实现完成（2026-08-24）；标准数据集实跑、平台证书签名和发布验证依赖外部资源**

- JSON-RPC/ACP 兼容层与 Python SDK；
- Plugin Manifest，打包 Skill、Hook、MCP 依赖和可选 UI 元数据；
- 在强 Sandbox 下实验 native/code/both 工具呈现模式；
- SWE-bench Verified 子集、Terminal-Bench、自建恢复与 Prompt Injection 基准；
- 签名桌面安装包、自动更新和脱敏崩溃报告。

完成门槛：SDK 不复制运行时逻辑，Code Mode 写操作仍走事务与审批，标准评测固定模型/温度/权限/工具集并报告成本与置信区间。

对标依据、为什么不直接迁移为“万物皆插件”以及完整优先级见 `docs/COMPETITIVE_ANALYSIS.md`。

---

## 9. 版本与里程碑

### v0.2 Runtime Foundation

包含：

- 阶段 0；
- 阶段 1；
- 阶段 2。

发布门槛：

- 离线集成测试稳定；
- 基础事件生命周期完成；
- PermissionManager 接入真实工具链路；
- 路径边界和子 Agent capability 漏洞修复。

### v0.3 Replayable Runtime

包含：

- 阶段 3；
- 阶段 5。

发布门槛：

- SQLite 持久化；
-恢复、Fork 和安全回放；
-事件投影式上下文压缩；
-context overflow recovery。

### v0.4 Provenance Security

包含：

- 阶段 4；
- 安全任务集和威胁模型。

发布门槛：

- 来源标签贯穿输入、工具结果和执行请求；
- source-to-sink 策略可解释；
- 间接 Prompt Injection 场景有可重复测试。

### v0.5 Evidence-driven Harness

包含：

- 阶段 6；
- 阶段 7；
- 阶段 8。

发布门槛：

- Skill 对照评测与版本晋升；
- 约束驱动多 Agent；
- 完整 Benchmark 和技术报告。

### v0.6 Production Workflow Foundation

包含阶段 9–12：Provider Harness、凭据/执行隔离、Git Patch 验收、离线 Benchmark 与 CI。发布门槛为真实安全夹具 Tool Calling 通过、子进程环境不继承凭据、Patch 验证证据完整、发布检查 ready。

### v0.7 Durable Change Transactions

包含阶段 13–14：受权限链约束的 ChangeSet、隔离验证、内容寻址备份、持久化状态机、并发冲突保护、启动恢复和安全显式回退。发布门槛为默认无直接修改旁路、故障注入通过、事务收据可查询且所有保证边界有文档。

### v0.8 Bounded Rollback Lifecycle

包含阶段 15：显式验收/固定、状态保护、引用安全 GC、两阶段 trash、容量预算和回退过期审计。发布门槛为共享 blob 不误删、活跃与冲突事务不自动过期、真实清理需确认、超预算在根工作区修改前失败。

### v0.9 Encrypted Deduplicated Backup Store

包含阶段 16：内容定义分块、块级去重、条件压缩、认证加密、旧格式迁移兼容、定期清理和容量可观测性。发布门槛为错误密钥/篡改 fail-closed、恢复完整文件哈希一致、共享块不可误删、2 GiB 预算按物理存储计算。

---

## 10. 测试策略

### 10.1 测试层级

#### 单元测试

验证纯函数和局部不变量：

- 路径规范化；
- capability 子集判断；
-事件序列校验；
-provenance 策略；
-Skill 评分；
-token 估算 fallback。

#### 组合测试

验证多个模块真实接线：

- Query Loop + EventStore；
- Tool Pipeline + PermissionManager；
- Context Projection + Compaction；
- Agent Scheduler + CapabilitySet。

#### 端到端测试

使用 Fake LLM 和真实临时文件系统完成完整任务：

- 读取并修改文件；
- 权限拒绝；
- 上下文压缩；
- 恢复和回放；
- 恶意内容传播；
- 子 Agent 冲突控制。

#### 安全回归测试

- 路径穿越；
- symlink/junction 越界；
- shell 拼接；
- 未授权工具；
-审批缓存混淆；
-间接 Prompt Injection；
-敏感文件读取；
-数据外传；
-记忆污染；
-子 Agent 权限扩大。

#### 真实模型评测

真实模型评测不进入默认测试套件，单独运行并记录：

- provider；
- model/version；
- temperature/seed（如果支持）；
- Harness commit；
- Skill 版本；
-任务集版本；
-token、成本、耗时和结果。

### 10.2 测试确定性要求

- 默认测试不得访问公网；
- 默认测试不得依赖用户主目录配置；
- 默认测试不得使用真实 API key；
- 时间、ID 和模型响应应可注入；
- 临时文件必须在测试目录；
- 测试后不得遗留进程；
- 数据库 schema migration 必须向前测试；
- 并发测试应有超时，避免永久挂起。

### 10.3 CI 门禁

每个阶段至少要求：

```text
format
lint
type-check
unit tests
integration tests
security regression tests
```

对核心安全代码降低覆盖率或删除测试，需要在 PR 中说明原因。

---

## 11. 评测体系

### 11.1 任务成功指标

- 最终测试是否通过；
- 目标文件是否正确修改；
- 是否满足显式约束；
- 是否产生不相关改动；
- 是否需要人工修复。

### 11.2 Harness 效率指标

- input/output token；
-缓存命中 token；
-模型请求次数；
-工具调用次数；
-总 Step 数；
-总耗时；
-失败重试次数；
-压缩次数；
-外置结果大小；
-估算成本。

### 11.3 安全指标

- 危险操作拦截率；
- 安全操作误报率；
- 需要人工审批的比例；
- 路径越界成功次数；
- 未授权工具执行次数；
- source-to-sink 解释覆盖率；
- 敏感数据进入模型/日志次数。

### 11.4 多 Agent 指标

- 子任务成功率；
-写冲突次数；
-返工次数；
-并行加速比；
-额外 token 开销；
-取消传播时间；
-孤儿任务数量。

### 11.5 对照实验要求

任何宣称“提升”的结果必须包含：

- 相同任务集；
- 相同模型配置；
- 相同工具能力；
- 基线 Harness；
- 改进 Harness；
- 多次运行或误差说明；
- 失败案例；
- 原始机器可读结果。

---

## 12. 安全威胁模型

### 12.1 保护对象

- 项目文件；
-项目外用户文件；
-凭证和密钥；
-网络访问权；
-Git 历史；
-长期记忆；
-用户审批意图；
-Agent 事件和审计记录；
-模型 API 成本预算。

### 12.2 攻击来源

- 恶意用户输入；
- 被污染的仓库文件；
-网页/搜索结果；
-MCP/App 返回内容；
-依赖包输出；
-恶意 Skill；
-被污染的长期记忆；
-子 Agent 输出；
-模型幻觉的工具参数。

### 12.3 安全边界

- CapabilitySet；
-规范化路径边界；
-PermissionPolicy；
-ApprovalProvider；
-OS Sandbox；
-ArtifactStore；
-Event/Audit Store；
-敏感信息过滤。

### 12.4 不作保证

项目不保证：

- 识别所有自然语言 Prompt Injection；
- 在无 OS 沙箱时完全阻止恶意 shell；
- 第三方模型、MCP 或网络服务不保留数据；
- 自动生成的安全判断没有误报或漏报；
- 对未知平台链接、驱动或内核漏洞提供隔离。

---

## 13. 数据与隐私规则

- API key、密码和 token 不进入事件 payload；
- 工具参数日志在落盘前经过脱敏；
- ArtifactStore 支持敏感标记和不同保留策略；
- memory 默认项目作用域，不默认跨项目共享；
-用户能够查看、导出和删除记忆；
-外部内容不得自动晋升为 trusted；
-真实模型评测数据不得提交私有仓库内容；
-回放默认复用脱敏后的记录。

---

## 14. 工程执行规则

### 14.1 每个任务开始前

开发者必须明确：

1. 对应本文档哪个阶段；
2. 解决哪个已知问题；
3. 修改哪些模块；
4. 是否改变事件或安全不变量；
5. 如何自动验证；
6. 是否需要 ADR。

### 14.2 每个 PR/提交的最小内容

- 明确问题和范围；
- 代码修改；
- 自动化测试；
- 必要的文档更新；
- 风险和限制；
- 验证命令和结果。

### 14.3 需要 ADR 的情况

以下变化必须新增 `docs/adr/NNNN-title.md`：

- 替换事件存储方案；
- 改变 Turn/Step/Event 定义；
- 改变安全边界或审批优先级；
- 允许子 Agent 扩大能力；
- 改变回放的副作用规则；
- 引入新的核心框架或运行时；
- 打乱阶段依赖；
- 取消本文档中的重要不变量。

ADR 至少包含：Context、Decision、Alternatives、Consequences、Migration。

### 14.4 范围控制

发现新需求时按以下顺序判断：

```text
是否服务于当前阶段验收？
  否 → 进入 Backlog
  是 → 是否能复用成熟组件？
        是 → 集成而非重写
        否 → 最小实现并补测试
```

### 14.5 完成定义（全局）

一个功能只有同时满足以下条件才算完成：

- 接入真实主链路；
- 有成功、失败、取消路径；
- 有自动化测试；
- 有可观察事件或指标；
- 有安全边界；
- 有文档；
- 不依赖隐藏的开发机状态；
- 声明与实际行为一致。

---

## 15. 风险与缓解

### 风险 1：事件模型过度设计

缓解：第一版只覆盖当前 Query Loop 必需事件；没有实际生产者或消费者的事件不添加。

### 风险 2：一次性重写导致不可运行

缓解：先使用内存 EventStore 包装现有 Query Loop，再逐步替换；每阶段保持测试通过。

### 风险 3：安全系统只有规则数量，没有真实隔离

缓解：Capability、路径校验和 OS sandbox 优先；AI 分类只作为附加信号。

### 风险 4：Provenance 精度不足

缓解：明确第一版为块级近似；记录 unknown 来源；对高风险 unknown 默认 ask/deny。

### 风险 5：Skill 演化被模型随机性误导

缓解：固定任务集、对照基线、多次运行、置信区间和人工晋升门禁。

### 风险 6：多 Agent 增加成本却没有收益

缓解：把单 Agent 作为永久基线；没有指标优势时如实记录适用边界，不强行宣称提升。

### 风险 7：测试只验证 mock，不代表真实能力

缓解：默认离线测试保证确定性，另外保留版本化真实模型评测，两者不混淆。

### 风险 8：项目规模失控

缓解：坚持非目标清单；新组件必须有当前阶段消费者；删除无实际接线的抽象。

---

## 16. Backlog（不进入当前阶段）

- ACP 兼容；
- MCP 完整接入；
- OpenTelemetry exporter；
- Web 可视化回放界面；
- 多模型路由；
-远程 Agent worker；
-分布式 EventStore；
-语义级代码合并；
-自动生成 Benchmark；
-多模态输入；
-插件市场；
-云端会话同步。

Backlog 项只有在核心阶段完成或成为其必要依赖后才进入实施计划。

---

## 17. 第一开发迭代

### 迭代目标

在不改变现有用户功能的前提下，建立离线 Query Loop 基线和最小事件生命周期。

### 迭代范围

1. 确保测试从当前仓库导入；
2. 修复 token fallback 隐式联网；
3. 新增 ScriptedLLMClient；
4. 覆盖文本、单工具、多工具、失败、循环上限和取消；
5. 定义 EventType、AgentEvent 与 InMemoryEventStore；
6. Query Loop 发出 Turn/Step/Model/Tool 基础事件；
7. 测试事件顺序和闭合不变量；
8. 保持 UI 与现有工具行为不变。

### 迭代完成条件

```text
[x] 原有测试全部通过
[x] 新增集成测试全部通过
[x] 默认测试完全离线
[x] 当前仓库导入路径得到断言
[x] 文本任务事件序列完整
[x] 工具任务事件序列完整
[x] 失败/取消任务存在明确终态
[x] 没有提前实现数据库、Skill 演化或复杂多 Agent
```

### 建议提交拆分

1. `test: establish offline query-loop baseline`
2. `fix: make token counting deterministic offline`
3. `feat(runtime): add immutable agent event model`
4. `feat(runtime): emit turn and step lifecycle events`
5. `test(runtime): enforce lifecycle invariants`

---

## 18. 执行检查表

每完成一个阶段，更新以下内容：

```text
[ ] 阶段工作项完成
[ ] 完成定义全部满足
[ ] 测试数量与结果已记录
[ ] 安全回归已运行
[ ] 基准数据已生成
[ ] 限制和已知问题已记录
[ ] README 与 TECHNICAL.md 已同步
[ ] ADR 已补充（如需要）
[ ] 下一阶段前置条件满足
[ ] 简历描述未超过实际实现
```

---

## 19. 路线变更记录

| 日期 | 变更 | 原因 | 影响阶段 | ADR |
|---|---|---|---|---|
| 2026-08-22 | 建立 v0.2–v0.5 执行基线 | 将项目从功能复刻调整为可回放、来源感知、可评测的 Harness Runtime | 全部 | 暂无 |
| 2026-08-22 | 完成阶段 5–7 | 接入事件压缩、Skill 证据门禁和任务契约调度；89 项离线测试通过 | 5–7 | 暂无 |
| 2026-08-22 | 完成阶段 8 与 BYOK 配置 | 增加主流 Provider 预设、自定义端点、内存 Key、连接测试和发布复现材料；94 项测试通过 | 8 | 暂无 |
| 2026-08-22 | 完成阶段 9 | Provider 能力声明、脱敏连接诊断和真实安全夹具 Harness；qwen3.7-flash 两轮工具调用通过，96 项测试通过 | 9 | 暂无 |
| 2026-08-22 | 完成阶段 10–12 | 子进程凭据净化、可选 Keyring、Patch 验收、6 套件 Benchmark 和跨平台 CI；103 项测试通过 | 10–12 | 暂无 |
| 2026-08-22 | 完成阶段 13 | 事务化 Patch 工具接入真实权限链，验证失败自动回滚；106 项测试通过 | 13 | 暂无 |
| 2026-08-22 | 完成阶段 14 | 回退升级为默认 Durable Change Transaction；122 passed、1 skipped，8/8 离线 Benchmark | 14 | `docs/DURABLE_CHANGE_TRANSACTIONS.md` |
| 2026-08-22 | 完成阶段 15 | 增加引用安全保留、两阶段清理和 2 GiB fail-closed 预算；131 passed、1 skipped，9/9 Benchmark | 15 | `docs/TRANSACTION_RETENTION.md` |
| 2026-08-22 | 完成阶段 16 | 回退区升级为压缩、内容定义分块去重和 AES-GCM 加密；144 passed、1 skipped，10/10 Benchmark | 16 | `docs/CHUNKED_BACKUP_STORE.md` |
| 2026-08-22 | 插入并完成阶段 17A，将原阶段 17 顺延为阶段 18 | 先解决超大文件删除无法由 unified diff 承载、整文件内存读取及 2 GiB 与 10 GiB 的物理容量矛盾；151 passed、1 skipped，11/11 Benchmark | 17A、17B、18 | `docs/LARGE_FILE_RECOVERY_VAULT.md` |
| 2026-08-22 | 完成阶段 17B 与 18，并升级 CLI/Desktop | 独立补偿事务、恢复租约、崩溃补偿、四模式 Prompt Rewind 和客户端 Recovery Center 完成；169 passed、1 skipped，13/13 Benchmark | 17B、18 | `docs/RESTORE_TRANSACTIONS.md`、`docs/PROMPT_CHECKPOINTS.md`、`docs/DESKTOP_CLIENT.md`、`docs/SYSTEM_AUDIT.md` |
| 2026-08-24 | 交付阶段 19 首批控制面 | 分层项目指令、Lifecycle Hook Bus、默认能力快照修复及可选 Linux Bubblewrap 后端完成；179 passed、1 skipped，14/14 Benchmark | 19 | `docs/PROJECT_INSTRUCTIONS_AND_HOOKS.md`、`docs/SYSTEM_AUDIT.md` |
| 2026-08-24 | 完成阶段 19–21 本地核心与适配层 | AppService/Queue、认证 Manifest、MCP、Telemetry、Worktree、Review、SDK/RPC、Plugin、Code Mode、评测和签名更新完成；196 passed、1 skipped，17/17 Benchmark | 19–21 | `docs/STAGES_19_21_IMPLEMENTATION.md` |
| 2026-08-24 | 面试证据纵向收口 | Restore/Rewind/Audit 后台化、可信 Plugin 激活、Diff 摘要合并和无 Key 现场 Demo 完成；201 passed、1 skipped，18/18 Benchmark | 19–21 | `docs/INTERVIEW_DEMO.md` |

> 后续路线变更必须追加记录，不得静默修改阶段目标。

---

## 20. 参考实现的借鉴边界

### OpenAI Codex

重点借鉴：

- thread/turn/item 事件协议；
-结构化审批；
-OS sandbox 与权限分层；
-恢复、Fork、Compact、Interrupt；
-Skill 生命周期和运行时配置。

不复刻：完整 Rust Runtime、TUI、App Server 全协议和产品级账号能力。

### DeepSeek Harness

重点借鉴：

- Turn/Step 生命周期；
-事件源 Session；
-可替换 capability seam；
-工具调用配对的上下文压缩；
-真实组合测试。

不复刻：完整 Cordis 插件生态和所有运行模式。

### Claude Code

重点借鉴：

- deny-first 权限优先级；
-PreToolUse 风格扩展点；
-权限与 OS sandbox 双层防御；
-项目级配置与 Skill 发现；
-清晰的人机审批体验。

限制：Claude Code 核心执行引擎未完整开源，只依据官方文档和公开插件行为，不宣称源码级复刻。

### UndoMesh 的差异化

- 轻量 Python 实现，适合教学、实验和策略消融；
-把可回放与 Harness 对照实验作为一等能力；
-显式追踪内容来源并用于工具授权；
-Skill 必须通过执行证据验证后晋升；
-多 Agent 调度以任务契约和写集合为中心。

---

本文档是后续开发的范围与质量基线。实现遇到冲突时，优先保证安全不变量、可测试性和阶段完成定义；若必须改变这些约束，应先通过 ADR 明确记录决策。
