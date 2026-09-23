# Section9：唯一演示主线

更新：2026-09-24。实际验收见 [DEMO_ACCEPTANCE.md](DEMO_ACCEPTANCE.md)，接力先读 [HANDOFF.md](HANDOFF.md)。

## 已收敛的产品路径

**自由业务任务 → 应用侧观测 → 选择依据 → EvoMap 协作核查 → 人工审核/退回重做 → 同案单助手对比。**

入口 `/demo`，服务 `http://127.0.0.1:9160`。已接入应用是开源 support-agent，使用隔离的本地订单数据库；评委可以自行输入订单、物流、退款资格、常见问题。请求实际进入它的 LangGraph、模型和业务工具。它不是录制答案，也不是一个无边界通用助手。

业务执行时展示应用实际产生的事件。Section9 规则观察员检查错误、超过 30 秒及转交人工；业务完成并同步 Langfuse 证据后，发现疑点会启动调查，无规则疑点时可人工发起。**持续语义模型监视尚未实现**，没有规则告警不代表没有业务问题。

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
| 对比 | 既有 v1 single/swarm 模式和用量账本 | 同一 frozen evidence、模型、每 run 预算；单助手也有完整事实；历史失败保留 |
| 发布/恢复 | 旧产品工作区已存在 | 保留历史能力，本次未桥接成新主线执行，不新造一套发布引擎 |

CloudThinker Connections、Pulse、Resolve/RCA、协作侧栏、Runbook/审批作为工作流参照；完整对标分母仍是原规划的 36 项功能、83 个连接器与 4 项补充集成。这次只交付上述一条路径，不声明完整平台达成。

## 可演示的协作价值与证据要求

1. 调查员分别读取应用调用和绑定版本的工具/政策源码，不靠同一答案改写角色名。
2. 独立反例步骤不读取调查员输出；后续复核必须同时读到证据、调查和反例，再交给协调员。
3. 每次交接与结果提交需原生会话回读及内容哈希一致；网页消息能追到 native message ID。
4. 暂停调查员后，迟到结果被拒绝，已花用量保留，人工继续让其他成员接管未完成任务。
5. single/swarm 同案完整保留；真实验收两者都识别了政策适用性缺证，single 更快、更省。**尚无蜂群质量胜出的证据**，不要预设演示结论。

本轮闭环到 M2 的“调查建议审核”：不覆盖动态规划、任意读工具循环、自动修复、执行对账、独立恢复验证或多租户权限。原生 PDRI、Hub 经验发布/检索、公共成员发现也不在已验收范围。

## 后续收敛顺序

1. 队友独立换题实跑并盲评同案结果；先定义任务成功/证据完整/错误结论/成本/延迟，再做多案对照，不为蜂群挑题或弱化 single。
2. 将旧发布、执行对账、恢复能力适配到 v1 的 ProposalVersion 和授权边界；先建立旧 ID 到新 ID 的明确关系及幂等迁移，验收前保留旧只读历史。
3. 增加经服务端验证的动态计划、按权限开放的只读工具、工作进程隔离和持久恢复；原生能力优先适配，不并建第三个调度器。
4. 收敛到 Workspace/Application/Environment 的权限域：观测只读、模型提出建议、人工批准具体不可变方案、执行器获得最小一次性授权、独立验证者判断恢复。
5. 每个旧入口退役前查调用方、迁移历史链接、跑新旧等价回归并保留回退版本。当前未盲删旧前端和历史实验。
