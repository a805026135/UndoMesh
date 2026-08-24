# UndoMesh 面试 QA

本文用于准备项目面试。回答模板强调事实、取舍和边界，不建议逐字背诵。面试时先给一句结论，再根据追问展开状态机、数据结构、失败场景和测试证据。

## 一、项目定位

### Q1：请用一分钟介绍这个项目。

UndoMesh 是一个用 Python 实现的恢复优先 AI Coding Agent Harness。它用 append-only 事件流驱动 Query Loop，将文件修改和恢复分别建模为可审计事务，并通过分层加密备份、Lease、完整预检、哈希验证和崩溃补偿保护未提交内容。Restore/Rewind/Audit 进入持久单写者队列；受信 Plugin 统一激活 Hook/MCP 并显式批准 Capability；多 Agent 候选使用真实 Worktree、验证和 Diff 摘要审批后合并。目前 201 项离线测试、18/18 固定 Benchmark 和一键无 Key 纵向 Demo 通过。

### Q2：为什么叫 Harness，而不是普通聊天机器人？

聊天机器人主要管理 Prompt 和回复；Harness 还要管理模型适配、Tool Schema、执行循环、权限、文件系统、上下文、持久化、恢复、子 Agent、评测和用户审批。模型提供推理能力，Harness 决定推理如何安全地作用到真实环境。

### Q3：项目最核心的亮点是什么？

不是 Tool Calling 本身，而是“可证明恢复”。系统在破坏前建立恢复证据，备份对象有明确生命周期，恢复依赖受 Lease 保护，恢复前验证所有目标和磁盘空间，提交后回读 Hash，崩溃后补偿或进入可见冲突。它把“可以 Undo”变成了一组能测试的安全不变量。

### Q4：这个项目解决了什么真实问题？

Coding Agent 会连续修改多个文件，也可能误删未提交数据。Git 只能恢复被版本控制保存的历史；编辑器 Checkpoint 往往只跟踪自己的编辑工具；大文件无法塞进普通 Diff。UndoMesh 解决的是 Agent 自身写操作的可追踪、可审计和有界恢复，并明确哪些操作不在保证范围内。

### Q5：为什么不直接使用 Codex、Claude Code 或 DeepSeek Harness？

生产工作当然可以直接用成熟产品。这个项目的价值是研究 Harness 内部问题并实现一个可解释的实验平台，而不是替代它们。通用的 Query Loop、Skill、权限和多 Agent 思路尽量借鉴；自主实现集中在恢复事务、分层存储、Provenance 和证据门控演进。

### Q6：你如何证明不是重复造轮子？

第一，项目没有重新训练模型，也没有发明另一套 Tool Calling 协议，而是复用 OpenAI-compatible API、SQLite、AES-GCM 等成熟组件。第二，竞品已有能力只做必要的轻量实现。第三，差异化问题明确：Claude Code 公开文档说明 Bash 修改不属于 Checkpoint，UndoMesh 让支持的写工具默认进入事务，并为大文件删除提供独立入口。第四，所有差异都有故障注入测试，而不是只做功能描述。

## 二、核心执行循环

### Q7：Query Loop 是怎么工作的？

用户输入打开一个 Turn。每轮模型请求打开一个 Step；若模型返回最终文本则结束 Turn，若返回 Tool Call，则依次记录请求、开始和结果事件，把 Tool Result 加回上下文，再进入下一个 Step。循环受到最大迭代、取消、超时和上下文预算限制。

### Q8：为什么要同时区分 Turn、Step 和 Tool Call？

Turn 对应一次用户目标；Step 对应一次模型推理；一个 Step 可以包含多个 Tool Call。分层后可以精确回答：失败发生在哪轮模型请求、哪个工具是否开始、是否有结果、Token 和耗时属于哪里，也便于 Replay 和运行时不变量检查。

### Q9：如何避免无限循环？

有最大迭代数、Turn 取消信号、工具超时、上下文预算和错误终态。后续可再加入重复 Tool Call 指纹、无进展检测和 Goal 级预算。仅依赖模型自己说“完成了”是不够的。

### Q10：多个 Tool Call 是并行执行吗？

ToolExecutor 支持批量调用，但是否并行要看工具副作用和写集。只读且独立的调用适合并行；写操作必须通过事务和冲突控制。不能为了降低延迟让两个未知写集的调用同时修改同一文件。

### Q11：模型返回不合法 Tool Call 怎么办？

先由协议适配层解析，再由 Tool Definition/Pydantic Schema 校验。未知工具、参数缺失或类型错误不会进入执行器副作用阶段，而是形成结构化失败结果供模型修正，并记录 Tool Failed 事件。

### Q12：模型说工具成功了，但实际没成功怎么办？

模型文本不具有执行权威性。只有 ToolExecutor 的结构化结果和事件终态才是事实；文件写成功还需要 Transaction Receipt 和 postimage Hash。模型无法通过自然语言把失败改写成成功。

## 三、事件溯源、Replay 与持久化

### Q13：为什么使用 Event Sourcing？

因为 Agent 状态不是单一聊天数组。需要同时支持审计、Replay、Fork、Compaction、Checkpoint、UI 进度和故障定位。Append-only 事件保留事实，Projection 决定当前模型看到什么，比直接修改 messages 列表更适合恢复和追责。

### Q14：Replay 会不会重新执行工具？

不会。Replay 只读取事件并重建 Projection。工具执行结果已经作为事件存在，重新执行会产生重复副作用，因此被严格禁止。

### Q15：Fork 是复制整个数据库吗？

不是。Fork 按指定事件边界复制源 Session 的逻辑前缀到新 Session，并记录 lineage。当前实现偏简单；更大规模可以使用 parent ID + boundary 的结构共享，避免复制长历史。

### Q16：事件与模型消息为什么要分开？

很多事实不应该进入模型上下文，例如审计元数据、原始流式 Chunk、恢复 Lease 和 UI 状态。反过来，所有真正进入模型请求的内容又应该能从事件解释。分开后既能完整审计，又能控制 Token。

### Q17：SQLite 为什么合适？有什么问题？

它零运维、事务成熟、适合单机 Harness。问题是 Python Connection 有线程归属，长写和并发写需要序列化；还需要 Schema Migration、备份、WAL 策略和损坏恢复。当前桌面端把 Event Store 固定在背景事件循环线程，下一步应升级为应用级单写者服务。

### Q18：如何验证事件流没有半截生命周期？

运行时 Invariant 检查 Turn、Step 和 Tool Call 的开始/终态数量及顺序；测试覆盖成功、工具失败、模型失败、取消和上下文溢出。持久化恢复后也可以重新跑不变量校验。

## 四、上下文、压缩与记忆

### Q19：长任务为什么会不稳定？

主要原因是上下文接近上限、历史噪声增多、工具大结果占用 Token、早期错误继续锚定后续推理，以及压缩破坏 Tool Call/Result 结构。项目从预算、外置、配对保护、结构化摘要和溢出重试多个层面治理。

### Q20：如何计算 Token？

优先使用适配模型的 tokenizer，无法精确匹配时使用可解释的估算。预算分别考虑系统提示、消息、Tool Schema、预留输出和安全余量。估算永远可能与 Provider 不同，因此还处理真实 Context Overflow 错误并重新压缩。

### Q21：为什么 Tool Call 和 Tool Result 不能拆开压缩？

多数模型协议要求结果引用已存在的 Call ID。如果只保留 Result 或只保留 Call，会形成非法历史或让模型误解执行状态。因此它们是压缩和淘汰的最小配对单元。

### Q22：大 Tool Result 怎么处理？

内容写入 Artifact Store，事件和上下文只保留稳定占位符、摘要、Digest 和按需读取方式。这样既保留可追踪性，又避免每轮重复发送完整结果。

### Q23：摘要会不会丢信息？

会，所以摘要不是事实源。原事件仍然保留，摘要只改变模型 Projection，并记录被遮蔽的 event IDs。需要核实时可以从 Artifact 或事件恢复原始材料。

### Q24：长期记忆与会话历史有什么区别？

会话历史记录一次任务发生了什么；长期记忆保存跨任务可复用的事实、偏好、经验和失败模式。记忆要经过提炼、分类和检索，不能把每次聊天全文当作长期记忆直接注入。

### Q25：Prompt Cache 做了什么？

Harness 尽量保持系统提示、Tool Schema 和稳定上下文前缀顺序不变，让支持缓存的 Provider 能复用前缀。项目不宣称控制 Provider 内部缓存，也不会把一次命中估计当作强保证。

## 五、Skill 系统

### Q26：Tool 和 Skill 有什么区别？

Tool 是原子执行能力，例如读取文件或应用 Patch；Skill 是高层知识与工作流，例如代码审查步骤。Skill 可以指导模型组合 Tool，但不能绕过 ToolExecutor 直接产生副作用。

### Q27：为什么需要二阶段路由？

Skill 少时可以把全部描述放进 Prompt；数量增长后会增加 Token 和误命中。第一阶段低成本召回候选，第二阶段结合边界、例子、负向条件和证据精排，只加载最终命中的完整内容。

### Q28：如何避免 Skill 自动优化把系统改坏？

候选版本和当前版本分离。收集成功率、Token、Step、耗时和安全失败，在固定任务与 Provider 条件下比较；满足最小样本、无安全回归、成功率不低于基线且成本在阈值内才晋升。否则保留当前版本。

### Q29：和 Codex/Claude Code Skills 相比还缺什么？

缺标准目录包、项目/用户/管理员层级、插件分发、诊断命令，以及 `scripts/references/assets` 的完整渐进披露。目前强项是路由和版本证据，生态兼容仍需补充。

## 六、权限、Prompt Injection 与凭证

### Q30：安全体系有哪些层？

Capability 限制 Agent 能看到哪些工具；Path Sandbox 约束文件边界；Permission Rule 做 deny-first 分类；Provenance Policy 控制来源到敏感 Sink；人工审批处理剩余高风险；Audit 记录决策。多层防御避免某一层失误直接导致副作用。

### Q31：什么是 source-to-sink 策略？

系统给用户输入、模型输出、网页、工具结果等数据标记来源和信任级别。当低信任外部内容影响 Shell、文件写入、凭证或永久清理等敏感 Sink 时，策略可以拒绝或要求审批。它关注数据流，而不只是命令中有没有危险关键词。

### Q32：如何防 Prompt Injection？

先把外部内容标为不可信数据，而不是系统指令；高风险 Tool Call 会携带活跃 Provenance 进入 Policy；对路径和命令继续做确定性校验；审批界面应说明风险来源。当前仍不是形式化信息流系统，但比单纯 Prompt 提醒更可测试。

### Q33：为什么 deny-first？

多个规则同时命中时，拒绝必须优先于允许，否则一个宽泛 allow 可能覆盖敏感路径或永久删除规则。其次是 ask，最后才是 allow。

### Q34：审批结果会缓存吗？

低风险、稳定范围的审批可以按策略缓存；永久 Hot Vault 清理、冲突恢复放弃和代码 Rewind 等操作采用一次性审批，不能因为用户之前同意过就永久放行。

### Q35：API Key 如何处理？

Key 来自环境变量或当前进程覆盖值，Pydantic 字段排除序列化和 repr；Provider 设置只持久化 provider、model 和 base URL；审计层按 token/password/secret/api_key 等字段脱敏。测试会读取配置文件确认 Key 没有落盘。

### Q36：当前 Sandbox 足够安全吗？

不够。它能限制项目路径和已知命令风险，但进程仍运行在宿主 OS 权限下。生产版本应增加 Landlock/seccomp、Windows 受限 Token/AppContainer 或容器后端，并限制网络、CPU、内存、进程和文件描述符。

## 七、Change Transaction

### Q37：为什么文件写入要事务化？

Agent 可能在长任务中连续修改多个文件。没有事务时，很难知道修改前内容、预期现场、提交进度和失败后的补偿依据。Change Journal 把这些信息变成明确状态和 Hash 证据。

### Q38：一次变更事务的流程是什么？

解析 Patch、规范路径、记录 preimage、备份、在 staging 应用、验证 postimage、获取 commit lock、重新检查现场、逐路径安装、回读验证并提交 Journal。任一预检失败都不修改工作区。

### Q39：为什么要保存 preimage 和 postimage Hash？

Preimage 防止提交时覆盖用户新修改；postimage 证明实际安装结果符合事务预期；恢复时二者还能区分现场是原状态、事务状态还是第三种未知内容。

### Q40：为什么不用反向 Patch？

反向 Patch 对二进制、删除、新建、上下文漂移和部分提交都不可靠。完整 Snapshot/Chunk Manifest 可以按 Digest 恢复精确字节，并验证一致性。

### Q41：多文件事务是原子的吗？

不是文件系统全局原子。每个文件通过 sibling temp、fsync 和 replace 实现单路径原子安装；多路径之间仍可能中断。系统通过 Journal、committed_paths 和补偿恢复提供可诊断的一致性，而不是夸大为数据库式全局原子。

### Q42：如果用户在预检后又修改文件怎么办？

进入 commit lock 后再次比较现场 Hash。只要不等于预期 preimage，就在第一处写入前报冲突。恢复事务也会在物化完成后做第二次现场检查。

## 八、分块、加密与存储生命周期

### Q43：为什么需要内容定义分块？

固定分块在文件头插入一个字节后会导致后续所有块边界漂移。内容定义分块根据滚动特征选择边界，中间插入通常只影响附近块，更适合跨版本去重。

### Q44：为什么选 16/64/256 KiB？

这是小代码文件和大文件之间的折中：平均块太小会增加 Manifest、Nonce 和文件系统对象开销；太大则降低局部修改复用率。当前参数是工程默认值，不是普适最优，后续应通过真实语料 Benchmark 调整。

### Q45：为什么先压缩再加密？

加密后的数据近似高熵，基本不可压缩；所以先对明文块尝试压缩，只在物理大小确实下降时保留压缩结果，再使用 AES-GCM 加密。

### Q46：AES-GCM 保护了什么？

它同时提供机密性和认证，Nonce 随机生成，块 Digest 作为 AAD 绑定。恢复时标签失败会立即拒绝。当前 Manifest 元数据没有作为一个整体签名，这是下一步需要补的边界。

### Q47：相同内容加密后为什么还能去重？

去重索引使用明文内容 SHA-256，仓库以 Digest 标识逻辑块；首次写入生成密文对象，后续相同块复用已有对象。随机 Nonce 不影响已有对象复用，但也意味着 Digest 泄露相等关系，因此不宣称抗确认攻击的强隐私模型。

### Q48：为什么默认 2 GiB？

2 GiB 是 Warm Cache 的可控默认预算，避免长期运行无限占用磁盘。它不是数学上能覆盖任意删除的保证；超出能力时必须走 Hot Vault、外部不可变来源或拒绝操作。

### Q49：如何恢复一个 10 GiB 文件，2 GiB 缓冲区装不下怎么办？

不能凭空从 2 GiB 恢复最坏情况下不可压缩的 10 GiB 数据。因此 256 MiB 以上文件默认进入独立 Hot Vault，预算和 24 小时 TTL 与 Warm Store 分开。删除前若 Hot Vault 容量或磁盘余量不足就拒绝，不会先删除再承诺恢复。

### Q50：Hot Vault 相当于文件没有删除，是否矛盾？

从工作区语义看文件已删除；从磁盘占用看短期副本仍存在。这是安全与释放空间之间的取舍。系统明确把 Hot Vault 定义为误删隔离，不把它描述为立即释放磁盘。若用户明确要永久清理，需要精确范围预览和一次性审批。

### Q51：什么时候清理备份？

Warm Store 根据 committed/terminal 状态、保留天数、Pin、重要度、年龄和预算清理；Hot Vault 默认 24 小时到期。活跃、恢复中、冲突、Pin 和被 Lease 引用的数据不自动清理。

### Q52：GC 如何避免删掉共享 Chunk？

使用 Mark-and-Sweep。先从所有受保护 Journal、Manifest 和 Lease 标记可达 Digest，再把不可达对象移入 Trash；度过宽限期后才 unlink。共享块只要仍被一个受保护对象引用就不会被删除。

### Q53：为什么分两阶段删除？

先移入 Trash 提供短暂恢复窗口，也避免扫描或并发状态短暂不一致造成立即不可逆删除。第二阶段才解除文件链接。

### Q54：unlink 等于安全擦除吗？

不等于。SSD 磨损均衡、文件系统快照、云同步和备份都可能保留数据。项目只承诺 UndoMesh 解除本地对象引用，不宣称取证级擦除。

## 九、Restore Transaction 与 Rewind

### Q55：为什么 Restore 要成为新事务？

如果直接把原 Change Journal 改成 rolled_back，会破坏历史事实，也难以表达多事务联合恢复、恢复失败和再次恢复。独立 Restore Journal 保留源事务 committed，同时完整记录本次恢复的计划、依赖、提交和结果。

### Q56：恢复前为什么要全量物化？

如果边恢复边覆盖工作区，最后一个 Chunk 损坏时前面的文件已经改变。全量物化先证明所有目标都能恢复且 Hash 正确，然后才进入 commit，保证依赖错误在第一处写入前暴露。

### Q57：Lease 解决什么问题？

恢复可能持续很久，期间 TTL 或 GC 可能认为对象到期。Lease 精确记录本次恢复所需的 Warm/Hot Digest，清理器必须跳过它们，直到完成、失败释放或冲突被人工处理。

### Q58：恢复中崩溃怎么办？

启动扫描非终态 Restore Journal。提交前状态可以安全标记 aborted；commit/recovering 状态检查每个现场是否等于 target 或 pre-restore expected，满足条件就补偿回 expected。出现第三种内容则进入 conflicted 并保留 Lease，避免覆盖用户修改。

### Q59：如果补偿数据也损坏了呢？

不能继续自动写。系统把恢复置为 conflicted，记录 recovery_error，保留 Lease，并继续完成应用启动，让用户诊断或选择外部来源。最后一轮故障注入专门覆盖了这个场景。

### Q60：恢复数据已经过期或被清理怎么办？

Checkpoint Health 显示 unavailable/expired，代码 Rewind 在第一处写入前拒绝。系统不会用空文件或不完整内容“尽力恢复”。未来可通过 Git/LFS/对象存储 T3 适配器提供替代来源。

### Q61：Prompt Checkpoint 如何找到相关文件事务？

每个 `TURN_STARTED` 是边界，系统扫描其后的成功 `TOOL_COMPLETED` 事件，从 Payload 收集 Transaction ID，按逆序组成恢复闭包，并检查同一路径的事务链是否连续。

### Q62：四种 Rewind 分别是什么？

Code 只恢复文件；Conversation Fork 到历史边界但保留当前代码；Both 先执行代码恢复再 Fork；Summary 不删事件，只写 Compaction 事件改变 Projection。

### Q63：Both Rewind 是原子的吗？

不是。文件系统和 Event Store 是两个持久域。当前顺序先完成代码 Restore，再创建会话 Fork；若 Fork 失败，代码已经恢复。系统会在 `REWIND_FAILED` 中记录 restore_id 和 `code_restore_completed=true`，明确部分完成，而不是伪装成全失败。

### Q64：和 Claude Code Checkpoint 相比改进在哪里？

Claude Code 公开文档说明只跟踪其 Write/Edit/NotebookEdit，Bash 和外部改动不跟踪。UndoMesh 同样不声称回滚任意外部副作用，但它把自身支持的文件写、Patch 和大文件删除统一收敛到 Change Transaction，并增加分层加密存储、恢复 Lease、健康预览、独立补偿事务和崩溃恢复。

### Q65：为什么还需要 Git？

本地恢复解决短期 Agent 误操作；Git 解决长期版本、协作、审查和远端复制。两者层级不同。UndoMesh 明确不把 Checkpoint 当版本控制替代品。

## 十、多 Agent

### Q66：为什么需要 TaskContract？

直接让子 Agent“去做这个任务”缺少可调度信息。TaskContract 把依赖、读写集、输出、验证、Token 和 Deadline 显式化，主 Agent 才能决定哪些任务并行、何时跳过、如何验收。

### Q67：如何避免两个 Agent 修改同一个文件？

Scheduler 在启动前比较规范化写集。写集有交集的任务不能进入同一并行波次；没有交集且依赖就绪的任务才能并行。最终写入仍经过事务现场检查。

### Q68：真实 Git Worktree 如何避免“审核的是 A，合并的是 B”？

审核收据不只保存 Diff Hash，还绑定审核时父 HEAD、候选 Commit、完整 Tree Hash、Binary Diff、验证命令和退出码。批准前重新验证父 HEAD、候选分支、Tree 与 Diff；任意漂移都会拒绝合并，合并后还会复核结果 Tree 与已批准 Tree 一致。

### Q69：子 Agent 能自己继续创建 Agent 吗？

委派能力由 CapabilitySet 控制，不应默认无限递归。主 Agent 持有全局预算和验收权。成熟实现还应限制最大深度、并发数、总 Token、Deadline 和消息大小。

### Q70：多 Agent 一定更快吗？

不一定。只有可分解、依赖较少、写集不冲突且任务足够大的工作才值得并行。小任务的上下文复制、模型调用和合并成本可能更高，所以 Benchmark 应同时记录完成时间、Token 和失败率。

### Q70.1：为什么多 Agent 回退不能只撤销目标 Agent 的 Commit？

后代任务可能读取它产生的接口、Schema 或文件。只撤销根任务会留下语法可用但语义失效的代码。UndoMesh 合并显式 dependencies 与读写集重叠关系，计算传递影响闭包，再按逆拓扑顺序撤销，保证先处理消费者、最后处理被依赖者。

### Q70.2：级联回退和重放有什么区别？

`cascade` 撤销整个影响闭包，最保守；`replay` 先撤销闭包，再跳过根任务重放后代候选，尽量保留仍成立的工作。重放若冲突就在隔离 Worktree 内失败，不会写主工作区。两种策略都把闭包和顺序写进 Plan SHA，并与 Review SHA 一起审批。

## 十一、多模型与 Provider

### Q71：为什么统一使用 OpenAI-compatible 接口？

它能用一个 Adapter 快速支持多家 Provider，减少 Harness 与模型厂商的耦合。代价是无法使用所有 Native Feature，因此项目把兼容层和 Provider 特性分开，不宣称完全等价。

### Q72：连通性测试检查什么？

使用用户选择的 provider、base URL、key 和 model 发起最小推理，验证网络、认证、模型名和基本协议。Tool Calling Harness 再验证工具 Schema、Call 和 Result 链路。它不评估模型解决真实 Coding Task 的质量。

### Q73：为什么真实百炼测试不算 Benchmark？

一次成功只证明当时该 Key、Endpoint、模型名和调用协议可用，没有固定任务集、重复样本、对照组、成本和置信区间，因此只能称 Smoke Test。

### Q74：Provider 返回限流或上下文错误怎么办？

Diagnostics 将认证、模型不存在、限流、服务端错误和 Context Overflow 分类。Overflow 触发压缩重试；限流后续应加入尊重 Retry-After 的指数退避和总重试预算；认证错误不应盲目重试。

## 十二、CLI、桌面客户端与线程模型

### Q75：为什么同时做 CLI 和客户端？

CLI 适合开发者、脚本和远程环境；桌面端适合任务导航、模型设置、审批和 Recovery Center。二者复用同一 Application/Runtime，避免出现两套业务逻辑。

### Q76：桌面端为什么使用 Tkinter？

它随 Python 提供、依赖少、能快速验证客户端工作流。缺点是样式、虚拟化、无障碍、编辑器集成和打包能力有限。当前目标是可用原型，成熟版本可以用 Tauri/原生壳通过 App Server 协议连接运行时。

### Q77：Tkinter 和 Asyncio 怎么协作？

Tk 主循环固定在主线程；UndoMesh Asyncio Loop 和 SQLite Connection 在背景线程创建并使用；背景结果通过 `root.after` 回到 UI 线程。审批 Modal 也通过主线程调度，背景操作等待用户结果。

### Q78：长时间恢复会冻结 UI 吗？

不会冻结 Tk 主线程，但会占用当前背景 Event Loop，使同一应用服务上的其他操作排队。下一步要引入 Command Queue、Worker 和细粒度进度/取消事件。

## 十三、测试、Benchmark 与工程质量

### Q79：201 个测试主要覆盖什么？

覆盖运行时生命周期、Replay/Fork、上下文压缩、权限与 Injection、Skill 演进、多 Agent 调度、Provider、事务写入、Retention、Chunk Store、大文件 Hot Vault、Restore、Checkpoint/Rewind、CLI 和 ViewModel。数量只是辅助指标，关键是包含故障注入和安全不变量。

### Q80：最重要的故障注入有哪些？

提交中途抛异常、恢复中断、Chunk 篡改、错误密钥、磁盘配额不足、用户并发修改、GC 与 Lease 并发、补偿数据损坏、跨域 Both Rewind 的 Fork 失败，以及上下文溢出后的压缩重试。

### Q81：18/18 Benchmark 代表什么？

它代表 18 个固定 Harness 套件都通过，用于检测组件与纵向接线回归；不是模型排行榜。第 18 套直接运行无 Key 面试 Demo 和可信 Plugin 激活链，报告仍区分离线工程证据与需要 Key/外部数据集的任务。

### Q82：为什么不用 Mock 就全部测试真实模型？

真实模型不确定、昂贵、受限流和版本变化影响，不适合作为每次提交的唯一门禁。核心状态机和安全规则需要确定性离线测试；真实 Provider 测试作为可选 Conformance/Smoke Test。

### Q83：覆盖率高就代表安全吗？

不代表。覆盖率只能说明代码路径被执行，不能证明断言质量、并发正确或威胁模型完整。安全更依赖不变量、负向测试、故障注入、边界声明和独立审查。

### Q84：CI 还可以怎么加强？

加入 Ruff/类型检查、跨平台文件系统矩阵、随机状态机测试、磁盘故障模拟、性能回归阈值、依赖漏洞扫描、Schema Migration 测试和 opt-in live Provider Conformance。

## 十四、架构取舍与反向质疑

### Q85：为什么不用 Git 实现全部恢复？

Git 对已提交文本版本很强，但 Agent 可能处理未跟踪文件、未提交修改、大二进制和临时数据。自动提交还会污染历史，也不能表达缓存到期、密钥和恢复 Lease。UndoMesh 把 Git 视为 T3 长期来源，而不是唯一短期恢复机制。

### Q86：完整保存文件是不是浪费空间？

逻辑上需要足够信息才能精确恢复，但物理上不必每版本保存整文件。内容定义分块、跨版本去重和条件压缩减少重复；Retention 和预算控制生命周期。对不可压缩的唯一大文件，信息论上无法用更小容量无损恢复，所以使用短期 Hot Vault 或拒绝。

### Q87：为什么不直接存反向操作日志？

反向操作适合语义明确的业务事务，但任意文件修改的逆操作可能依赖已丢失内容。系统保存可验证 preimage，再把恢复动作记录为补偿事务。外部业务系统未来可以实现专用补偿器。

### Q88：为什么不用数据库保存所有 Chunk？

大 Blob 放 SQLite 会增加 WAL、VACUUM、并发和损坏影响面。当前采用文件对象 + 原子 Manifest，SQLite 保存会话事件。未来可以抽象 Storage Backend，但不应让单个数据库同时承受高频事件和几十 GiB 流式对象。

### Q89：为什么不现在就做“万物皆插件”？

插件内核会引入生命周期、依赖图、版本、隔离、配置合并和错误传播复杂度。UndoMesh 当前更需要稳定 Capability Seam。先让 Hook、Storage、Sandbox、Provider 和 Tool 有清晰接口，再根据真实替换需求插件化，避免为了架构漂亮而重写。

### Q90：项目最大的技术债是什么？

OS 级 Sandbox、应用服务单写者/后台任务、Manifest/Key 管理、大文件 Native 性能和标准 Agent Benchmark。这些比再增加几个工具更影响生产可信度。

### Q91：如果重新设计一次，你会先做什么？

先定义事件 Schema、Tool Pipeline 和 Change/Restore 状态机，再做 UI 和 Skill。恢复和权限属于横切能力，晚加会迫使所有写工具重构。还会更早建立 App Server，让 CLI、桌面和 SDK 从一开始共享协议。

### Q92：项目中最难的 Bug 是什么类型？

不是普通语法 Bug，而是跨状态机边界：例如恢复已修改部分文件后失败、GC 同时清理依赖、启动补偿本身损坏，以及代码恢复完成但会话 Fork 失败。这类问题必须通过 Journal、Lease、幂等恢复和故障注入解决。

## 十五、对标与后续规划

### Q93：从 Codex 最值得学习什么？

分层 `AGENTS.md`、Skills 渐进披露、MCP 审批配置、Worktree/Review 工作流、App Server/SDK 和任务化客户端。UndoMesh 应吸收这些生态接口，但保持副作用统一经过 ToolExecutor。

### Q94：从 Claude Code 最值得学习什么？

Hook、Permission、Skill、Subagent 的职责分离，以及 `/doctor`、`/context`、`/diff`、`/rewind` 等可诊断交互。尤其是“确定性规则放 Hook/Permission，而不是只依赖 Prompt”这一点。

### Q95：从 DeepSeek Harness 最值得学习什么？

Service Definition/Provider/Consumer Capability Seam、可组合 Tool Pipeline、Event-sourced Session、Projection Cache、OpenTelemetry 和多种 Carrier。Code Mode 值得实验，但必须排在 Sandbox 和权限成熟之后。

### Q96：UndoMesh 相比三者还有什么独特价值？

它把短期文件安全作为主架构，而不是附属 Undo：Change/Restore 双 Journal、Warm/Hot 分层、Chunk 加密去重、Lease、恢复健康、崩溃补偿和显式清理审批组成一个完整闭环。同时它规模较小，适合解释和实验每个不变量。

### Q97：下一阶段优先级是什么？

P0 是 Lifecycle Hook、分层项目指令、OS Sandbox、App Server/后台任务、Manifest 认证和 Key Rotation；P1 是 MCP、OpenTelemetry、真 Git Worktree、Diff/Review 和 Native 大文件性能；P2 才是 Plugin 分发、SDK、Code Mode 和更大 Benchmark。

### Q98：为什么 MCP 不是第一天就实现？

MCP 会迅速扩大工具和外部信任边界。如果 ToolExecutor、Provenance、审批和事件协议不稳定，接入更多服务器只会放大风险。现在核心控制面已经形成，下一阶段接入更合适。

### Q99：如何把项目推进到生产可用？

先收紧保证范围：只支持事务化写工具和受控 Sandbox。然后补 App Server、后台任务、迁移、Key 管理、Telemetry、安装升级、崩溃报告和标准性能测试；再进行第三方安全审查和真实仓库灰度。生产化是运维与可信度工程，不是继续增加 Prompt。

### Q100：如果面试官只记住一个点，希望是什么？

希望记住：我没有把 Agent 回退理解成一个 Undo 按钮，而是把它设计成“破坏前准入、分层存储、恢复依赖租约、全量验证、补偿事务、崩溃恢复和清理审批”的完整系统，并用故障注入验证每个失败边界。

## 十六、回答时应避免的表述

- 不说“完全复刻 Codex/Claude Code”。
- 不说“所有操作都能回滚”。
- 不说“多文件修改全局原子”。
- 不说“2 GiB 一定能恢复任意大文件”。
- 不说“AES-GCM 等于企业密钥安全”。
- 不说“201 个测试证明没有 Bug”。
- 不说“18/18 Benchmark 证明模型能力领先”。
- 不说“应用层路径 Sandbox 等于操作系统隔离”。
- 不说“目录副本就是完整 Git Worktree”。
- 不把计划中的 MCP、Hook、SDK、OpenTelemetry 和 Native CDC 写成已完成。

## 十七、面试展示建议

推荐按以下顺序演示：

1. 使用 CLI 或桌面端发起一个文件修改，展示 Change Transaction Receipt；
2. 打开 Recovery Center，解释 Checkpoint Health 和四种 Rewind；
3. 手动修改目标文件制造冲突，展示系统在第一处恢复写入前拒绝；
4. 展示大文件 Hot Vault 配置、Storage Status 和永久清理审批；
5. 展示 Event Replay/Fork，而不是再次执行工具；
6. 最后运行 `pytest`、离线 Benchmark 和 Release Check。

如果时间有限，只演示“正常恢复 + 人工并发修改冲突 + 测试证据”三个部分，最能体现项目的工程深度。
