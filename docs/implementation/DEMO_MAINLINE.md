# Section9：唯一演示主线

更新：2026-09-24。本轮处置验收见 [RESOLVE_ACCEPTANCE.md](RESOLVE_ACCEPTANCE.md)，前序调查验收见 [DEMO_ACCEPTANCE.md](DEMO_ACCEPTANCE.md)，接力先读 [HANDOFF.md](HANDOFF.md)。

## 已收敛的产品路径

**自由业务任务 → 应用侧观测 → 选择依据 → EvoMap 协作核查 → 建议审核 → 固定预案独立授权 → 实际配置执行 → 对账/独立验证 → 同案事故关闭 → 经验审核复用。** 同案单助手对照仍可单独运行。

入口 `/demo`，服务 `http://127.0.0.1:9160`。已接入应用是开源 support-agent，使用隔离的本地订单数据库；评委可以自行输入订单、物流、退款资格、常见问题。请求实际进入它的 LangGraph、模型和业务工具。它不是录制答案，也不是一个无边界通用助手。

业务执行时展示应用实际产生的事件。Section9 规则观察员区分真实处理失败与正常澄清、安全拒绝、明确人工请求。超过 30 秒仅提示；业务结束且 Langfuse 证据同步后，可按开关自动调查确认疑点，也可人工发起。**持续语义模型监视尚未实现**，没有规则告警不代表没有业务问题。

原生 EvoMap 用于独立身份的会话、证据交接、结果回读及成员接力。模型角色仍由 Section9 有界任务图执行：按来源分工的调查员、先独立提出反例的复核员、看完调查后再次复核、最后汇总的协调员。当前三个 native 身份由同一台 Section9 主机控制；不宣称多台远程自治 Agent 或原生托管规划器。

## 实际连接

```mermaid
flowchart TD
  UI[网页 /demo 与 OfficeScene] --> API[/api/v1/demo]
  API --> Demo[DemoWorkflow]
  Demo --> App[support-agent :9150 实际业务任务]
  App --> Models[远程 EvoMap 模型推理]
  App --> DB[(隔离业务数据库)]
  App --> Live[应用实时事件]
  App --> LF[应用 Langfuse SDK → 本地 :9030]
  Live --> Demo
  LF --> Demo
  Demo --> V1[ProductRegistry v1 Signal / Incident / InvestigationRun / Evidence]
  V1 --> Tasks[既有任务图 / 租约 / fencing / 预算与用量]
  Tasks --> Native[EvoMap 原生 session / message / context]
  Native --> Models
  Tasks --> Proposal[ProposalVersion → hash 绑定审核与版本历史]
  Proposal --> UI
  UI --> Approval[独立固定预案授权]
  Approval --> Execute[复用 ExternalRuntime 切换配置与重启]
  Execute --> Validate[原任务重放 / DB 真值 / 健康观察]
  Validate --> Incident[同一 v1 Incident 关闭]
  Incident --> Lesson[人工审核历史经验]
```

`/api/product` 的 ProductService 提供现成应用适配器、版本绑定、运行时和配置。新主线的事故/任务以 v1 为准；旧 Incident/Approval/Execution/Recovery **未自动迁移成 v1 对象**。原 `/agent`、Core/Worker、小智 Pair 和旧产品工作区保留自己的历史，不参与拼接本次事故证明。

## 复用与归属

| 能力 | 复用/适配 | 唯一归属与本次边界 |
| --- | --- | --- |
| 被治理业务 | support-agent、模型适配 patch、ExternalRuntime | 既有业务工具；新隔离 DB，未重写一个客服 |
| 应用观测 | 原 Langfuse 连接器、本地观测栈 | SDK 装进业务应用；准确 trace/版本/环境过滤，保留实际输入输出 |
| 业务对象 | ProductRegistry v1 | Signal、Incident、InvestigationRun、Evidence、Task、usage；额外 business_task 只保存业务会话关联与 UI 投影 |
| 协作 | 原任务图、租约、预算；新增 native transport | 分块内容校验并从远端回读；无本地静默替代；固定角色、按来源分支 |
| 方案 | 既有 ProposalVersion 契约 | 持久化版本与 hash 审核；认可建议不授予业务写权限 |
| 网页 | 原 React 前端、OfficeScene、Star Office 素材与 fallback | 任务、证据、角色、建议；无伪指标或固定胜负 |
| 对比 | 既有 v1 single/swarm 模式和用量账本 | 同一 frozen evidence、模型、每 run 预算（新运行 250000）；单助手也有完整事实；历史失败保留 |
| 执行/恢复 | 复用 runtime 与 Approval/ExecutionAttempt/ValidationReport/RecoveryReceipt 契约 | 固定 support_policy 预案适配进同一 v1 Incident；旧历史不混入本案 |
| 经验 | 既有 scoped registry | 验证后待审、同应用同源码版本引用；不自动发布 Hub |

CloudThinker Connections、Pulse、Resolve/RCA、协作侧栏、Runbook/审批作为工作流参照；完整对标分母仍是原规划的 36 项功能、83 个连接器与 4 项补充集成。这次只交付上述一条路径，不声明完整平台达成。

## 可演示的协作价值与证据要求

1. 调查员分别读取应用调用和绑定版本的工具/政策源码，不靠同一答案改写角色名。
2. 独立反例步骤不读取调查员输出；后续复核必须同时读到证据、调查和反例，再交给协调员。
3. 每次交接与结果提交需原生会话回读及内容哈希一致；网页消息能追到 native message ID。
4. 暂停调查员后，迟到结果被拒绝，已花用量保留，人工继续让其他成员接管未完成任务。
5. single/swarm 同案完整保留。前序政策适用性缺证案例中两者都选择不确定；最新否定人工语义案例中，团队给出与源码相符的根因候选，单助手保留根因不确定，单助手更快、更省。只有这两例具体观察，**尚无多案盲评或普遍质量优势证明**，不要预设演示结论。

本轮增加一个固定配置预案的执行、对账和独立恢复验证，并有一次有界证据补读。仍不覆盖动态规划、任意工具循环、任意代码自动修复或多租户权限。原生 PDRI、Hub 经验发布/检索、公共成员发现也不在已验收范围。

## 后续收敛顺序

1. 队友独立换题实跑并盲评同案结果；先定义任务成功/证据完整/错误结论/成本/延迟，再做多案对照，不为蜂群挑题或弱化 single。
2. 将旧发布、执行对账、恢复能力适配到 v1 的 ProposalVersion 和授权边界；先建立旧 ID 到新 ID 的明确关系及幂等迁移，验收前保留旧只读历史。
3. 增加经服务端验证的动态计划、按权限开放的只读工具、工作进程隔离和持久恢复；原生能力优先适配，不并建第三个调度器。
4. 收敛到 Workspace/Application/Environment 的权限域：观测只读、模型提出建议、人工批准具体不可变方案、执行器获得最小一次性授权、独立验证者判断恢复。
5. 每个旧入口退役前查调用方、迁移历史链接、跑新旧等价回归并保留回退版本。当前未盲删旧前端和历史实验。
