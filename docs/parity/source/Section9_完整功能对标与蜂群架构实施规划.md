# Section9 完整功能对标与蜂群架构实施规划

版本：1.0 · 规划基准日：2026-09-23  
仓库：er-s-an/section9 · 分支：main  
审查基线：a803012cdde1fa4fdffc72bcbb7099316455e9ea  
文档性质：架构与实施提案，不是开发完成报告或运行验收证明。

## 阅读说明与结论

本项目的最终目标是完整覆盖 CloudThinker 已公开的产品功能与工作流，并以 EvoMap/GEP 集成的蜂群协作为主要实现方向。分期交付是控制集成风险的方法，不代表将最终范围缩减为一个客服演示工具。短期先交付可独立使用的 Mac 本地产品，随后扩展为受控试用系统，再完成全平台功能对标。

“功能对标”和“效果差异”必须分别验收。一个功能存在，要求它在真实授权数据上能完成对应工作，并正确处理失败；蜂群更有效，要求与强单 Agent、无 GEP/有 GEP 等对照在公平条件下比较。不得以角色数量、消息数量、办公室动画或一个成功样本代替结果。CloudThinker 官方 RCA 文档本身已经描述动态只读 subagent 调查，不能把竞争叙事写成“对方只有单 Agent”。[S12]

本次重新读取 main，仍指向上述完整提交。项目现有可复用资产包括权限与写入事务、租约/fencing、真实模型网关、探针、Pair 隔离、事件和证据导出。当前主要缺口是：真实应用接入与事故生命周期尚未成为产品主线；上轮 R1–R5 风险需要处理；完整冷安装、真实在途强杀恢复、成本场景重复验收仍缺充分证据。[S01][S02][S03]

架构决定如下：采用模块化单体，不立刻拆微服务；引入工作区级业务 authority，将 Incident 与 Run 分离；外部读写全部经过连接器和权限代理；Agent 负责调查与提案，确定性系统负责权限、准入、状态和执行；先打通 Langfuse → 事故 → 代码/配置 → 方案 → 受控执行 → 独立验收，再横向扩展全部功能域。

本文中“应当、必须、建议、新增”均表示拟实施设计。文中出现的新目录、接口、状态或检查命令均不代表仓库已经存在。已读取的公开资料、源码基线、未读取材料和证据限制详见第 24 节。

## 目录

1. 目标、范围与完整对标口径
2. 当前基线与必须先处理的问题
3. 产品主流程与用户工作对象
4. 功能对标矩阵与完成标准
5. 总体架构与现有实现复用
6. 领域模型、存储与迁移
7. 状态机与异常处理
8. 连接器、Langfuse 与代码上下文
9. 信号发现、关联与监护
10. 蜂群协作运行时
11. EvoMap/GEP 集成与真实性边界
12. 权限、审批、租约与执行安全
13. 配置修复、代码补丁与部署
14. 预算、调度、取消与恢复
15. Skills、Runbook、知识与记忆
16. 界面与端到端体验
17. 全平台模块扩展设计
18. API、事件与公共契约
19. 本地发行、团队部署与运维
20. 困难治理场景与公平实验
21. 测试矩阵与交付证据
22. 路线、工作包、依赖和估算
23. 本地 Codex 实施交接
24. 来源、待核实事项与最终验收
附录 A. 连接器完整范围登记
附录 B. 建议冻结的契约样例

## 1. 目标、范围与完整对标口径

### 1.1 产品目标

Section9 应允许使用者连接自己的应用、代码和运维数据，发现异常，组织有证据的调查，审查候选修复，依据权限执行，在实际生效版本上验证结果，并复用经过验证的经验。除事故响应外，最终范围包含代码审查、成本优化、基础设施管理、安全测试、自动化、人机协作、知识与技能、报表、开发者接口和团队治理。

近期运行边界仍是 Mac 上的本地控制面与远程 EvoMap 推理。本地产品不是缩小版假数据 SaaS；它应能接真实数据，但只执行已明确支持且获授权的动作。新增云连接器并不自动授权操作用户账号，连接成功也不自动启动所有付费调查。

### 1.2 三套独立的完成标准

| 标准 | 定义 | 不能替代它的证据 |
| --- | --- | --- |
| 功能可用 | 明确用户任务、入口、输出、权限、失败处理和实际集成测试全部成立 | 页面存在、工具注册成功、提示词声称支持 |
| 产品可交付 | 非开发者能安装、接入、操作、排错、停止、恢复和导出证据 | 原开发者机器上的单次演示 |
| 蜂群效果 | 在冻结条件下，相对强基线改善指定指标，或明确说明无优势 | 多个角色、更多消息、GEP asset ID 或供应商宣传 |

功能对标按“用户可完成的任务”判断，不要求复制品牌、视觉资产或未公开算法。不把更严格的权限约束认定为功能缺失，但每个被限制动作都应提供明确的审批或人工处理路径。SOC 2 等认证、采购渠道、服务等级承诺及商业授权单独登记，它们不是代码实现即可获得的能力。

### 1.3 完整范围如何冻结

建立 `docs/parity/catalog.yaml`，逐项登记：capability_id、功能域、用户任务、官方来源、资料抓取日期、来源成熟度、支持平台、验收场景、实现状态、负责人、版本和证据路径。每条能力的状态只能为 discovered、specified、implemented、verified、blocked 或 not_supported；不能用“已完成页面”跳到 verified。

公开资料存在新旧命名：最新入口强调 Review、Resolve、Optimize、Cyber，官网另出现 Stack、OnCall、Automation；部分页面仍使用 DRE、CostOps 等名称。本规划使用所有公开能力的并集建立范围，合并改名项、保留独立任务，不根据模块数量计算完成率。[S10][S28]

对正文未成功读取、仅目录或官网提及的功能，状态必须是 discovered/待契约核对，而不是删除，也不是宣称其行为已被确认。针对私有功能、实际权限、套餐差异和预览功能，实施前需要授权演示或产品文档补齐验收细节。

### 1.4 近期与远期的关系

B 层目标：受支持 Mac 上的单操作者本地产品，完成真实应用接入及一条可靠事故闭环。C 层目标：增加困难治理场景、可审查代码修复、受控试用和可重复实验。F 层目标：第 4 节与附录 A 的公开功能逐项 verified，并完成相应团队与部署能力。

B/C 通过不等于 F 完成。团队、云端部署、SSO、计费、全部连接器属于最终范围，但不应成为修复眼前预算与误操作问题的前置条件。反过来，近期没做的能力必须留在账本和工作量中，不能靠“暂不做”无限期消失。

## 2. 当前基线与必须先处理的问题

### 2.1 可复用实现与证据等级

| 项目 | 当前可采用的判断 | 规划处理 |
| --- | --- | --- |
| 实际模型与小智故障 | 存在真实请求、配置变更与业务探针 | 包装为示例应用适配器，不删除 |
| 权限与验收绑定 | 上轮新增 VerifyRequest、任务/实例/版本检查 | 复用安全语义，扩大到产品作用域 |
| 重启 usage 结算 | 存在 not_sent/sent/legacy 恢复路径 | 共用账本服务；补真实进程时序测试 |
| 禁言 | 固定 blocked 已移除，允许模型决策或弃权 | 保留；补公平消融和旁路读取测试 |
| Pair 与导出 | 独立 arm、完整历史投影、分页及 ZIP | 保留实验室；修前端尾部窗口问题 |
| 当前交付证据 | 仓库记录组件检查及 4 Pair/8 arm，其中 cost baseline 失败 | 不按测试数签产品；复验固定发行版本 |

上述是代码审查和仓库记录所支持的范围，不证明任意电脑当前正常运行。阶段文档明确承认独立冷安装和真实 Provider 在途强杀尚未完成。[S02][S03]

### 2.2 R1–R5 修复工作包

| ID / 优先级 | 触发条件与影响 | 修复方案 | 必须新增的验收 |
| --- | --- | --- | --- |
| R1 / P0 | 请求预留后排队，run 已失败但 generation 未变，可能继续付费推理 | 发送前原子准入；终态统一取消；记录 dispatch 线性化顺序 | 占槽→排队→终态→释放；后请求不调用 Provider，对侧不变 |
| R2 / P0 | 历史 Pair 选择被四秒轮询 active 指针覆盖，按钮可能操作错误对象 | selectedPairId 与 activePairId 分离；请求捕获目标和版本 | 乱序轮询、多次刷新后仍操作原选择；另一个 Pair 无变化 |
| R3 / P1 | request.completed 的 error/cancelled 被展示为“已回复” | 类型＋结果统一投影，失败筛选同源 | success/error/cancelled/unknown 四种事件逐一检查 |
| R4 / P1 | 最近 200 条替换后旧证据抽屉为空、未读计数不增 | event_id 读取、稳定分页、按 sequence 计数 | 250 条浏览器测试＋超过 10,000 条后端测试 |
| R5 / P1 | 失效 caffeinate PID 被系统复用，停止脚本可能误杀 | 核对进程启动身份、可执行文件和父/目标服务关系 | 不存在、损坏、无关 PID、真实归属四种测试 |

R1 是对发送准入缺失的静态风险判断，R2–R5 来自上轮具体控制流审查。[S04][S05][S06]实现者必须先在本次基线重现或构造最小反例；发现后续提交已经修复时登记关闭证据，不能机械重复修改。模型网关现有准入片段仍可在固定源码中核对。[S04]

### 2.3 成本失败与版本证据

成本场景的失败不能直接解释成“单 Agent 不行”。阶段记录称修复已完成但验收预算预留失败；在取得该次完整时间线前，它是待独立复核的归因。需提取每个 request 的 queued、reservation、dispatch、settlement、purpose、deadline，判断是预算确实不足、保守预留导致不能准入，还是重复检测与验收相互挤占。[S03]

不得用无限加预算、给某个 arm 单独放宽，或弱化验收绕过失败。允许统一调整预算策略，但必须同时更新两侧 PairSpec、合同版本与全部样本身份。

本阶段最后先提交源码，再生成绑定源码/构建哈希的验收证据；证据后提交可以是独立提交。避免要求“证据文件所在提交必须等于被测提交”这种循环要求，也避免只记录 dirty=true 却不保存被测源码内容身份。

## 3. 产品主流程与用户工作对象

### 3.1 首次接入

首次入口为“使用示例应用”和“连接我的系统”。示例应用与真实应用使用同样的产品对象和页面，但拥有不同环境标识、权限及数据根目录。示例故障只能影响示例对象，不能从产品连接器指向真实账号。

连接流程依次为：选择服务与用途；保存受保护凭据引用；校验网络/认证；列出可访问项目或账号；由用户确认应用和环境；读取最小数据样本；展示数据完整性与权限；选择是否运行一次有预算的只读发现；设置监护/自动调查/自动执行三个独立开关。

Connected 仅表示认证与必要读取成立；“没有近期数据”“正在补历史”“权限不足”“数据延迟”“业务未验收”必须分别显示。只读发现若会调用模型，需要明确费用上限，不能藏在保存连接后的默认动作里。

### 3.2 标准事故闭环

| 阶段 | 系统产物 | 人的决定 | 进入下一阶段的条件 |
| --- | --- | --- | --- |
| 接入与范围 | Application、Connection、ScopeBinding | 确认对象及读取/写入权限 | 数据样本可追溯；范围明确 |
| 监护与基线 | MonitorPolicy、Baseline、Coverage | 是否启用扫描、付费调查 | 规则合法；样本不足有明确状态 |
| 发现与归并 | Signal、Cluster、Incident | 忽略、合并、拆分、认领 | 有证据；无重复启动 |
| 调查与反证 | EvidenceSet、Hypotheses、InvestigationResult | 补充信息或继续 | 结论有依据，或明确无法确认 |
| 方案与审批 | ProposalVersion、ValidationPlan | 批准、拒绝、要求修改 | 审批对象完整且未变化 |
| 执行 | ExecutionAttempt、ActionReceipt | 停止、接管、人工处理 | 实际外部状态已确认 |
| 验证与观察 | ValidationReport、ObservationWindow | 继续处理或结案 | 恢复且无新增损害，数据覆盖充分 |
| 复盘与复用 | IncidentSummary、LessonCandidate | 接受/修订/撤销经验 | 验证产物通过经验发布门禁 |

CloudThinker 的 Resolve 官方说明支持信号关联、调查、受限动作和后续经验，并明确不是每件事故都能找到根因或完成修复。本设计采用这类完整工作结构，但自行落实更严格的权限与验收条件。[S11][S13]

### 3.3 失败后的工作仍须继续

一次调查未找到根因，应结束该 InvestigationRun，保持 Incident 为 needs_input 或 open。方案被拒绝只改变该方案决定，不能删除调查证据。后续调查创建新 run，复用已接受证据并记录新时间窗；旧 task、lease、grant 永远不复活。

执行失败不等于可以立即重试。先判断动作是“确定未发生”“确定已发生”“结果未知”。未知时进入 reconciling，通过读回或人工核实确定真实状态，再决定是否补偿。对不可逆变更，产品必须允许以未恢复/风险接受方式归档，但不能显示业务恢复。

### 3.4 工作入口与日常待办

默认首页按需要用户注意的事项组织：等待输入、等待审批、高优先级未解决、自动处理中、数据源异常、最近完成。办公室位于具体事故或运行的协作页，用于解释谁在调查什么、依赖谁、为何阻塞，不替代事故工作区。

聊天可以创建或继续工作，但必须链接到具体 WorkItem；浏览器、CLI、ChatOps 发起同一种操作时都经过相同服务与权限入口。用户关闭聊天窗口不自动取消事故；取消必须是对明确对象的操作。

## 4. 功能对标矩阵与完成标准

### 4.1 公共底座与日常工作

表中里程碑 M0–M5 在第 22 节定义。所有条目均属于最终目标；“后期”不是范围删除。当前状态按基线只分“局部可复用”和“新增”，不把已有实验实现直接等同完整产品功能。

| ID | 完整功能目标 | 主要交付和验证口径 | 阶段 |
| --- | --- | --- | --- |
| CAP-01 | 工作总览、待办、跨模块活动搜索 | 以真实状态生成待办；按权限检索历史，不遗漏失败 | M1/M4 |
| CAP-02 | 应用/环境/工作区 | 明确隔离凭据、知识、工作项、预算和外部对象 | M1/M5 |
| CAP-03 | 连接目录与验证 | 新建、测试、发现、更新、撤销、失效恢复 | M1/M4/M5 |
| CAP-04 | 资源清单与拓扑 | 资源身份、关系、所属服务、来源与新鲜度 | M1/M4 |
| CAP-05 | 聊天与多 Agent 工作侧栏 | 附件、引用回复、排队追问、分支、停止、历史 | M2/M4 |
| CAP-06 | 自定义 Agent 与能力 | 配置版本、能力目录、工具范围、任务证据 | M2/M4 |
| CAP-07 | 通用 MCP 接入及对外 MCP | 客户端和服务端分别鉴权；工具白名单与配额 | M1/M4 |
| CAP-08 | Skills / 命令 / 知识库 | 创建、导入、启停、场景绑定、版本、检索与撤销 | M2/M4 |
| CAP-09 | Runbook 与执行历史 | 具体步骤、允许/审批/拒绝、验证和回滚 | M2/M3 |
| CAP-10 | Workspace/Incident/Review 记忆 | 来源、作用域、过期、纠正、删除、复用记录 | M2/M4 |
| CAP-11 | 报表、图表、仪表盘、表格分析 | 数据查询可复查、筛选、导出、结果版本 | M4 |
| CAP-12 | 幻灯片、图像生成/编辑及参考图 | 独立产物服务；费用、权限、版本、来源 | M4/M5 |
| CAP-13 | 分享与撤销 | 显式发布、脱敏、访问范围、到期和撤销 | M4/M5 |
| CAP-14 | 通知与 ChatOps | 应用内、邮件、Slack、Teams、Google Chat；去重 | M3/M4 |
| CAP-15 | API、CLI、headless 与 webhook | 同一工作对象、幂等、状态查询、退出码、重放防护 | M3/M4 |
| CAP-16 | 组织、成员、RBAC、SSO/MFA/SCIM | 服务端授权、即时撤权、审计和迁移 | M5 |
| CAP-17 | 使用量、额度、计费、BYOK | 可核对账本、月额度、提醒、限流、退款与套餐 | M4/M5 |
| CAP-18 | 本地、专属云、自托管与隔离部署 | 分部署配置验收，不混称离线推理 | M0/M5 |

功能范围依据官方连接目录、能力页面、Skills、组织、安全、API 与部署资料；本文每个验收细节属于 Section9 的拟定设计，不是竞品行为的逐字复述。[S14][S17][S18][S19][S20][S21][S22]

### 4.2 业务模块

| ID | 功能域 | 完整任务集合 | 阶段 |
| --- | --- | --- | --- |
| RES-01 | Pulse/信号 | webhook/poll/manual/chat 输入、去重、抑制、归并、路由、数据源健康 | M1/M4 |
| RES-02 | RCA | 多假设、并行调查、反证、因果图、证据、影响服务、时间线 | M2 |
| RES-03 | 事故管理 | 认领、严重级别、合并/拆分、等待输入、再调查、结案及人工判定 | M1/M2 |
| RES-04 | 处置 | Runbook 匹配、方案审批、执行、验收、复查、经验 | M2/M3 |
| RES-05 | 洞察 | 漏检/噪声、调查质量、耗时、覆盖、人工评分、失败与成本 | M3/M4 |
| REV-01 | PR/MR 审查 | 多 Git 平台、diff/上下文、精确评论、严重度、重新审查、修复追踪 | M3/M4 |
| REV-02 | 自动修复与冲突 | 补丁、后续 PR、冲突解决候选、检查状态、审批；不默认合并 | M3/M4 |
| REV-03 | 发布与流水线 | CI 失败分析、发布风险报告、Approve/Hold、版本绑定 | M4 |
| REV-04 | 约定与洞察 | 仓库规则、工单验收标准、学习候选、覆盖与人类判定指标 | M4 |
| OPT-01 | 成本分析 | 多账号/服务/标签、周期/币种、预测、异常与数据截止点 | M4 |
| OPT-02 | 优化建议 | 闲置、规格、承诺折扣、存储、模型路由等有证据建议 | M4/M5 |
| OPT-03 | 执行与收益 | 去重节省估计、风险、批准变更、实际收益、性能守护 | M4/M5 |
| INF-01 | 基础设施 | 多云、Kubernetes、数据库、配置及资源关系的发现与诊断 | M4/M5 |
| INF-02 | Stack/变更计划 | IaC 计划、状态锁、审批、应用、漂移、回滚与验证 | M4/M5 |
| SEC-01 | Cyber/安全评估 | 授权目标、认证资料、深度/范围、计划或手动测试 | M4/M5 |
| SEC-02 | 安全发现 | 可复查证明、去重、影响、误报、整改、复测、导出 | M4/M5 |
| AUTO-01 | 自动化 | 定时、webhook、仓库事件、运行历史、停用、重试与预算 | M3/M4 |
| TEAM-01 | OnCall/协作房间 | 事故房间、人/Agent、交接、提及、升级与来源集成 | M4/M5 |

RCA、Runbook、Review、Optimize 的公开流程已有正文依据；Stack/OnCall 的部分细节只在官网或旧导航出现，必须再核实其完整契约。不得把“官网提到”写成“已亲测完整成熟功能”。[S11][S12][S13][S15][S16][S28][S29]

### 4.3 不允许的“假对标”

通用 MCP 可以扩展工具，但不能自动让所有连接器 verified。每个服务仍需验证鉴权、分页、权限、错误、数据标准化和实际动作。对接一个 GitHub 工具也不等于 Review 完成；生成补丁也不等于部署和业务恢复。使用量面板不等于云 FinOps；文档上传不等于知识检索；多个角色不等于并行调查；一个 active 开关不等于可靠的长期自动化。

每条能力至少拥有一个正常任务、一个权限负例、一个依赖失败、一个恢复/重试场景，以及与固定版本绑定的证据。重复的公共安全测试可以复用，但业务输出测试不可省略。

## 5. 总体架构与现有实现复用

### 5.1 架构选择

采用“模块化单体控制面＋受限 Worker＋独立执行代理”。Mac 本地版本保留 Python/FastAPI、React/TypeScript、SQLite 和现有远程模型客户端的方向。不为完整对标立即引入 Kubernetes、Kafka、图数据库或多套编排框架。

控制面分为产品业务服务、确定性 authority、连接器代理、运行时、证据与投影五层。所有模块共享同一套权限、预算和审计，不允许 Review、Cyber 或 CLI 绕开主执行门禁。第三方 API 的访问由服务端适配器承担；Agent 获得的是有范围的工具能力，不是原始密钥和不受限网络。

```text
浏览器 / CLI / ChatOps / webhook
                 |
       产品 API、身份、工作区作用域
                 |
  应用与连接 / 信号与事故 / Review / Optimize / Cyber
                 |
     Workflow Service + Durable Jobs + Outbox
                 |
  Authority：策略、审批、预算、lease、fence、版本
       |                   |                   |
  Evidence Broker     Swarm Runtime       Executor Broker
       |                   |                   |
 Langfuse/Git/云API     受限推理Worker       受限变更/补丁/部署
       |                   |                   |
   证据快照       EvoMap推理 / GEP适配器    独立验收与读回
                 |
       事件 / 投影 / 审计 / 完整证据导出
```

### 5.2 工作区 authority 与资源隔离

新的产品模式使用一个工作区 SQLite 数据库，包含该工作区的业务对象、run、task、grant、预算、资源锁、审计和 outbox。所有影响授权和状态的关键判断必须能在同一个本地事务内完成。不要将 Incident 放一份数据库、权限放另一份数据库，再把两次检查描述为原子授权。

实验室现有 primary 和 Pair arm 数据库保留。通过 LegacyLabAdapter 映射到只读历史与示例入口，不批量改写过去的 run_id 或验收结果。实验室隔离的对象是复制出的环境；产品多个 run 则可能治理同一个外部对象，必须共享该对象的资源锁与 fencing。不能直接复用“每 run 独立配置数据库”来宣称真实应用也不存在写冲突。

每个外部资源使用规范标识：provider/account-or-project/region/resource_type/resource_id/environment。所有产品任务对同一资源的写入都经工作区级 ResourceAuthority，锁按资源集合排序获取。初版一个资源最多一个变更执行者；并行读取不持写锁。资源跨工作区重复接入时，首版禁止双写绑定，或强制统一执行代理；不能只靠不同数据库中的锁解决跨工作区竞争。本地 ResourceOwnershipRegistry 使用全服务唯一的规范资源键和事务唯一约束登记写绑定；若部署多个控制面实例，必须共享该注册/执行 authority，不能各主机独立判定无人占用。

### 5.3 非阻塞控制面

现有较多 SQLite 操作是同步调用；新增模块不应在事件循环中执行长时间全历史扫描、ZIP 压缩、Git 命令或逐个进程等待。采用单写入队列/专用线程或明确的异步数据库边界，事务内只做有限本地读写。模型、连接器和文件执行均在提交之后开始。

投影采用增量 checkpoint，不在每次 UI 轮询时重新读取全部事件。完整导出由固定水位的后台作业生成，作业本身不调用模型；“后台”指未来本地产品实现，而非本次规划会在对话之外执行。

### 5.4 现有模块的复用与调整

| 已有模块 | 保留内容 | 必须调整的边界 |
| --- | --- | --- |
| `s9/store.py` | grant、哈希绑定、版本、lease、fence 语义 | 从全局 current_run/meta 转为显式作用域；提取 authority 接口 |
| `s9/model.py`、`model_scheduler.py` | 请求、预算、取消、共享准入 | 发送前复核、严格 usage 状态、层级预算、请求身份 |
| `s9/core.py` | 本地生命周期与已有闭环 | 避免同时负责产品 CRUD、连接器、策略和全部协作 |
| `s9/workers.py` | 受限客户端、任务认领、结构化产物 | 多步工具循环、动态任务、独立复核与能力声明 |
| `s9/victim.py`、`assets/xiaozhi` | 示例业务与验收用例 | 实现 WorkloadAdapter；不得作为所有应用的固定真值 |
| `s9/pairs/*` | 实验冻结、隔离、对照、历史 | 仅用于实验域；复用证据格式，不成为产品权限权威 |
| `frontend/src/*` | 页面样式、办公室、证据抽屉 | 产品路由、稳定选择、状态映射、分页数据契约 |
| `integrations/gep/*` | SDK 资产工具 | 增加版本锁定、Hub/session 适配、验证与撤销 |
| `scripts/*` | 安装、体检、启停及历史验收 | 聚合发行入口、命令分类与已知副作用 |

### 5.5 建议目录，不是当前目录事实

```text
s9/product/       applications, connections, work_items, incidents
s9/authority/     policies, approvals, resources, dispatch, verification
s9/runtime/       jobs, task_graph, agents, context, model_gateway
s9/connectors/    base, registry, langfuse, github, mcp, cloud_families
s9/evidence/      objects, snapshots, ingestion, redaction, exports
s9/execution/     config_adapter, patch_runner, deploy, reconcile
s9/modules/       resolve, review, optimize, infrastructure, cyber
s9/integrations/  chatops, notifications, evomap
s9/storage/       workspace_store, repositories, migrations, outbox
frontend/src/features/  overview, apps, incidents, review, optimize, lab
```

迁移按薄切片逐步完成。目录拆分不是独立里程碑；每次拆分必须有原接口回归。禁止为“代码整洁”一次移动所有文件，导致来源哈希和行为同时大幅改变。

## 6. 领域模型、存储与迁移

### 6.1 核心对象与职责

| 对象 | 关键字段 | 职责与不变量 |
| --- | --- | --- |
| Workspace | id、policy_revision、budget、deployment_mode | 数据与权限边界；本地首版自动创建，不强制注册 |
| Application | id、name、owner、environments、bindings | 被治理对象，不是 Agent 进程 |
| Connection | type、endpoint、credential_ref、capabilities、health | 只保存密钥引用；范围变化有版本 |
| Resource | canonical_id、snapshot_hash、observed_at、provenance | 真实外部对象及可验证状态 |
| WorkItem | id、kind、workspace、application、status | 跨模块索引，kind 指向独立业务记录 |
| Incident | id、scope、severity、signals、revision、outcome | 长期业务问题；一次失败不删除事故 |
| InvestigationRun | id、incident_id、input_snapshot、budget、outcome | 一次有界调查；终态不可重新激活 |
| Task | id、run、capabilities、dependencies、epoch、holder | 可接管的有界子任务；能力和依赖被服务器验证 |
| Evidence | id、origin、source_version、hash、classification | 不可变原始/脱敏快照，保留出处和覆盖限制 |
| Hypothesis | id、support、counterevidence、status | 可推翻解释，不作为权限凭证 |
| ProposalVersion | id、hash、targets、actions、risk、validation | 每版不可变；改动导致旧审批失效 |
| Approval | actor、proposal_hash、scope、policy_rev、expires | 具体授权决定；不是通用“同意自动执行” |
| ExecutionAttempt | id、proposal、grant、resource_versions、outcome | 每次实际动作及读回；未知状态不自动重发 |
| ValidationReport | artifact/config/hash、contract、evidence、checks | 验证实际运行对象；测试定义与修复者隔离 |
| LessonCandidate | provenance、compatibility、validation、state | 候选经验，不自动变为可执行规则 |
| Automation | trigger、scope、principal、policy、revision | 每次触发生成新 run；变更策略后不继承旧授权 |

ReviewFinding、OptimizationFinding、SecurityFinding 作为 WorkItem 的独立子类型，各自保留业务专属字段。不要将所有业务都塞入 Incident 的 status 字段；公共层只管理链接、权限、任务、执行和事件。

### 6.2 ID 与关联

统一采用无语义随机 ID，避免从路径或 scenario 名称泄露实验答案。RunRecord 的 `incident_id` 在产品模式指向真实 Incident；实验历史的旧映射保留为 legacy 属性。API 明确 `mode=product|lab`，不得按空值猜测。

所有对象都带 workspace_id；属于应用的对象还带 application_id/environment。任务和模型请求带 run_id；Pair 仅在实验模式增加 pair_id/arm/spec_hash。服务端从认证上下文解析 workspace 与能力，再核对请求对象，不能仅信任请求体传来的 scope。

### 6.3 证据存储

小型结构化记录在 SQLite；原始 trace、日志、diff、测试输出和导出文件使用本地受保护的内容寻址存储。每个 Evidence 同时保存 source identity、获取时间、源事件时间、查询范围、分页覆盖、脱敏版本和内容哈希。

原始内容与模型可见内容分开：raw_blob_ref 默认只允许操作者/审计角色；sanitized_blob_ref 才可进入 Agent。哈希证明内容一致，不证明事实真实；外部来源也可能错误。证据来源改变不覆盖旧记录，而是新版本引用。

### 6.4 关键数据库约束

必须有对象复合外键或等效事务校验、非负预算约束、scope 下的幂等唯一键、active job 竞争保护、资源锁 epoch 和 outbox unique event_id。对 SQLite 实际启用外键检查，不能只在 ORM 模型中声明。

建议核心表：applications、connections、resource_bindings、resources、monitor_policies、source_cursors、signals、clusters、incidents、investigation_runs、tasks、messages、hypotheses、evidence_refs、proposals、approvals、grants、executions、validation_jobs、validation_reports、usage_reservations、resource_leases、events、outbox、projection_checkpoints、automations、lessons。

初版不为每个 Agent 增加独立向量库；使用明确 scope 的事实检索和小型内容索引。全文检索可以先采用 SQLite FTS；向量检索是后续性能/质量实验驱动的可替换实现，不作为第一条闭环的硬依赖。

### 6.5 迁移顺序

先新增工作区 schema 和只读 legacy 适配；再让新应用/事故进入产品路径；随后迁移共享 authority 服务；最后将示例应用通过同一产品路径运行。历史 Pair、source identity、原始事件和失败结果保持不变。

迁移前停止本项目写入，使用数据库备份接口形成一致快照，记录 schema 版本、文件哈希和凭据备份方式。迁移必须支持从备份回退；不要求旧二进制直接读取新 schema。升级失败时禁止自动生成新密钥覆盖旧数据库凭据。

## 7. 状态机与异常处理

### 7.1 不用一个状态覆盖所有层级

业务事故、调查运行、模型请求、审批和部署状态必须分开。Incident 可以仍 open，而 InvestigationRun 已 failed；Proposal 可以 approved，而 Deployment 仍 pending；ModelRequest 可以 succeeded，但业务验收 failed。界面和统计必须体现这种差异。

### 7.2 Incident 状态机

| 状态 | 进入条件 | 可执行操作 | 离开条件 |
| --- | --- | --- | --- |
| open | 有效信号或人工建单 | 认领、合并、调查、误报处理 | 进入调查或明确关闭 |
| investigating | 新调查 run 已准入 | 查看、补充事实、停止本次调查 | 结论、阻塞或运行失败 |
| needs_input | 缺权限、证据或人工判断 | 补充输入、授权读取、再调查 | 新输入形成新快照与新 run |
| awaiting_approval | 可审查方案已冻结 | 批准、拒绝、要求新版本 | 对确切版本的决定 |
| remediating | 写授权已获得且执行开始 | 查看、受控停止 | 读回成功、失败或未知 |
| validating | 有明确被测版本 | 独立验证、停止 | checks 完整结束 |
| observing | 即时验证通过，等待稳定窗口 | 看恢复趋势、补充异常 | 窗口覆盖满足且无退化 |
| resolved | 满足业务恢复及审计合同 | 导出、复盘；新信号可重新打开 | 新的事故修订，不复活旧 run |
| dismissed | 明确误报/重复/不适用 | 查看理由、重新打开 | 有新证据且有权限 |

失败作为尝试结果和阻塞原因保存，不把每次失败都变成不可恢复的事故终态。用户决定暂不修复时，outcome=accepted_risk 或 archived_unresolved，界面必须显示未恢复，与 resolved 分开。

### 7.3 Run、Proposal 与 Execution

InvestigationRun：queued → running → succeeded / inconclusive / abstained / failed / cancelled / interrupted。succeeded 仅表示调查交付物完成，不表示业务恢复。proposal_ready、root_cause_identified、needs_data 等作为结果类型。

ProposalVersion：draft → under_review → ready_for_approval → approved / rejected / superseded / expired。审批期间不持有长时间任务租约，不每 0.6 秒请求 grant；人工批准后新建 execution run，重新获得 task lease 并检查资源和版本。发生变化则回到新方案/重新审批，不暗中更新旧方案。

ExecutionAttempt：queued → authorized → dispatch_committed → running → applied / not_applied / outcome_unknown；outcome_unknown → reconciling → applied / not_applied / manual_required。applied 之后仍需独立 validation，不能直接结案。

### 7.4 状态转换与并发

所有写 API 携带 expected_object_revision 或 If-Match，并要求 scope 下的 Idempotency-Key。竞争操作返回明确冲突，不用后到请求覆盖前者。服务器产出的状态和事件在一个事务中提交；UI 只能提交意图。

并发“批准/取消”“执行/接管”“验收/配置更新”必须以 authority 提交顺序决定。测试覆盖两种顺序。对于数据库提交与外部网络发出之间的缝隙，不声称跨系统原子性，使用 dispatch intent 和后续读回记录不确定性。

### 7.5 暂停、取消和重启

暂停监护：不再收取新数据，但已有历史保留。关闭自动调查：继续发现和建单，不自动启动付费 RCA；重新打开默认不追跑积压事故。取消调查：停止该 run，事故仍可继续。停止应用治理：拒绝该应用新任务并取消可取消工作，但不删除连接和历史。

产品不提供含混的“reset 全部”。示例重置、Pair 单侧重置、取消调查、撤销连接、清空本地数据分别实现和命名。清空数据属于破坏性维护流程，要求备份与单独确认。Mac 休眠/重启后的任务进入 interrupted，新请求使用新 boot epoch；旧租约不得继续有效。

## 8. 连接器、Langfuse 与代码上下文

### 8.1 连接器统一契约

每个连接器声明稳定名称、实现版本、认证方式、资源范围、数据能力、支持操作及 effect classification。至少实现 validate、discover、query、health、redact、revoke-local-access；支持写入的连接器另实现 prepare、execute、reconcile、validate-effect、compensate（如适用）。

“读”按语义定义，不按 HTTP GET/POST 判断。数据库函数、导出敏感内容、SSH 命令和 custom MCP 的自报 readOnly 都不能自动被视为安全。工具 schema 或描述升级后，原白名单不得自动扩大；新增工具默认拒绝直至重新审查。

查询结果统一返回：items、source_cursor、coverage、observed_at、source_version、warnings、rate_limit、next_page、evidence_refs。空结果必须区分已完整查询为空、权限不允许、数据源失联、时间窗不含数据和分页尚未完成。

### 8.2 凭据与网络边界

Mac 默认用受保护凭据存储保存 secret，仅在数据库保存 credential_ref 和 revision。需要导出/跨机器恢复时，提供单独的加密凭据备份流程；普通证据包不含凭据。密码、secret、token 不进入 Worker 环境、模型上下文或普通日志。运行外部工具时尽量由 broker 注入短期授权，不把完整云管理员凭据交给任意命令。

连接器 endpoint 在保存和每次重定向时校验协议、目标主机与已批准网络范围。云 metadata/link-local、未批准 localhost/私网默认禁止；本地 Langfuse 作为显式本地例外，只允许确切服务地址。域名重解析、重定向和 URL 内嵌凭据都要检查。TLS 错误不静默关闭证书校验。

### 8.3 Langfuse 的两个角色必须分离

第一类连接为 `observed_application`：读取用户应用的 traces、observations、scores、prompts、会话和评测资料，用于诊断。第二类为 `section9_telemetry`：记录 Section9 自身模型、任务和工具运行。两者用不同项目/密钥/逻辑身份，检测规则默认排除 Section9 自身流量，防止“监控自己的调查调用又触发调查”的循环。

官方 Langfuse data MCP 是鉴权数据服务，公开 docs MCP 只是文档知识服务；两者不能混用。data MCP 默认有读写工具，需要本地服务端白名单限制。项目级 API key 的范围不等于该 key 天生只读。[S23]

### 8.4 Langfuse 初始完整读取能力

必须支持按时间、环境、服务、trace/session、提示词版本和模型筛选；读取 trace 根对象与子 observations；检查错误/延迟/usage；读取评分、提示词版本/标签、数据集及相关注释（以已连接版本实际暴露能力为准）。没有某项 API 时能力标为 unavailable，不伪造空集合。

实现优先使用固定版本官方 REST/SDK 适配器，以便控制分页、额度和响应 schema；受限 MCP 可作为同等工具入口。官方 Agent Skill 作为按需加载的方法资料，固定来源和版本；不能因为 Skill 示例使用 CLI 就放开现有 Worker 的任意 bash 或凭据权限。[S24][S25][S26]

观测与聚合必须避免 trace 总用量和子 generation 用量双计。选择 canonical accounting level，保留源字段。新增/延迟的 observations 和 scores 使用重叠时间窗与基于 source_id/version 的幂等更新；不能只读取一次 trace 就永久认为它已完成。

### 8.5 代码与部署版本关联

新增 RepositoryBinding：provider、repository_id、允许分支/路径、权限、连接引用；新增 DeploymentBinding：application/environment、commit_sha、build/artifact_digest、config_revision、observed_at 和来源。Langfuse 的 release/version/metadata 字段只作为待核对线索，不直接断言它就是实际运行源码。

调查必须区分“异常发生时版本”“当前部署版本”和“仓库 HEAD”。找不到部署映射时允许只读诊断，禁止以最新 HEAD 生成所谓针对运行版本的确定补丁。用户可以手动确认映射，但要保存确认来源和时间。

### 8.6 连接器最终覆盖与验收等级

附录 A 登记公开连接目录，分为云平台、基础设施、数据库、队列、观测、代码/交付、工单和 ChatOps。先做 Langfuse、GitHub、通用 webhook 与受限 MCP，随后按功能依赖推进其余服务。每个平台必须通过 C0 文档契约、C1 认证与只读、C2 数据归一、C3 获准写入、C4 恢复与验证；不支持写入的平台以其公开只读能力为对标范围。

OAuth 回调、token 刷新、分页、删除/撤权、429、部分权限和断网分别验收。连接器临时失效只停依赖它的工作，不使所有无关应用停止；通知按每次故障去重，恢复后有记录。

## 9. 信号发现、关联与监护

### 9.1 输入与确定性检测

支持手动指定 trace/问题、连接器定时拉取、签名 webhook 和已映射的聊天报告。信号检测优先使用确定性阈值、业务探针、错误码、进度和覆盖数据；保持“哨兵不用 LLM 判定权限或制造异常”的约束。

语义业务正确性若源系统已有评分或独立评测，可读取其结果并保存评分器版本；没有评分时，不应把关键词差异当作正确性证明。需要付费复测时单独提交获准的验证任务，不混入无成本健康检查。

### 9.2 数据窗口与基线

每个 MonitorPolicy 冻结应用、环境、metric、aggregation、comparison_window、minimum_samples、threshold、allowed_lateness、cooldown、max_incidents 和 alert_destination。所有时间以 UTC 保存，显示使用工作区时区，不根据当前机器时区重写历史。

按同一分组比较，避免新流量结构使全局均值产生伪异常。没有足够样本标为 baseline_insufficient；缺一部分分页标为 partial；数据源停止更新标为 source_stale；都不能显示“健康”。初始阈值和最少样本量是策略参数，必须用该应用的验证数据校准，不把任意经验值写成行业标准。

### 9.3 去重与关联

第一层 source 去重使用 source_id/version；第二层 signal 去重使用 rule_version＋application＋environment＋resource＋time_bucket；第三层按影响资源、时间与已确认关系形成 Cluster。LLM 可提出关联建议，但合并/拆分经过确定性范围校验并保留原始信号。

无因果证据时只写“相关”，不能把同时发生标成原因。跨环境或跨工作区信号不自动合并；共享基础设施可用明确的 dependency edge 关联。合并保留 child references，拆分不会丢失已批准的处置决定。

### 9.4 调查预算与噪声控制

自动调查开关独立于监护和执行模式。相同 Cluster 在 cooldown 内只更新证据，不反复开多支蜂群。异常级别提升、影响范围显著扩大或已解决后复发时可新建调查修订，仍受应用/工作区频率和预算上限约束。

低余额或连接失效时，事故仍可记录，调查进入 awaiting_budget/awaiting_connection。恢复后是否补跑由显式策略决定，默认不瞬间启动全部积压工作。

## 10. 蜂群协作运行时

### 10.1 协作要解决的具体问题

蜂群必须能将有独立证据来源的子问题并行调查，记录相互反驳的解释，在局部失败时交接已验证信息，并通过独立业务约束减少误修。若一个小问题只需一次读取和一次决策，允许运行时选择单 investigator；不是每个请求都必须启动完整团队。

任务分解由模型提出，服务器验证 DAG 无环、任务范围、允许工具、预算和角色能力。确定性调度器按可用能力、优先级和预算匹配，Agent 自主认领。GEP 经验和声望可影响候选排序，但不能扩大资源权限。

### 10.2 建议角色与职责

| 角色/能力 | 工作 | 禁止事项 |
| --- | --- | --- |
| Coordinator | 提出任务图、识别缺口、汇总结论 | 不直接执行外部写入，不以置信度签权 |
| Evidence investigator | Langfuse/日志/代码/拓扑等专项调查 | 不读取隐藏故障标签、验收答案或对侧结论 |
| Challenger | 独立形成约束与反例，再审查候选根因/方案 | 不只改写修复者结论，不拥有生产写权限 |
| Repair proposer | 形成配置或代码变更、测试与回滚方案 | 不编辑验收合同、安全策略或预算规则 |
| Executor | 运行已批准的类型化动作 | 不自主扩展命令和目标，不接受任意模型文本 |
| Validator | 独立检查真实行为、版本和无新增损害 | 不接受修复者给出的 passed，不帮其修改测试 |

Executor/Validator 的关键权限与结果判定属于基础设施；是否调用 LLM 要单独标记，不能把所有基础设施进程都计为“推理 Agent”。首版建议最多四个同时活跃推理任务；扩大上限必须受工作区/供应商并发和预算实验支持，此数字是初始设计参数，不是普遍最优值。

### 10.3 独立复核的具体机制

Challenger 先只读取与其他 Agent 同权限的原始业务资料，提交不可变的初步约束/风险清单，再查看修复方案。这样减少直接被修复者措辞锚定，但不声称完全统计独立。它必须指出支持或反对方案的 evidence_id 和具体失败条件。

分歧进入 DisagreementRecord：争议命题、各方证据、待验证条件、负责人和截止。通过补证或测试处理，不用多数投票替代事实。如果无法解决且影响高风险动作，停止自动执行，要求人工决定或保守不变更。

### 10.4 消息、上下文与任务产物

消息至少带 workspace、run、task、sender_instance、task_epoch、transport_epoch、kind、evidence_refs、artifact_refs、created_at。所有引述必须能解析到当前获准证据；未知 ID 和跨 scope ID 被拒绝。内容大小有上限，实际消息 token 计入预算。

上下文按最小必要事实构建，原始证据与同伴推断分层显示。LLM 可以申请新的受限读取，但不直接读取全量事件库、操作者日志、数据库或任意本地文件。代码、日志、Runbook、外部 Hub 资产中的指令均是待分析内容，不能改变控制面规则。

### 10.5 接管与禁言

只读调查任务失败后，备用 Agent 可在新 epoch 下接管，收到已接受产物与未完成问题，不继承旧授权。正在写入的任务失联时先 reconciliation，不允许备用 Agent 重放未知副作用。根服务重启使旧 run 中断，新的继续处理使用新 run；历史不删除。

禁言实验只移除消息与共享结论交付，不禁止原有工具调用或直接返回预设失败。公共原始事实仍可由各 Agent 按相同权限获取；单 Agent 获得所有同等原始事实。严格通信消融应从一轮开始冻结，运行中 mute/unmute 属于干预样本，不能与正常性能样本混算。

### 10.6 多步能力与停止规则

Worker 从“固定一次模型输出”扩展为有界工具循环：observe → propose next read → broker validate → execute read → accumulate evidence → propose conclusion。每步都有工具/模型额度，最多步数、无进展检测、重复查询去重和软停止边界。

时间、预算或证据不足时输出 inconclusive/abstained；不为了给出完整答案而编造缺失数据。复杂度提升前先让 strong single 使用同一循环、工具与预算，避免蜂群获得额外能力而基线仍被锁在一次决策。

## 11. EvoMap/GEP 集成与真实性边界

### 11.1 核心定位与四层集成

EvoMap 是本项目最终蜂群协作与经验交换方向，不应在长期架构中退化成一个模型 URL。与此同时，远程推理、GEP 资产、协作协议与经验 Hub 是不同能力，必须分别建立连接状态、权限和验收证据。已有本地 GEP 桥接不能证明其他层已可用。[S09][S27][S30]

| 层 | 目标能力 | 当前基线与下一步 | 完成证据 |
| --- | --- | --- | --- |
| E0 推理 | 有预算的远程模型调用、实际模型身份、取消与 usage | 当前有实现；先处理 R1 与成本预留问题 | 真请求及失败记录，实际返回身份，账本对账 |
| E1 GEP 资产 | Gene/Capsule/EvolutionEvent 格式、内容身份和本地复用 | 当前有官方 SDK 桥接；扩展到新领域对象 | 固定 SDK 版本、schema 校验、成功/失败发布门禁 |
| E2 蜂群协作 | 能力发现、会话、任务交接、消息/事件、成员变化 | 官方公开资料有协作描述，具体账号/API 可用性尚未在本次验证 | 两个独立节点真实交换与接管，协议原始记录 |
| E3 经验网络 | 范围内查询、候选引入、审核发布、来源与撤销 | 当前 Hub 发布/远端检索未实现 | 真正远端读取/发布回执、权限失败、撤销/隔离记录 |

M1 必须完成 E2/E3 的小型接入验证，早于大规模 Worker 重构。核对官方接口、版本、鉴权方式、配额、事件语义和测试环境。文档中的能力、开发者 OAuth 与节点身份不得由模型推理密钥替代。不能将“对方公开描述支持”写成“本项目账号已经获得权限”。

### 11.2 适配器契约与本地 authority

建议新增 `SwarmTransport` 内部接口：capabilities、open_session、publish_capability、submit_task、claim_task、send_message、subscribe_events、complete_task、close_session。它是 Section9 待冻结的适配器契约，不是声称这些就是上游 API 名称。

上游任务认领可以指导协作，但真实写入仍要求本地 authority 签发且检查 lease/fence。上游心跳正常不等于执行授权有效；上游旧任务消息到达不能重新打开本地终态 run。每条外部消息同时保留 upstream_id、收到时间、去重键及本地 scope 映射，未映射消息进入隔离队列。

对暂时不可用的上游原生能力，可以保留 `transport=section9_local` 完成本地产品开发；界面和实验 manifest 必须明确标注。该回退只解除本地开发阻塞，不计为 E2/E3 完整对标通过。最终目标中，真实 EvoMap 协作路径仍有独立验收门槛。

### 11.3 原生能力验证清单

第一次接入只做授权范围内的合成任务。验证节点注册/身份续期，工作区或会话作用域，消息有序性和重复交付，断线游标恢复，任务认领冲突，成员失效后的接管，以及关闭会话后迟到结果的处理。验证客户端凭据轮换时旧凭据确实失效。

本次未完整取得所有协作接口的正文和真实账号运行记录，因此接口名、可用套餐、是否需要额外授权、是否具备与本地 lease 等价的语义，均由这一接入验证确定。不能预先绑定未确认 SDK，也不能根据营销描述实现一个同名本地替身后宣称官方接通。

### 11.4 经验导入与发布

外部经验先作为不可信候选进入 quarantine，经过来源、格式、适用版本、字段/动作白名单、敏感内容和本地验证后才能被使用。资产 ID 验证的是内容身份，不保证方法正确。声望、GDI 或其他平台评分只可影响检索排序，不改变权限、预算或验收门禁。

发布默认关闭。发布前展示将离开本地的内容，禁止自动包含完整用户 trace、源码秘密、账号标识或个人信息。允许发布合成、脱敏或经用户批准的经验摘要；发布仍需独立于修复审批的分享授权。失败、撤销、过期及源版本变化都应使本地候选失效。

### 11.5 如何证明 EvoMap 的贡献

至少分别比较：同一 Worker 算法采用本地协作传输和 EvoMap 协作传输；相同算法的本地冻结经验与获准 Hub 候选；强 single 和 swarm 在相同原始事实上的表现。网络集成带来的额外延迟与费用单列。

EvoMap 路径可以提供不同协作与复用机制，但不能从“采用了该协议”直接推导业务效果更好。若主要提升来自更强模型、更宽工具权限或更多预算，报告必须按真实原因归因。

## 12. 权限、审批、租约与执行安全

### 12.1 有效权限的交集

一次操作的有效权限应等于：使用者/工作区角色权限、连接器实际凭据权限、应用与环境范围、Agent 能力白名单、任务权限、自治策略和本次 grant 的交集。任何一层缺失、过期或发生版本变化均拒绝，不通过其他层补足。

读取也可能有成本或敏感性。数据库导出、秘密读取、大范围日志检索、云端查询和外部消息发送不能统一标为“无风险 read”。工具目录应使用确定的 effect_class、data_class、cost_class 和 target_scope；运行时模型不能通过把工具描述写成只读来绕过策略。

### 12.2 自治级别与人的身份

| 类别 | L0 仅建议 | L1 明确审批 | L2 受控自动 |
| --- | --- | --- | --- |
| 普通授权范围内读取 | 配额内允许 | 配额内允许 | 配额内允许 |
| 生成候选方案/本地产物 | 允许，不外部发布 | 允许，不隐含批准 | 允许，不隐含执行 |
| 修改示例或已支持的配置 | 禁止实际修改 | 具体方案批准后执行 | 满足全部预置策略后执行 |
| 创建外部 PR、发消息、改工单 | 视作外部写入，不自动发送 | 审批或明确直接指令授权 | 仅注册动作及策略范围 |
| 高风险/不可逆动作 | 禁止 | 专门授权和额外确认 | 默认禁止，需独立安全决策 |
| 变更自身权限、测试或策略 | Agent 不允许 | 由有权限的使用者操作 | Agent 不允许 |

本地单用户版可以由同一位使用者提出并批准低风险动作，但不得伪称双人复核。团队版增加 viewer、investigator、operator、approver、workspace_admin 等角色，按环境配置职责分离。组织管理员并不自动获得所有连接器的生产变更权限。

### 12.3 方案审批必须绑定什么

ApprovalDecision 绑定 workspace、work_item、proposal_id/version、动作规范化哈希、目标资源集合、连接器能力版本、权限策略版本、基线配置/部署身份、测试计划哈希、回滚方案哈希、有效期和允许的最大影响范围。

等待审批期间不维持一个无限续租的执行任务。批准后建立新的 ExecutionAttempt，获得新 lease/grant，并再次读回外部基线。目标、补丁、动作参数或约束发生变化就要求重新批准。单纯“方案标题相同”不能复用旧批准。

外部平台 webhook 的批准事件必须验证签名、主体映射、时效和重放；来自聊天的“同意”必须指向唯一待批准对象。含糊回复不能授权最近一次变更。审批按钮请求使用 Idempotency-Key 和 expected_revision，避免双击或迟到响应执行不同方案。

### 12.4 必须保留的安全不变量

| ID | 不变量 | 落点 |
| --- | --- | --- |
| I-01 | Agent、模型、UI、遥测不能直接改变活动配置或授予权限 | Authority + Executor |
| I-02 | 所有付费与外部写入在最终准入点重查 run、scope、epoch 和策略 | Dispatch/Execution gate |
| I-03 | 相同外部资源的 fence 由共享资源 authority 管理，不按 run 各自从零计数 | ResourceAuthority |
| I-04 | 旧实例、旧任务、旧 transport、旧 generation 的产物不得参与新一轮决定 | Task/Context gate |
| I-05 | 终态 run 不复活；继续工作只能新建 run | Workflow state machine |
| I-06 | 一个 arm 的取消、预算、状态或经验不能影响对侧 | Pair boundary |
| I-07 | 执行绑定批准内容及当前资源版本；校验和写入尽可能使用供应商 CAS | Execution adapter |
| I-08 | 不支持安全条件写入的资源，不声称绝对 fencing；高风险自动模式禁用 | Capability policy |
| I-09 | 验收来源独立，绑定实际生效配置；部分修复不能误过 | Validation service |
| I-10 | Provider 未知消耗不算零；费用和结果分开结算 | Budget ledger |
| I-11 | 被治理内容中的提示词不能改变权限或选择隐藏真值 | Evidence/tool broker |
| I-12 | UI 状态由权威事件及明确结果共同推导 | Projection service |
| I-13 | 凭据不进入模型、普通 Worker、日志或公开导出 | Credential broker |
| I-14 | 读回不一致或副作用未知时先 reconciliation，不自动盲重试 | Execution state machine |
| I-15 | 经验命中与外部声望不跳过当前审批、预算和业务验证 | Memory gate |
| I-16 | 公开分享、Hub 发布和通知发送拥有独立授权与撤销路径 | Artifact/notification service |

### 12.5 外部系统不是本地 SQLite 事务

不能宣称本地事务能使任意云 API 具备 exactly-once 或原子回滚。推荐过程为：原子登记 execution intent 与资源 fence；创建受限 grant；执行器准入；调用供应商幂等/CAS API；持久化回执；读回实际状态；再提交业务阶段转换。

请求超时或服务在调用后崩溃时，以 execution_id 和供应商 request/idempotency ID 查询结果。供应商无法查询或不支持幂等时，进入 unknown/reconciling 并暂停同资源的新写入。重试策略是动作类型的能力，不是通用 HTTP 重试装饰器。

### 12.6 本地入口也需要明确安全边界

服务默认绑定 loopback；校验 Host 与 Origin，写请求使用本地会话/CSRF 机制，Agent 专用入口采用独立身份。浏览器里的其他网页不得借助本地端口触发操作。连接器 URL 要限制协议、明确允许的主机与端口，防止凭据被重定向到任意目标。

允许本地 Langfuse/私有服务时，以用户确认的连接作用域作为例外，而不是完全关闭地址检查。外部 webhook 原样输入需验签、大小限制、时间窗口与防重放；未通过校验的内容不能直接立案或触发付费调查。

## 13. 配置修复、代码补丁与部署

### 13.1 两条修复通道

配置通道优先复用现有有限动作执行体系，扩展为 `ConfigAdapter` 的 read_current、validate_change、apply_if_version、read_back、restore_if_safe。不同对象有各自 schema、权限与回滚条件，不能把所有值交给任意 JSON 更新器。

代码通道建立单独的 `PatchWorkspace`。它从固定 commit 创建隔离工作副本，修改允许路径，生成 diff，在隔离环境测试，再由独立 Publisher 创建 PR/MR。Agent 不拥有用户原始工作区写权限，也不直接编辑项目自身的 authority、隐藏验收集、部署凭据或安全检查。

### 13.2 代码修复流水线

| 步骤 | 产物与约束 | 失败去向 |
| --- | --- | --- |
| 绑定源码 | 事故时部署版本、仓库 commit、当前目标分支分别记录 | 无法映射时 needs_input，不猜 HEAD |
| 准备工作副本 | 固定 source hash、允许目录、依赖清单 | 脏目录不覆盖；隔离失败停止 |
| 生成补丁 | diff、修改理由、证据引用、文件清单 | 越界或更改安全规则拒绝 |
| 静态与组件检查 | 固定命令、工具版本、退出码、原始日志 | 保留失败，不自动弱化测试 |
| 独立复核 | 对功能、误修、权限与副作用给出结论 | 冲突未解决则不发布 |
| 发布候选 PR | 固定 base/head、补丁哈希、检查链接 | 只写获准仓库/分支；失败不重复创建 |
| 部署适配器 | 获准制品、目标环境、发布回执与实际版本 | 无适配器时停在 awaiting_deployment |
| 业务验收 | 针对实际部署版本进行独立测试与观察 | 验收失败继续事故，不显示恢复 |

补丁已生成、PR 已创建、PR 已合并、制品已部署、业务已恢复是五个不同里程碑。不能因为外部 PR merge 成功就自动触发 incident.resolved。

### 13.3 测试代码本身是不可信输入

仓库的测试、构建脚本、依赖安装钩子都可以执行任意代码。测试执行器不得继承模型、Git、云端或通知凭据，不挂载用户 HOME、SSH Agent、Docker socket 或原工作目录。网络默认关闭，必要依赖在受控下载阶段准备，并记录锁文件和来源。

Mac 本地可为初期只读调查复用现有 Seatbelt 边界；运行不可信构建需另外选择容器/虚拟化执行配置并完成隔离验证。不能因为 Node/Python 命令名常见就认为安全。未具备该隔离环境时，允许生成补丁但不能声称已执行可信测试。

### 13.4 回滚与补偿

回滚是新的受控动作，绑定要撤销的 execution、before/after 身份和当前状态。若其他合法变更已发生，不允许直接恢复旧快照覆盖它们。对数据库迁移、删除资源、消息发送和退款等不可逆副作用，明确使用补偿/人工处理路径，不能提供虚假的“一键回滚”。

M3 只对一类可安全条件写入的本地应用/预发布目标完成自动部署闭环；生产云变更在后续连接器认证后逐项开放。最终功能仍保留，不把未支持目标显示为已恢复。

## 14. 预算、调度、取消与恢复

### 14.1 预算账户与费用事实

建立 WorkspaceBudget、WorkItemBudget、RunBudget 和 Reservation。所有角色、工具、辅助总结、独立验证、重试及外部查询费用均归入相应账户；不能把“只有修复模型 token”当全流程成本。观察被治理应用的消费与 Section9 自身治理消费分别统计，避免相互叠加。

账本区分 known_actual、pending_reservation、unknown_exposure 和 confirmed_zero。unknown_exposure 是保守占用/不确定性，不是已经确定的实际账单；后续供应商对账可追加更正记录，但不能覆盖原始未知状态。没有官方计价数据时只报 token 与已知费用下界，不编造金额。不同模型的 token 不直接代表相同货币成本；金额换算绑定供应商价格版本、币种和统计期间，token 预算与货币预算分别检查。

请求 max_tokens 与本地估算不是供应商绝对计费保证。若供应商返回超过预留的真实 usage，应记录 overrun、停止新准入并提醒；不能截断记录让账本看起来没超限。真正的硬费用上限还取决于供应商账户额度和能力，必须明确限制。

### 14.2 为验收保留预算，不给某一侧特殊待遇

计划进入执行前预留整个独立验收集的最低可运行预算；调查和补充检测不得消耗这部分 escrow。若无法容纳验收，停止在 awaiting_budget 或不执行，而不是先改生产再发现没有钱验证。

当验收集无法可靠估算时，以公开保守上限或可解释的分批策略准入。两侧使用完全相同的预算公式、总额度、验收任务和费用口径。预算不足时不跳过必要检查；允许停止检测后重估或由使用者为整个实验统一增额并生成新 spec。

重复探针采用必要性与退避策略。已经有可用检测证据、正在执行或验收时，不持续启动昂贵的重复语义检测；但必要的低成本活性与安全信号保持运行。实现先用确定性测试复现当前 cost baseline 失败，再决定策略，不直接假定某种死锁已被证明。

### 14.3 发送前准入与终态竞态

在获得供应商槽位后，authority 原子检查 run_active、cancel_epoch、权限、task/instance（角色推理）、budget reservation、deadline，并将请求从 queued 变为 dispatch_authorized。这个事务提交是线性化点。

若终态/取消先提交，后续准入必须失败，不发送请求。若准入先提交，则该请求视为已准入在途操作；终态尽力取消，但不承诺供应商未收到或停止计费。实际网络发送与本地数据库无法形成跨系统原子事务，因此不能把“终态后物理网络绝不会出现一个已准入字节”作为不可实现的保证。

为减少空窗，准入后立即由受控网关调用；故障导致无法确认发送时按 unknown 保守处理。该规则应同时用于付费模型和有副作用工具，区别在各自的对账和补偿机制。

### 14.4 调度与并发

Workspace 层限制总活跃请求；每个 run、角色和连接器另有上限。共享网关按工作区/run 的公平队列准入，保留验证优先级但不能使其他工作永久饥饿。预算等待不占供应商槽位，网络等待不持有 SQLite 写事务，取消后及时移出队列。

正式 benchmark 模式应限制本项目其他模型工作，包括聊天、导览、后台总结和监护调查；后台只读扫描仍需记录资源影响。每个请求分别记录 queue_ms、provider_ms、tool_ms、total_ms。两侧同机并行的竞争成本不得与顺序资源匹配实验混合解释。

### 14.5 统一终止与恢复

run 终止先提交终态、递增 cancel_epoch 并撤销任务/授权，再取消队列、在途请求和运行 jobs，最后结算已知或未知费用。终止函数必须幂等，可在部分清理失败后继续执行。清理失败显示 drain_pending，不隐藏后台仍存在的请求。

重启恢复按 request dispatch_state 和 execution_state 对账。旧 run 统一 interrupted，不复用旧 lease；符合策略的监护/自动化可以恢复调度，新一次调查建立新 run。旧日志和迟到网络消息保持原 run、generation、resource revision，不因收到时间晚就使用当前配置元数据重新归属。

机器睡眠或关机时不能承诺持续监护。恢复后显示覆盖缺口，按 catch-up 策略补数据；禁止为积压事件瞬间启动大量付费工作。需要全天运行的用户使用后续常驻服务器部署，不用一条防睡眠命令替代可用性设计。

## 15. Skills、Runbook、知识与记忆

### 15.1 四类资产分开管理

Knowledge 保存事实与文档；Skill 保存如何开展一类任务的方法；Runbook 保存可审核的操作步骤；Memory/Lesson 保存经过结果反馈的经验。这四类资产可以关联，但不能都当成可以执行的提示词。

Langfuse 官方 Agent Skill 可作为调查方法参考，实际读取通过 Section9 的受限连接器进行。Skill 中的 CLI/MCP 调用要求不自动获得权限；绑定到一个调查也不代表可以写 prompt、dataset 或 scores。官方 MCP 的读写能力需要由实际工具白名单和凭据范围共同限制。[S23][S24]

### 15.2 资产生命周期

统一记录来源、license、内容哈希、版本、作者、可用领域、数据分级、所需工具和适用条件。状态建议为 draft → candidate → validated → approved → active；过期、撤回或发现反例后进入 deprecated/revoked。

导入外部 Skill/Runbook 时不直接执行安装脚本；先列出命令和访问目标。Runbook 的步骤使用类型化动作或审核过的执行模板，参数有 schema。任意 shell 模板不得因为文件来自 Git 仓库就自动可信。

### 15.3 经验生成与复用

业务恢复后，通过 outbox 生成 LessonCandidate，不让外部 Hub 故障阻塞已经通过的业务结案。经验内容包含症状、适用上下文、采取的动作、实际结果、反例、版本范围和限制；自动总结不是自动发布。

下次检索时检查当前资源、版本和条件，显示为何匹配。命中不意味着照搬；仍需形成新方案、新批准和新验收。用户可纠正、停用或删除经验，引用它的历史记录保留不可变来源摘要，后续不得再次召回已撤销内容。

### 15.4 训练与评估隔离

开发集、验证集和正式 benchmark 的经验输入分开存储。正式实验冻结输入快照，输出写独立结果库，不让第一侧或前一轮的答案进入另一侧。任何跨轮学习实验都应作为独立实验条件，single 和 swarm 获得同样的可用经验。

对开放 Hub 经验，记录查询时间、候选清单和内容哈希；无法冻结或删除变动影响时，不把结果与冻结条件直接合并。敏感数据默认不进入公开经验。

## 16. 界面与端到端体验

### 16.1 信息架构与路由迁移

新的产品主导航为工作总览、应用与接入、事故、代码审查、成本优化、基础设施、安全评估、自动化、知识与经验、组织设置。B/C 版本只展示已可用模块；后续功能可在能力目录中明确标为开发中，不摆放看似可点却无真实工作流的空页面。

实验室保留原控制台、演练、Pair、禁言与接管。建议新入口 `/lab/console`，原 `/showcase/swarm` 和 `/showcase/baseline` 保持可访问；新首页正式启用前保留旧 `/` 行为，借助路由功能开关迁移。历史固定链接不可被悄悄重定向到新实验。

### 16.2 关键页面与必要字段

| 页面 | 必须展示 | 必须处理的空/失败状态 |
| --- | --- | --- |
| 工作总览 | 等输入/审批、进行中、未解决、连接健康、预算 | 无连接、无事件、数据滞后、暂停监护 |
| 应用详情 | 环境、连接范围、版本映射、监护策略、最近工作 | 权限不足、版本未知、没有近期业务数据 |
| 事故工作区 | 影响、时间线、证据、假设、方案、执行、验收 | 证据不足、方案被拒、执行未知、验收失败 |
| 协作办公室 | 真实任务、角色、阻塞、交接与引用 | 未启动、离线、接管中、没有独立模型活动 |
| 审批页 | 明确对象、diff、范围、风险、测试、回滚、有效期 | 方案失效、资源变化、无批准权限 |
| 运行详情 | source identity、任务、用量、模型/工具事件 | 中断、unknown、尚有清理、原始证据不完整 |
| 实验室 | 示例范围、条件、Pair身份、预算、干预记录 | 一侧失败、受干预、规格不匹配、未测试 |

每个页面都有“下一步是什么”的明确出口，但不得自动替用户扩大权限。技术 ID 可折叠，不能丢失；业务语言必须保留成功、失败、取消、未知的语义。

### 16.3 证据浏览与事件投影

产品状态从权威状态机和完整投影读取，分页日志仅负责浏览。阶段/角色的证据抽屉以 event_id 查询并校验 workspace/run，而不是只过滤最近 200 条。服务端返回覆盖范围、固定 watermark 和 next_cursor；用户翻历史页时新事件不挤掉当前阅读内容。

未读计数按最后已读 sequence 计算；页面可暂停跟随，但继续接收并累计。网络恢复以事件身份去重；对不完整分页明确提示，不能用“完整记录”命名第一页。后台轮询不得改变 selected_work_item 或 selected_pair。

### 16.4 显示的真实性约束

原始请求完成要查看 outcome/status。授权成功只表示可执行；执行提交只表示动作发生；验收对象存在只表示有记录；只有独立 checks 全部满足且版本稳定，才能显示恢复。模型服务最近一次成功不等于当前健康，连接器最近一次认证成功不等于数据及时。

办公室角色只有在真实任务事件存在时移动到工作区域。纯布局动画、预览内容与真实运行明确区分；不能利用固定动画营造独立调查、反驳或成员接管。图片和角色资产获取失败时提供可用的原创替代布局，商业发行前另过素材许可门禁。

### 16.5 可理解性验收

让未参与开发的使用者完成接入、判断一件事故、拒绝方案、批准新方案、查看失败、重新调查和导出。记录误点、需要解释的步骤、是否误解对象及结果，而不只测按钮是否存在。要求受支持桌面宽度下关键操作不被遮挡；键盘可进入抽屉、读取原始证据、关闭并恢复焦点。

## 17. 全平台模块扩展设计

### 17.1 Resolve：先完成的产品纵向主线

Resolve 作为第一条完整产品线，交付连接、信号、事故、调查、处置、验收、观察和经验。每个事故拥有多个调查尝试与方案版本；补充证据、误报、人工覆盖、再调查和复发均有独立记录。影响图必须区分“已证实因果关系”和“待验证相关性”。

Pulse 类能力先实现确定性去重、基于服务/时间/资源的关联和规则抑制，再引入有预算的语义辅助关联。模型建议的合并可被撤回；拆分后保留原始关系和成本归属。质量洞察统计所有终态结果、人工判定和无法确定的样本，不将没有反馈算正确。[S11][S12][S32]

### 17.2 Review：不是给代码 diff 发一次提示词

接入 PR/MR 事件后，冻结 base/head、仓库权限、文件清单及关联工单。按变更范围收集上下文，运行语言工具与有界调查，形成带文件位置、证据、影响和可操作建议的 Finding。批注必须绑定实际 diff 位置和 head；新提交到来时旧 finding 保留版本，不错误贴到新行。

交付审查工作区、接受/驳回/修复状态、人工误报反馈、提及命令、重新审查、补丁建议、自动修复候选、冲突解决候选、CI 失败调查、发布风险与 Approve/Hold 决策，以及仓库约定和趋势报表。旧命名/入口按任务并入，不重复计数。[S15]

代码审查的 root cause 与漏洞严重程度需要证据，不把所有可疑点都发成高危。发布风险报告是建议，不修改 CI 成功状态；冲突解决需要合并语义测试，不能只消掉文本冲突标记。发布评论、创建 PR、合并和部署是不同权限。

第一集成平台为 GitHub，随后 GitLab、Bitbucket、Azure DevOps，以及 Review 鉴权目录中的 AWS CodeCommit。每个平台独立通过 webhook、撤权、分页、diff 定位、重复事件、限流和写入幂等验收，通用 MCP 不能代替这些证明。[S10]

### 17.3 Optimize：云成本与治理成本分开

建立 BillingSnapshot、CostAnomaly、OptimizationFinding、SavingsEstimate、ChangeAction 和 RealizedOutcome。读取多账号账单、用量与资源指标，保留币种、税费/折扣处理、计费时区、服务商数据截止和完整性。进行中的月份与已结束月份分开，不把迟到费用默认为零。

分析支持按服务/账号/标签/资源归因、已完成月份对比、趋势、预测、异常、报告及计划报告；建议包括闲置、规格、存储与承诺折扣等领域。其用户价值与现有“把上下文 multiplier 改回一”不同，需真实云账单与资源关系才能验收。[S16]

潜在节省是估计，不是已实现收益。不同建议若作用于同一资源/相同费用基数，必须去重并说明互斥；避免同时把删除实例和缩小实例的节省相加。执行优化前检查性能、可用性和回滚，执行后按相同口径测量收益与新增损害。

承诺购买、预留实例、长期合约等会产生财务义务，不能因为 L2 自动模式就默认购买；必须有专门权限、金额和期限限制。没有真实账单数据时，只能交付演示数据分析或方法样例，不能标为用户收益。

### 17.4 Stack/基础设施：资源和变更计划

先实现资源发现、标签/所有权、服务拓扑、健康与配置漂移，再接类型化变更或 IaC 计划。Terraform/Pulumi/Kubernetes 等各自的版本、状态锁与权限必须通过适配器传入 authority。计划可审查、批准和拒绝，执行前重新确认计划基线未变化。

数据库调优先只读收集 schema、慢查询与执行计划；创建索引、改参数、改行数据、迁移和删除分成不同能力。Kubernetes 调查必须绑定 cluster/context/namespace，禁止使用用户默认 context 决定执行目标。SSH 默认使用批准的模板和固定主机密钥，不向模型交付任意网络 shell。

官方 Stack 页面部分细节尚未完整取得，现阶段把以上定义为 Section9 拟交付任务集；最终对标前需补齐官方功能明细并进行差异验收，不能以自拟范围覆盖未知功能。[S10][S14][S28]

### 17.5 Cyber：只对授权目标开展受控安全评估

安全评估拥有 AppTarget、AuthorizationScope、AssessmentPlan、AssessmentRun、SecurityFinding 与 Retest。记录目标所有权或授权、域名/IP/环境、测试时间窗、认证账户、允许深度、速率和禁止动作。重定向或发现相邻目标不能自动扩大范围。

完整产品流程包括注册应用、上传/关联源码及测试账号、手动或计划评估、进度、发现表格/看板、证据、人工判误报、整改任务、复测和导出。先实现非破坏性配置/代码/授权测试和测试环境能力；高影响测试必须独立授权、限流和紧急停止，不能自动扫描无关第三方系统。[S10]

评估凭据与推理凭据分离，验证产物默认私有；敏感漏洞细节、令牌与用户数据在导出时脱敏。没有执行验证的怀疑项标为 suspected，不与 confirmed 混计。安全复测通过并不代表整个系统没有漏洞。

### 17.6 Automation、OnCall 与 ChatOps

AutomationJob 支持定时、webhook 和仓库事件触发，持久化配置版本、运行记录、预算、并发限制、停用和失败政策。事件处理采用可重放 inbox 与幂等消费；至少一次交付不能导致多次实际写入。时区使用 IANA 标识，夏令时、错过窗口和长时间离线有明确 skip/catch-up 规则。

OnCall/协作房间围绕事故建立：成员、人/Agent 消息、任务、交接、升级、审批与来源链接。人员的外部聊天身份映射到工作区主体；机器人收到的任意群聊不自动获得资源权限。外部通知支持去重、失败重试和撤销订阅，敏感正文不能在无权限频道泄漏。[S29]

Slack、Teams、Google Chat、邮件和应用内通知使用同一业务对象及 command API。自动化、CLI 与聊天都不能绕过审批。默认新启用自动调查只处理之后的合格事件；历史积压需要显式范围与预算，避免一键开启产生大量支出。[S31][S33]

### 17.7 通用 Agent、知识、Artifacts 与分享

提供自定义 Agent/能力、对话附件、排队追问、引用、分支、历史与命令模板；配置改变只影响新任务，历史保存原始模板哈希。跨对话 memory 可纠正与删除，工作区和仓库范围不混用。[S10][S22]

图表、表格、仪表盘、报告、幻灯片和图像生成/编辑属于独立 ArtifactJob。数据分析保存输入快照、查询、计算口径与产物版本。图像/编辑能力需要明确支持的生成服务与费用账户，不能假定当前 EvoMap 文本端点已经提供该能力。[S17]

分享动作生成有权限、有效期和撤销机制的 ArtifactShare。Mac 本地链接不能冒充互联网公开链接；对外分享在后续托管模式或用户明确选择的发布目标中实现。工作区 branding、导出模板和可访问性要求纳入公共产物服务，不各模块独立造一套。

### 17.8 团队管理、API、计费和部署

完整目标包含组织、工作区、成员/角色、审计、MFA、OIDC/SAML SSO、SCIM、权限撤销、API/CLI/webhook、使用量、额度、通知、BYOK 和不同部署形态。组织与套餐界面不能先于服务端授权；权限测试必须覆盖跨工作区和撤权后的在途任务。[S18][S19][S20][S21]

计费采用独立不可变账本与供应商/支付回执，订阅、席位、额度和实际模型消耗分开。支付重试、退款和 webhook 幂等是后期产品任务；当前规划不执行购买或连接支付账号。BYOK 要能证明指定请求确实使用用户选定账户，并在停用后停止新请求。

## 18. API、事件与公共契约

### 18.1 API 版本与语义

新产品 API 使用 `/api/v1`；现有 `/api` 与 `/agent` 作为兼容入口，内部逐步映射到公共服务。命令与查询分开，耗时任务返回 job/run 标识，不阻塞浏览器直到远程模型全部完成。

以下均为建议接口，需根 Agent 冻结后实现。所有对象 ID 在服务端解析 workspace/app/environment，不信任请求体自行声明其归属。写请求需要主体身份、Idempotency-Key 和资源/对象版本；重复请求只返回相同 receipt，内容不同使用相同幂等键返回冲突。

| 建议接口 | 用途 | 重要约束 |
| --- | --- | --- |
| POST /api/v1/connections | 新建连接和凭据引用 | 不返回秘密，不自动启动付费调查 |
| POST /api/v1/connections/{id}/validate | 验证并读取最小样本 | 范围限制、无外部变更，记录探测成本 |
| POST /api/v1/apps/{id}/scans | 创建明确时间窗扫描 | 覆盖与预算，异步任务 |
| GET /api/v1/work-items | 总览与模块查询 | 工作区授权、稳定分页 |
| POST /api/v1/incidents | 人工创建事故 | 发生时间与提交时间分离 |
| POST /api/v1/incidents/{id}/investigations | 新调查或补充调查 | 新 run，不复活旧任务 |
| GET /api/v1/runs/{id}/events | 固定水位分页/SSE | scope、cursor、丢失窗口明示 |
| GET /api/v1/events/{id} | 按 ID 获取抽屉证据 | 验证调用者对原 scope 的读取权限 |
| POST /api/v1/proposals/{id}/decisions | 批准/拒绝具体版本 | decision_id、版本和动作哈希 |
| POST /api/v1/proposals/{id}/executions | 建立执行尝试 | 审批有效、目标版本与 fence |
| POST /api/v1/runs/{id}/cancel | 取消明确 run | 先撤权再排空，幂等 |
| POST /api/v1/executions/{id}/reconcile | 查询未知副作用 | 不隐含重试或回滚 |
| POST /api/v1/executions/{id}/validations | 独立验收 | authority job + 实际部署版本 |
| GET /api/v1/work-items/{id}/export | 生成/下载证据包 | 一致快照、脱敏、分页文件与哈希 |
| POST /api/v1/automations | 定义计划/事件工作 | 独立授权，默认禁用后确认启用 |
| POST /api/v1/webhooks/{source} | 事件接收 | 验签、去重、大小/速率限制 |

Review、Optimize、Cyber 等模块在相同规范下扩展资源路径，不能直接调用数据库或另建无审计写接口。对外 MCP 仅暴露注册工具的这些服务，不是开放所有 REST 路由。

### 18.2 错误契约

统一 ErrorEnvelope：code、message、retryable、scope_ref、request_id、details、next_action。客户端不得解析自然语言决定是否重试。401/403 为身份与权限，409 为版本/幂等/状态冲突，422 为业务输入错误，429 为预算或频率限制，503 为依赖不可用。无法确认副作用使用明确结果状态，不伪装成普通可重试 503。

建议枚举包括 RUN_TERMINAL、SCOPE_MISMATCH、INSTANCE_STALE、LEASE_EXPIRED、PLAN_STALE、APPROVAL_REQUIRED、RESOURCE_VERSION_CHANGED、CAPABILITY_UNSUPPORTED、BUDGET_RESERVED_FOR_VALIDATION、PROVIDER_USAGE_UNKNOWN、EXECUTION_OUTCOME_UNKNOWN、EVIDENCE_NOT_AVAILABLE、SOURCE_COVERAGE_INCOMPLETE。

### 18.3 事件信封

事件至少包含 schema_version、event_id、event_type、occurred_at、recorded_at、workspace_id、realm、work_item_id、run_id、task_id、producer/instance、generation、task_epoch、transport_epoch、resource_ref、resource_version、correlation_id、causation_id、payload、evidence_refs。

source_occurred_at 和本地 recorded_at 分开，迟到 telemetry 保持原操作身份。Source 数据与模型推论明确不同 event_type。原始外部 payload 存入受控证据库，公开事件仅携带脱敏引用。运行时不需要完整内部思维链；需要可复查的简明结论、工具行为和引用证据。

### 18.4 Outbox、投影与导出

事务内同时提交状态、费用/动作记录和 outbox 事件；外部通知、Hub 发布及 UI 投影异步消费。每个消费者以 event_id 去重，保存水位；重建投影不会再次执行外部动作。Catalog 与日志不再共同承担 authority 的职责。

导出记录 export_snapshot_id、source_watermark、模式/版本、文件数量、每文件哈希、脱敏策略和不完整原因。超过上限不能静默截断；生成较大 ZIP 应在后台 job 或流式管道中完成，避免同步压缩阻塞心跳。这里的后台任务是拟实现的本地产品能力，不是本次文档编写会代为运行的服务。

## 19. 本地发行、团队部署与运维

### 19.1 B 层本地发行要求

保持 install、doctor、start、readiness、stop 的职责分离。install 明示网络与依赖下载；start 只启动已安装服务，不隐含付费模型；doctor 只读并给出修复路径；business readiness 需显式发起，验证真实业务但不扩大为普遍健康声明。

支持矩阵记录已测试 macOS/CPU 架构、Python/Node、Docker/虚拟化版本和资源范围。未验证 Intel 或其他系统时不能标支持。配置统一解析器供 runtime 与 doctor 共用，避免环境变量、dotenv、空值和默认值在多个脚本中产生不同结论。

完整停止要覆盖本项目应用、子进程、导览/评估和本项目观测栈，保留数据卷、不影响无关容器/进程。素材和观测服务故障可降级，但必须清楚说明哪些能力不可用。没有模型密钥时仍能读历史，不应产生 Provider 调用。

### 19.2 资源控制与维护成本

记录空闲、单次调查、Pair、补丁测试和导出时的 CPU、内存、磁盘、文件句柄与模型槽位；分别记录应用进程和 Docker 服务。只有实测后才给最低配置，不以当前机器能启动作为资源峰值证明。

保留策略按数据类型设置：原始 traces、脱敏证据、审计、制品、日志和模型内容可有不同期限。删除前检查被引用证据和用户导出需求；权限审计和费用更正不得随日志轮转意外消失。长期 retained history 的投影应增量化，不每次轮询全表重建。

### 19.3 备份、升级与恢复

本地通过 SQLite 一致备份或停止写入后导出，保存 schema 与运行身份。备份不含明文 Keychain 秘密；恢复到另一台机器需要重新绑定凭据，并显示哪些连接尚不可用。恢复前校验版本和完整性，失败不能覆盖正在使用的数据。

新增数据库版本必须具有 migration 验收和可回退安装流程；涉及不可逆迁移时先生成并确认备份。升级后先只读检查，再允许监护和执行，不自动继续旧的未知副作用任务。历史只读链接保持原身份。

### 19.4 团队与专属部署

M5 增加组织身份、服务端 RBAC 和共享执行资源 authority 后，才开放网络团队访问。可将新产品存储迁移到 PostgreSQL，并增加部署模式下的租约/互斥测试；不为了“企业感”要求 B 版立刻更换数据库。

SaaS、专属云、自托管和隔离网络分别作为部署 profile 验收，包含凭据管理、备份恢复、审计、升级和依赖清单。CloudThinker 自托管资料含预览/早期接入表述，不能据此推断其所有模式已经在本次被验证。[S20]

### 19.5 “完全断网”与远程 EvoMap 的矛盾必须明确解决

远程 EvoMap 推理及远端 Hub 需要相应网络访问，不能同时承诺完全断网实时推理。若完整对标包含真正隔离部署，需提供客户专属可达的私有端点或本地模型/本地协作模式，并分别验证能力。隔离模式的模型效果、经验范围与联网版可能不同，不能混用结果。

因此，将“联网默认 EvoMap 蜂群”和“隔离部署等价工作流”分别登记。未有获准私有部署方案之前，完全断网实时治理保持 blocked，而不是悄悄改称“离线可读历史”来算完成。

### 19.6 发行签署

B 发布需所有 P0/P1 交付风险关闭，真实安装/故障/恢复矩阵通过，且文档由陌生使用者验证。C 需困难场景、真实连接与安全边界通过。F 需全部能力行及连接器支持操作 verified；采购、认证和 SLA 等非代码事项单独取得证据。

任何阶段都不得由总测试数量或某张首页截图自动签署。源版本、构建、配置、凭据范围、模型标识和测试范围必须同时记录。

## 20. 困难治理场景与公平实验

### 20.1 首选场景：分群售后政策与检索发布事故

目标用户是假设中的客服 AI 维护团队。失败代价包括错误退货承诺、运费承担错误、正常订单被拒，以及全局回滚导致新政策失效。这个场景能够复用现有小智工具、配置变更和独立验收，同时引入真实的范围判断与修复副作用；需求强度仍需后续用户访谈验证，不能当作已经验证的市场结论。

场景至少有三个订单 cohort：旧政策订单、新政策适用订单、不受此次发布影响的另一 SKU/区域。两份政策都合法，只是适用条件不同；故障来自日期/分组过滤或缓存范围错误，而不是文件名写着 degraded 就代表答案。不要增加全部模块和大量故障来制造人为困难。

### 20.2 证据与隐藏边界

订单事实、政策规则、发布记录、检索命中和缓存元数据分别经受限工具读取。Agent 获得调查所需原始资料，但不获得故障注入标签、预期补丁、验收答案或“正确配置”全量快照。单 Agent 与蜂群都能通过相同工具取得全部同等资料，不人为限制 single 只看一个来源。

隐藏真值由确定性业务规则和测试控制器维护。检测集展示少量异常症状；独立验收含未见订单、边界日期、正常 SKU、缺数据情况以及合法新政策。全局回滚、只改 prompt、只修一个 cohort 应至少有一个可复查的新增损害或失败结果。

### 20.3 适合并行与必须串行的部分

订单/政策适用性、发布差异、检索/缓存行为可以并行调查；共享证据后的因果验证和方案选择要汇合。执行必须按资源锁、当前版本、审批和动作依赖串行或显式 DAG 提交。独立验收不能与未完成的相关配置变更同时无约束进行。

首版修复限制在一至两类受控配置或一个小代码模块。若需要多个资源同时变化，但目标平台没有事务/CAS，则用明确顺序、补偿和未知状态处理；不把多 Agent 并行写入包装为“协作更快”。

### 20.4 第二、第三候选场景

候选二是工具重试与业务幂等不一致：调查调用轨迹、账本和重试策略，验证没有重复操作或漏处理。先在合成账本上测试，不接真实支付。候选三是成本保护与质量冲突：按查询类型压缩上下文/调整模型策略，检查复杂业务质量和响应时间没有恶化。两者作为后续题库，首阶段不同时实施。

### 20.5 比较条件

| 对照维度 | 必须一致 | 必须单独记录 |
| --- | --- | --- |
| 模型 | 相同请求模型、可支持参数、输出约束和调用能力 | 实际返回身份、供应商参数不确定性 |
| 信息 | 相同原始工具、证据集合与读取范围 | 调用顺序、每次返回快照和时间窗 |
| 能力 | single 同样可多步推理、批量读取、修订假设 | 角色分工和通信开销 |
| 预算 | 总 token/费用上限、验收 escrow、工具费用、超时 | 预算预留、unknown、overrun |
| 资源 | 相同并发规则、执行权限及目标克隆 | 同机竞争、缓存、其他负载 |
| 记忆 | 关闭或相同冻结输入快照 | GEP/Hub 来源、学习是否允许 |
| 验收 | 相同隐藏样例和业务判定，不按补丁字符串 | 失败类别、无新增损害、人工介入 |

用动态 source 时按 Pair 冻结可读取快照，并通过相同查询语义返回；真实应用修复不能让对照两侧同时改同一生产资源。Lab 使用两个独立克隆，验证它们初始业务事实与故障真值相同。

### 20.6 实验设计与结果判定

先做 4–6 对 pilot 排查工具和合同问题，pilot 不并入冻结后的主要结论。冻结代码、提示词、预算、故障生成规则和验收合同后，使用未见 seed 开始初步重复实验。可先准备不少于 20 对，但是否足够取决于差异大小与方差，不能把该数字当作统计显著性保证。

比较单 Agent、swarm、本地/原生 EvoMap 传输、冻结 memory 开/关时分批做受控实验，不一次改变全部变量。若产品运行采用廉价子模型，另设“真实生产配置”实验，不能把模型不同的收益归为蜂群结构。

主要结果为整体业务恢复、分组正确率、误修、实际权限违规和影响范围；次要结果为恢复耗时、已知成本、unknown 率、人工介入和维护负担。被拒绝的越权尝试与真正应用的违规分开统计。失败、超时、弃权、干预都保留分母；人为 reset 安全实验单列，不能伪装性能样本。

耗时同时报告包括失败的完成/中断分布与成功样本的条件分布，避免只挑成功样本比较。成本含所有角色、验证和失败请求，unknown 时提供不完整标记或上下界，不给虚假精确节省率。采用成对差值和适当区间估计；多场景子组分析预先声明，不事后挑赢家。

若强 single 同样安全、成功率不低且更快更省，应承认该类任务不需要蜂群。若收益只在证据分散且可并行场景出现，就限定产品推荐范围。任何一次未授权真实写入都阻塞相应执行能力发行，不能被平均成功率稀释。

## 21. 测试矩阵与交付证据

### 21.1 测试层次

T0 是纯函数/状态机/规则检查；T1 是真实 SQLite 与受控 transport 的组件测试；T2 是 ASGI/HTTP 与多个真实 Worker 的集成；T3 是真实授权服务和真实远程模型；T4 是非开发者、独立环境的产品验收。每条报告必须声明层次，不能把替身返回的 403 写成已验证生产入口拒绝。

对模型输出使用结构化边界测试与实际样本验证，不以 exact 文案匹配替代普遍语义正确性；在合成业务合同中可以使用确定性输出结构，但要明确其范围。测试数量只反映执行记录，不作为发行条件本身。

### 21.2 关键验收矩阵

| ID | 场景与触发 | 必须观察到的结果 | 最低层次 |
| --- | --- | --- | --- |
| AUTH-01 | L0 请求真实写入 | 配置不变、审计拒绝、无外部写调用 | T1/T2 |
| AUTH-02 | L1 批准方案后改参数/目标 | 旧批准失效，不能执行 | T1/T2 |
| AUTH-03 | 调查者请求隐藏数据或写工具 | broker 拒绝，不把工具自报权限当授权 | T1/T2 |
| AUTH-04 | 验收前无任务/旧实例/过期 lease | 不启动有成本验收 | T1/T2 |
| AUTH-05 | 验收中暂停、换实例、reset、改通信版本 | 不提交恢复；费用保留 | T2/T3 |
| AUTH-06 | 低权限用户通过 CLI/MCP/ChatOps 写入 | 与 UI 同样被拒绝 | T2 |
| SCOPE-01 | 跨 run/arm 的 task、plan、grant 与消息 | 拒绝并归入原请求审计，对侧不变 | T1/T2 |
| SCOPE-02 | 两个产品 run 修改同一外部资源 | 共享资源锁和 fence 生效 | T2/T3 |
| SCOPE-03 | 同一资源在两个工作区重复写绑定 | 禁止或经统一 authority，不能各自独立写 | T2 |
| SCOPE-04 | R2 选择 A 后轮询返回 active B | 仍选择 A，写操作目标不变 | T2 浏览器 |
| DISP-01 | R1 预留排队后 run 终态，随后释放槽位 | 不调用 Provider，已知未发送正确结算 | T1/T2 |
| DISP-02 | 准入先提交、终态后发生 | 按在途请求取消/结算，不宣称未发出 | T1/T2 |
| BUD-01 | 验收 escrow 与调查同时争预算 | 调查不能吞掉验收预留，两侧规则相同 | T1/T2 |
| BUD-02 | usage 缺失、矛盾、超预留或重复结算 | unknown/overrun 明示，不能负数或双计 | T1/T3 |
| BUD-03 | 重复检测、补充模型和验收并发 | 有界等待、不持槽等预算、不无限重试 | T2/T3 |
| EXEC-01 | 执行者暂停后接管并恢复旧请求 | 旧 fence 拒绝；实际配置不被旧者改写 | T2/T3 |
| EXEC-02 | 真进程退出而非 SIGSTOP | 按明确接管/中断规则处理，不混同暂停证明 | T2/T3 |
| EXEC-03 | 供应商已应用但回执丢失 | 进入 reconciliation，不重复变更 | T2/T3 |
| EXEC-04 | 外部资源在审批后被第三方更新 | CAS 失败或转人工，不覆盖合法变更 | T2/T3 |
| EXEC-05 | 补丁改到隐藏测试/权限/锁文件禁止路径 | 拒绝，原工作区不变 | T1/T2 |
| EXEC-06 | 不可信构建尝试读取凭据/联网/宿主资源 | 沙箱拒绝且保留证据 | T2 |
| VALID-01 | composite 只修部分 | 验收失败，无恢复事件 | T1/T3 |
| VALID-02 | 全局回滚修好旧订单却损坏新订单 | 无新增损害检查失败 | T1/T3 |
| VALID-03 | PR 已创建但未部署 | 状态停在待部署，不显示业务恢复 | T2/T3 |
| VALID-04 | 测试后配置/制品版本变化 | 结果不能用于结案 | T1/T2 |
| CONN-01 | 错密钥、撤销、限流、分页中断 | 区分失败与空结果，记录覆盖 | T2/T3 |
| CONN-02 | 自身 telemetry 被当作被治理流量 | 默认排除，不递归触发调查 | T1/T2 |
| CONN-03 | MCP 新增写工具/伪造只读描述 | 未审核工具不自动获得权限 | T1/T2 |
| CONN-04 | URL 重定向/私网越界/证书错误 | 范围外拒绝，秘密不发送给新主机 | T1/T2 |
| EVT-01 | 重复/乱序/迟到消息与 webhook | 幂等，保留源时间和 scope | T1/T2 |
| EVT-02 | R4 250 条事件及早期阶段抽屉 | 证据完整、未读与分页正确 | T2 浏览器 |
| EVT-03 | 超过 10,000 条事件、投影重建和导出 | 终态不丢失，水位/计数/哈希一致 | T1/T2 |
| UI-01 | R3 success/error/cancelled/unknown | 文案、颜色、筛选与真实结果一致 | T2 浏览器 |
| UI-02 | 延迟旧响应覆盖新选择/已关闭抽屉 | 拒绝旧对象渲染，写目标不变化 | T2 浏览器 |
| REC-01 | queued/sent/legacy usage 后真实服务强杀 | 恢复幂等、无永久 reserved、未知不归零 | T2/T3 |
| REC-02 | 备份恢复到新机器/旧 schema | 身份可追溯，要求重新绑定凭据 | T2/T4 |
| REC-03 | 停止脚本遇失效 PID/无关容器 | 不误杀，不删卷，明确未清理项 | T2/T4 |
| REC-04 | 休眠和长时间离线后恢复 | 标覆盖缺口，按预算和 catch-up 策略运行 | T3/T4 |
| GEP-01 | 真远端会话、消息、节点失效和去重 | 协议身份正确，本地权限不被上游覆盖 | T3 |
| GEP-02 | 不可信经验、过期资产和敏感内容 | 隔离/拒绝，不跳过验证，不自动上传 | T1/T3 |
| BENCH-01 | strong single 与 swarm 的读写能力 | 同原始工具、同预算、同验收和资源政策 | T1/T3 |
| AUTO-01 | 重复触发、停用后迟到事件、DST/错过窗口 | 不重复副作用，停用有效，时序可解释 | T1/T2 |
| PROD-01 | 陌生用户独立冷安装到完整事故闭环 | 无口头补步骤；失败可排错；证据可导出 | T4 |

多云、Review、Optimize、Cyber、团队和全部连接器还需业务专属测试，不能只复用上述安全矩阵。例如 Review 验证行号/版本，Optimize 验证互斥节省去重，Cyber 验证授权范围与误报复测，SSO/SCIM 验证撤权和会话失效。

### 21.3 真实验收环境

保留 dev、demo、acceptance、benchmark、product 五类模式。模式影响数据源、预算和允许目标，不改变核心权限规则。生产读取的数据不能未经授权复制到公开验收 artifacts。真实模型和有副作用测试有明确预算与目标白名单；测试脚本启动前展示其副作用。

独立验收至少使用另一位操作者和一套新的项目数据/配置。冷安装应包括首次依赖/镜像获取，而不是只复制开发者的 node_modules、venv 和已构建前端。不能在拿不到资源时伪造通过，报告保持 pending_external_validation。

### 21.4 交付证据格式

```text
release-evidence/
  manifest.json              源码、构建、模型、环境、范围、哈希
  checks/index.json          测试 ID、层次、命令、退出码、结果
  runs/<run_id>/             原始事件分片、usage、计划、动作、验收
  incidents/<incident_id>/   历次尝试、方案、关联信号及最终业务结果
  connectors/               认证/分页/权限/撤权/错误矩阵
  lifecycle/                安装、启动、停止、重启、备份恢复
  benchmark/                冻结协议、样本索引、全部成功与失败
  screenshots/              可选视觉证据，注明真实/替身/预览
  limitations.md            未执行、失败、外部依赖及不支持能力
```

manifest 不保存秘密，只保存凭据范围和版本引用。原始日志与截图在进入版本库前做敏感数据审核；关键词扫描无法证明图片、历史 Git 和全部秘密都无泄漏。业务恢复证据与权限审计都要可定位到实际测试对象和被测 commit。

## 22. 路线、工作包、依赖和估算

### 22.1 估算口径

以下是规划工作量，不是本次对话将执行的工作，也不是固定交期承诺。一个人日按约六小时有效工程工作计，包含实现、测试、代码审查、文档和集成；等待供应商、账户授权、真实数据和验收人员不计在纯工程人日中。

假设核心团队有 2–4 名熟练开发者，能使用 AI 辅助开发，但架构、授权、外部集成和真实验收不能按生成代码速度线性压缩。下列区间至少存在约 ±50% 的不确定性，主要来自连接器实际能力、EvoMap 原生接口和不可信代码隔离。

### 22.2 六个里程碑

| 阶段 | 交付范围 | 出口门槛 | 核心工程量 |
| --- | --- | --- | --- |
| M0 基线修复 | R1–R5、成本失败诊断、发行证据与真实恢复补测 | 高风险回归关闭，版本/账本/停止行为可信 | 5–8 人日 |
| M1 产品接入 | Workspace/Application/Incident、Langfuse+GitHub 读取、信号收件箱、EvoMap 接入验证 | 用户真实数据能形成可追溯事故，不依赖注入器 | 10–16 人日 |
| M2 调查与方案 | 多步 single/swarm、任务 DAG、反证、方案版本、审批、经验候选 | 调查失败可继续，建议可审查，权限不能绕过 | 12–20 人日 |
| M3 受控闭环 | 配置/补丁/部署首适配、独立验收、困难场景、自动化初版、B/C 验收 | 陌生用户完成一条真实闭环，公平试验可运行 | 15–25 人日 |
| M4 横向模块 | Review、Optimize、基础设施、Cyber、报表/Skills/ChatOps/API 初完整流程 | 每条产品线有真实纵向闭环，公共底座一致 | 45–80 人日 |
| M5 完整平台 | 全连接器补齐、组织/SSO/SCIM/计费、分享、部署 profiles、最终对标 | 全功能清单逐项 verified，未核实项清零或明确差异未完成 | 55–100 人日＋长尾连接器 |

M0–M3 合计约 42–69 人日，是第一条可靠产品主线及受控试用的规划量，不等于整个 CloudThinker 功能复刻。M4/M5 可按模块并行，但依赖同一权限/证据协议，不能将各自分支的“完成”相加就视为全平台已集成。

### 22.3 长尾连接器工作量不能隐藏

附录 A 按公开连接目录登记 83 个入口，另有 Review/ChatOps 文档中的附加平台任务。这是范围索引，不是已逐个验证的成熟度排名。[S10]

上述核心阶段可包含约 8 个优先服务/能力适配；其余约 75 个入口需额外拆解。复用同族认证和数据模型的简单只读适配，暂按 1.5–4 人日/入口规划，约 113–300 人日。复杂云平台、SSH、IAM、数据库写入和 IaC 的安全动作不能套用这个均值，另留 20–60 人日专项验证。单个完整云平台也可能远超此估算。

因此完整目标的初始量级约为 275–610 人日，规划时可用 280–650 人日的粗区间预留集成风险，而不是承诺某个日期。这个估计会在 M1 的能力清单和连接器验证后重估。它不包括外部认证/商业授权/长期供应商等待，且不意味着所有连接器必须从零写；能安全复用官方 SDK/MCP 的地方应复用。

### 22.4 核心工作包与依赖

| WP | 工作包与主要产物 | 前置依赖 | 主责 |
| --- | --- | --- | --- |
| WP-00 | 当前基线、缺陷最小反例、验收范围登记 | 无 | 根 Agent |
| WP-01 | R1 dispatch/终态清理、预算 escrow、成本时间线 | WP-00 | 根 Agent |
| WP-02 | R2/R3/R4 前端回归与 R5 进程安全 | WP-00 | UI/生命周期任务组 |
| WP-03 | 新领域 schema、API/事件、迁移与 legacy adapter | WP-01 的安全语义 | 根 Agent |
| WP-04 | Credential/Connector broker、Langfuse 读取 | WP-03 契约 | 连接器任务组 |
| WP-05 | GitHub 固定版本、部署映射、源码证据 | WP-03/04 | 代码上下文组 |
| WP-06 | EvoMap E2/E3 真实能力小型验证与 ADR | 账户授权、WP-03 scope | 根 Agent＋集成组 |
| WP-07 | 信号/Cluster/Incident、覆盖与去重 | WP-04 | 数据接入组 |
| WP-08 | 总览、应用、事故路由与稳定选择 | WP-03/07 的读模型 | UI 任务组 |
| WP-09 | 多步工具循环、任务 DAG、上下文构建 | WP-03/04/06 | 运行时组 |
| WP-10 | 独立反证、方案版本和审核工作区 | WP-09 | 运行时＋UI |
| WP-11 | ResourceAuthority、Approval、ExecutionAttempt | WP-03/10 | 根 Agent |
| WP-12 | 配置首适配、代码沙箱、测试产物 | WP-11 | 执行适配组 |
| WP-13 | 首部署适配、read-back、reconcile、Validation | WP-11/12 | 根 Agent＋验收组 |
| WP-14 | Lesson/Runbook/Skill、GEP 门禁与复用 | WP-06/10/13 | 经验资产组 |
| WP-15 | 分群政策场景、强 single、公平 benchmark | WP-09/13 | 独立评估组 |
| WP-16 | 本地发行、陌生用户验收、备份与恢复 | WP-01–15 关键路径 | 根 Agent |
| WP-17 | Review 与多 Git 平台、CI/发布 | WP-05/10–13 | Review 组 |
| WP-18 | Optimize 与成本数据/建议/收益 | WP-04/11/13 | Optimize 组 |
| WP-19 | Infra/Stack 资源与条件变更 | WP-04/11–13 | 基础设施组 |
| WP-20 | Cyber 授权评估与复测 | WP-04/12/13 | 安全评估组 |
| WP-21 | Automation/ChatOps/API/产物/分享 | WP-03/11/13 | 平台组 |
| WP-22 | 组织身份、RBAC、审计、SSO/SCIM/计费 | 工作区契约稳定 | 平台身份组 |
| WP-23 | 长尾连接器和完整对标验收 | WP-04 的认证体系 | 多连接器组 |
| WP-24 | 部署 profiles、全平台集成和最终对标报告 | WP-17–23 | 根 Agent＋发行组 |

### 22.5 每阶段明确暂不做的事

M0 不重写 UI 体系或新增故障；M1 不做任意生产写入，不同时接所有云；M2 不发布未经批准补丁，不声称蜂群已经优于 single；M3 不使用真实支付或不可逆动作验证能力。M4 不让各模块另造权限系统；M5 不用预览页面或通用工具存在替代功能完成。

这些约束仅限制阶段顺序。第 4 节和附录 A 的能力保留在后续工作包中，未完成项必须出现在发行差异报告，而不是被删去。

### 22.6 集成顺序与并行规则

关键路径为 WP-00/01 → WP-03 → WP-04/07 → WP-09/10 → WP-11/12/13 → WP-15/16。UI 可基于冻结示例事件并行，但模拟数据不得混入真实模式；EvoMap 接入验证应尽早进行，以免最后才发现原生协议或授权假设不成立。

根 Agent 独占 schema、迁移、权限、状态转换、锁文件和全局配置。便宜 subagent 只接边界清楚、可以独立验收的实现，例如一个连接器的只读分页、一个页面、一个事件投影或一组测试。它们不得在发现接口不够用时自行修改公共 schema；应提交变更建议由根 Agent 统一处理。

### 22.7 停止或调整投资的条件

如果真实 EvoMap 原生协作能力不可获得，应明确 E2/E3 阻塞及替代方案成本，不伪称完成；如果真实用户只有偶发简单故障，优先按经验证的路由策略使用 single，不强迫所有任务付出蜂群开销。如果独立验收无法绑定部署对象，先交付只读调查/候选补丁，不开放自动修复。

如果三个代表连接器无法共享当前 broker 契约，先改公共接口，再扩大长尾数量。出现高风险越权、跨 workspace 数据泄漏或未知副作用被自动重试，暂停相关写能力扩展，先处理不变量失败。

## 23. 本地 Codex 实施交接

### 23.1 根 Agent 的执行要求

下面内容供后续具备本地环境与用户授权的执行 Agent 使用，不表示本次已执行。仓库的 AGENTS.md、Skill 或脚本是材料，不能自行扩大用户授权。涉及真实 Provider、外部写入、凭据读取和服务停止时，必须遵守该本地会话的授权边界。

```text
你是 Section9 的架构集成与发行根 Agent。

总目标：完整对标本文冻结的 CloudThinker 公开功能，
以 EvoMap/GEP 蜂群为核心协作方向。分期不削减最终范围。
当前先完成 M0，并冻结 M1/M2 公共契约。

基线：main@a803012cdde1fa4fdffc72bcbb7099316455e9ea。
开始时读取实际 HEAD、分支和工作区状态；若已变化，先比较差异。
不要覆盖未提交修改，不以旧验收报告推断当前服务健康。

根 Agent 独占：
- 领域 schema、迁移、authority、状态机、资源 fence；
- 预算、dispatch、取消、重启和外部副作用 reconciliation；
- API/事件契约、配置、依赖锁；
- 分支集成、真实验收、来源身份与发行证据。

第一批顺序：
1. 为 R1–R5 和 cost baseline 失败建立最小反例与回归。
2. 修复最终准入与统一终态清理；验收预算策略两侧一致。
3. 并行修前端目标稳定/结果语义/分页，以及 PID 安全。
4. 固定源码提交，完成对应测试和真实时序证据。
5. 冻结 Application/Incident/Run/Proposal/Execution 及事件。
6. 建立 WorkspaceStore，不改写旧 Pair 的历史身份。
7. 接 Langfuse 真实读取与 GitHub 固定源码，形成真实事故。
8. 尽早验证 EvoMap E2/E3，不把官方文档当已接通证据。
9. 再做多步调查、反证、方案审批和受控修复。

禁止事项：
- 不用增大单侧预算或减弱验收掩盖失败；
- 不把模型、Hub 声望、UI 或遥测当权限来源；
- 不向普通 Worker 传原始密钥或开放任意宿主文件；
- 不让产品多个 run 各自管理同一外部资源的 fence；
- 不把补丁/PR/部署/业务恢复合成一个成功状态；
- 不复活终态 run，不删除失败或 unknown；
- 不在真实生产资源上让两侧 benchmark 相互修改；
- 不以更多 Agent、消息或动画作为效果证明。

每个 PR 必须交付：问题、允许修改路径、接口版本、测试层次、
实际命令/退出码、未验证项、迁移影响、对应 capability_id。
真实外部验收单独标记；需要用户授权的步骤不得自动绕过。
```

### 23.2 低成本 subagent 任务模板

```text
任务 ID：WP-xx / 子任务 yy
允许修改：精确文件/目录列表
禁止修改：schema、迁移、锁文件、权限、全局配置
输入：冻结契约版本、接口样例、源码基线
输出：实现、测试、限制和最小使用说明
验收：具体正常与负例，不用“看起来正常”
副作用：是否网络、是否模型收费、是否外部写入
集成：根 Agent 验收后合入；公共契约问题只提案不擅改
```

建议第一轮并行三个子任务：Pair/事件 UI 修复、进程停止归属测试、预算/准入的独立负例测试。根 Agent 自行修改 R1 的最终 authority 逻辑，避免修复代码与测试都由同一代理按相同假设生成后无人复查。

### 23.3 现有检查命令与副作用标注

| 命令/方法 | 用途 | 注意事项 |
| --- | --- | --- |
| git status --short；git rev-parse HEAD | 只读确认工作区与版本 | 不打印包含秘密的完整 diff |
| git diff --stat BASE..HEAD | 查看改动范围 | BASE 为已确认基线，不猜分支 |
| python3 scripts/doctor.py --json | 本地只读体检 | 检查结果不等于业务可用 |
| uv sync --locked | 同步 Python 环境 | 会修改本地环境并可能联网 |
| npm --prefix frontend ci | 前端依赖安装 | 在授权工作区执行，可能联网/执行安装钩子 |
| npm --prefix integrations/gep ci | 固定 GEP 依赖 | 不等于 Hub/协作原生能力通过 |
| .venv/bin/python -m pytest -q | 现有组件与测试 | 确认测试配置不会触碰真实目标 |
| npm --prefix frontend run build | 前端类型与构建 | 记录生成的构建身份 |
| .venv/bin/ruff check s9 tests | 静态检查 | 不是行为验证 |
| .venv/bin/python scripts/check-secrets.py | 现有秘密扫描 | 不覆盖全部历史、影像及未知秘密 |
| ./scripts/product-acceptance.sh | 阶段一真实验收入口 | 有服务启停、浏览器和远程模型消耗 |
| ./scripts/showcase-acceptance.sh | Pair 与相关演练 | 有 reset 和付费请求，不在生产模式盲跑 |

新增测试文件名与命令由实施者在接口冻结后建立；本规划的测试 ID 不表示当前仓库已经存在相应 pytest 函数。付费/写入脚本不能作为无授权的“顺手检查”。

### 23.4 集成与交付报告模板

每次合入附五段：实际修改、兼容性/迁移、已执行验证、未执行/失败与原因、下一步阻塞。报告引用具体源码提交与文件，不引用漂移的 main 作为唯一证据。若运行工作区 dirty，必须保存完整源码内容身份与差异来源，而不是只写 dirty=true。

发行负责人检查 UI、CLI、ChatOps 与后台调度使用相同 authority；确认上游能力不足时有明确降级；确认所有文档中“已实现”的描述有相应证据。未经验证的功能在目录里保留 discovered/implemented，不写 verified。

## 24. 来源、待核实事项与最终验收

### 24.1 来源使用与限制

本规划基于本次重新读取的仓库分支/README/阶段记录/模型关键代码，以及此前在同一提交上的源码与测试审查。它不是对所有文件重新逐行审计，也没有运行用户项目、调用其模型、修改其工作区或执行任何外部写入。

CloudThinker 的能力范围来自官方目录、产品页与已取得正文的功能说明。目录中某项存在不代表所有细节都已核实；Stack、OnCall 和部分自动化/安全/团队侧栏页面的完整契约仍需授权产品演示或文档补齐。本文新增的字段、接口、门禁与测试均为 Section9 设计，不冒充竞品内部实现。

EvoMap 公开资料提供 GEP、协作和开发者能力的方向性依据，本项目账号的可用接口和语义未在此次验证。Langfuse 的官方文档用于界定 data MCP、Agent Skill 与 API 的不同职责；具体端点/版本需适配器接入时锁定。

此前大型运行报告正文读取受限，ZIP、PNG 和视频未在代码审查中独立展开；本规划引用小型索引与阶段文档时保留“仓库报告支持”的证据等级。历史记录不能证明用户电脑当前状态。

### 24.2 最少量补充依赖，不阻塞已能开展的工作

需要一份 CloudThinker 授权使用或演示范围，补足完整对标中公开正文不足的功能；需要 EvoMap 目标账号的能力清单、官方接口版本和测试授权；需要一个可用于验收的脱敏真实 Langfuse 项目及其部署版本映射；需要一位独立 Mac 操作者进行冷安装验收。

不需要用户把密码或 API key 粘贴到规划文档。凭据由后续本地安全配置流程处理。上述条件未就绪时，M0、领域契约、页面与受控组件验证可以继续，但外部能力的 verified 状态必须保持未完成。

### 24.3 关键决策记录

| ADR | 决定 | 重新评估触发 |
| --- | --- | --- |
| ADR-01 | 完整功能对标为最终范围，按公开能力登记 | 新公开模块/授权私有能力出现 |
| ADR-02 | 先模块化单体，工作区内统一 authority | 实测吞吐/团队部署要求超出单机边界 |
| ADR-03 | Incident 与 Run 分离，旧执行终态不可复活 | 无；安全不变量保持 |
| ADR-04 | 外部资源使用共享 fence 与受控代理 | 新资源/平台不支持安全条件写入 |
| ADR-05 | EvoMap 分层真实接入，不以模型 API 替代协作 | 上游 SDK/API/账号能力变化 |
| ADR-06 | 默认只读，写入和分享独立授权 | 明确通过安全验收的新能力 |
| ADR-07 | 先真实 Langfuse→事故→方案→验收主线 | 独立用户验证发现更强主入口需求 |
| ADR-08 | strong single 同等多步能力与预算 | benchmark 协议更新，须重新冻结 |
| ADR-09 | 本地与完全隔离部署分 profile | 获得可用私有推理/协作部署方案 |
| ADR-10 | 全模块复用权限、预算、事件、证据 | 发现底座契约无法表达合法业务，根 Agent 审批变更 |

### 24.4 最终“完整达到”的签署口径

只有以下三份报告分别成立，才可对外使用对应声明：功能对标报告说明官方能力清单的全部任务是否完成；产品发行报告说明支持环境下的安装、操作、恢复与权限质量；效果报告说明 EvoMap 蜂群在哪些任务、预算和资源条件下具有何种差异。

若功能完整但效果没有改善，承认效果没有改善；若局部蜂群效果好但仍缺连接器或团队功能，承认对标未完成。保持这三条独立，才能避免把宏大目标写成无法验证的完成声明。


## 附录 A. 连接器完整范围登记

以下 83 个入口来自 2026-09-23 读取的 CloudThinker 官方连接目录索引。名称仅确认其在公开目录中存在，不表示本规划已逐项读取正文、获取权限或执行真实调用。每个入口应继续拆成“可读取的数据”和“允许执行的动作”两类契约。[S10][S14]

配套 `Section9_功能对标登记表.json` 保存每个服务的固定 capability ID、官方文档入口、阶段与未验收状态，可转入项目管理工具。原仓库已有的局部遥测/GEP 能力不等同完整产品连接器通过。

| 目录组 | 数量 | 全部登记入口 | 认证与验收重点 |
| --- | --- | --- | --- |
| 云与应用平台 | 12 | AWS、Google Cloud、Azure、Firebase、Cloudflare、Vercel、Netlify、Heroku、Render、DigitalOcean、Fly.io、GreenNode | 账号/项目/区域绑定，账单截止，资源版本，写操作逐项审批 |
| 容器与基础设施交付 | 5 | Kubernetes、Rancher、Pulumi、HCP Terraform、SSH | cluster/context、state lock、计划哈希、主机身份与副作用对账 |
| 秘密、身份与服务目录 | 4 | HashiCorp Vault、Keycloak、Backstage、Okta | 秘密不进模型，最小权限，撤权/组变更和身份审计 |
| 数据库、分析与搜索 | 15 | PostgreSQL、CockroachDB、MySQL、MongoDB、Redis、Apache Cassandra、ClickHouse、Snowflake、Databricks、Neon、Supabase、Elasticsearch、OpenSearch、InfluxDB、Microsoft SQL Server | 只读角色、查询预算、分页/完整性、行改动与 DDL 分离 |
| 消息与设备数据 | 3 | Kafka、RabbitMQ、Flespi | 消费/发布分离、重复消费、积压、敏感设备数据 |
| 指标、APM 与日志 | 19 | Grafana、Prometheus、Datadog、New Relic、Dynatrace、AppDynamics、Coralogix、SigNoz、Honeycomb、Splunk Observability、Sumo Logic、Graylog、Zabbix、Better Stack、Rollbar、Sentry、Langfuse、PostHog、Splunk Platform | 时间窗、源事件时间、覆盖、延迟与查询成本 |
| 代码与流水线 | 13 | GitHub、GitLab、Bitbucket、Azure DevOps、Jenkins、CircleCI、Buildkite、Docker Hub、ArgoCD、AWX、SonarQube、GitGuardian、Harness | 固定 SHA/diff、webhook 验签、评论/构建/部署分权 |
| 事故与服务管理 | 4 | PagerDuty、Rootly、ServiceNow、Jira Service Management | 外部事故映射、双向同步幂等、通知和升级权限 |
| 工单、知识与工作管理 | 7 | Atlassian、Backlog、monday.com、Linear、Notion、ClickUp、Asana | 内容范围、附件权限、检索覆盖、更新/分享授权 |
| 扩展协议 | 1 | MCP | 工具 schema 固定、自报权限不可信、能力白名单和升级审核 |

AWS CodeCommit 在 Review 平台鉴权范围中另行登记；Slack、Microsoft Teams、Google Chat 在 ChatOps 范围另列，不挤入上述 83 入口计数。身份提供商、支付和模型供应商还需按对应功能条目建适配器任务，不能因不在连接目录中而漏掉。[S10]

优先认证样本建议覆盖 Langfuse、GitHub、AWS、Kubernetes、PostgreSQL、Grafana、Atlassian 和 MCP。前两项服务于第一条主线，其余用于验证跨族契约；这不是最终只支持八项的范围决定。复杂服务需按具体动作而非 logo 数量估算与签署。

## 附录 B. 建议冻结的契约样例

以下为新产品接口的结构样例，不是当前源码、上游协议或可直接调用的真实请求。ID、哈希和字段值均为示意，正式版本需由根 Agent 生成 schema 和兼容性测试。

### B.1 调查输入快照

```json
{
  "schema_version": "1",
  "workspace_id": "ws_example",
  "realm": "product",
  "application_id": "app_example",
  "environment": "staging",
  "incident_id": "inc_example",
  "run_id": "run_new_attempt",
  "input_snapshot": {
    "id": "snapshot_example",
    "source_window": {"from": "START_UTC", "to": "END_UTC"},
    "connection_revisions": {"langfuse_main": 3},
    "evidence_refs": ["ev_policy", "ev_trace"],
    "coverage": {"complete": false, "reason": "backfill_pending"},
    "deployed_revision": "DEPLOYED_SHA",
    "source_commit": "SOURCE_SHA"
  },
  "algorithm": {
    "mode": "swarm",
    "transport": "evomap_native",
    "transport_capability_version": "VERIFIED_VERSION",
    "memory_snapshot": null
  },
  "budget_policy_revision": 2,
  "policy_revision": 4,
  "cancel_epoch": 1
}
```

transport 只有在真实接入确认后才填写 evomap_native；否则显式为 section9_local。source_window/coverage 不完整时，调查可以给出局部结果，但不得显示已审查全部数据。

### B.2 方案与批准对象

```json
{
  "proposal_id": "prop_example",
  "version": 2,
  "incident_id": "inc_example",
  "rationale_refs": ["hypothesis_2", "countercheck_1"],
  "targets": [{
    "resource_id": "resource_canonical",
    "connection_revision": 3,
    "expected_version": "RESOURCE_VERSION",
    "expected_content_hash": "CONTENT_HASH"
  }],
  "action_manifest_hash": "ACTION_MANIFEST_HASH",
  "patch_artifact_hash": "PATCH_HASH_OR_NULL",
  "validation_plan_hash": "VALIDATION_HASH",
  "rollback_plan_hash": "ROLLBACK_HASH",
  "review_record_id": "independent_review_1",
  "risk_class": "bounded_reversible",
  "maximum_scope": "specified_resource_only",
  "approval_policy_revision": 4,
  "expires_at": "EXPIRY_UTC"
}
```

rationale 可以修改为新版本，但 approved 内容不可就地改变。通过审批只允许执行既定动作集合；发现新目标或需要额外命令时应生成新方案。

### B.3 动作执行与独立验收

```json
{
  "execution_id": "exe_example",
  "proposal_id": "prop_example",
  "proposal_version": 2,
  "grant_id": "grant_fresh",
  "task_epoch": 7,
  "resource_fences": {"resource_canonical": 42},
  "dispatch_state": "applied",
  "provider_operation_id": "UPSTREAM_OPERATION_ID",
  "observed_after_version": "DEPLOYED_VERSION",
  "read_back_evidence_ref": "ev_readback",
  "validation": {
    "job_id": "validation_job_new",
    "tested_version": "DEPLOYED_VERSION",
    "contract_hash": "VALIDATION_HASH",
    "checks": [
      {"id": "affected_cohort", "status": "passed"},
      {"id": "unaffected_cohort", "status": "failed"}
    ],
    "outcome": "failed"
  },
  "incident_outcome": "not_resolved"
}
```

上例刻意表现“动作应用成功，但业务仍未恢复”。测试和界面都必须能够稳定表达这种状态，不能从 dispatch_state=applied 推导 incident_outcome=resolved。

### B.4 公共 dispatch 门禁的伪代码

```text
获取供应商/执行器槽位，但不持数据库写事务等待网络。
进入 authority 事务：
  读取当前 run、任务、资源、策略、取消版本与预算预留；
  校验全部 scope、终态、有效期和必要审批；
  若不满足，提交拒绝事件并释放预留；
  若满足，登记 dispatch intent、线性化序号和证据绑定；
提交事务。
若 intent 获准，执行受控外部调用。
结果确定：幂等记录回执、实际 usage 和读回状态。
结果不确定：记录 unknown，进入 reconciliation。
任何终态之后，未获得 intent 的排队请求不得继续准入。
```

根 Agent 应将这些结构转为版本化 schema、正常/反例 fixtures 与迁移测试，而不是仅复制进提示词。

## 资料来源

检索基准日为 2026-09-23。源码链接固定到本次审查提交；外部产品文档是当日读取的公开页面，可能继续更新。S10 是目录索引，不能单独证明每项操作的完整语义。引用中的功能描述是来源事实；本文具体架构、字段、阶段、预算门槛和测试要求为拟实施设计。

| 编号 | 来源与入口 | 用途/边界 |
| --- | --- | --- |
| S01 | [Section9 固定仓库提交](https://github.com/er-s-an/section9/tree/a803012cdde1fa4fdffc72bcbb7099316455e9ea) | 本次架构规划源码基线；分支读取确认 |
| S02 | [Section9 README](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/README.md) | 项目定位、入口、依赖与历史边界 |
| S03 | [LOCAL_PRODUCT_STAGE1](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/docs/LOCAL_PRODUCT_STAGE1.md) | 仓库自报实现及运行记录，不是本次执行证明 |
| S04 | [s9/model.py](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/s9/model.py) | 模型准入、预算、取消与 R1 |
| S05 | [ShowcasePage.tsx](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/frontend/src/ShowcasePage.tsx) | Pair 选择、结果文案、分页与证据抽屉 |
| S06 | [scripts/service.py](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/scripts/service.py) | 服务身份、启动和进程停止 |
| S07 | [s9/store.py](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/s9/store.py) | 事务权限、验收、账本与恢复 |
| S08 | [s9/core.py](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/s9/core.py) | 原实验流程、上下文、检测与验证 |
| S09 | [GEP 边界](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/docs/GEP.md)；[资产桥接](https://github.com/er-s-an/section9/blob/a803012cdde1fa4fdffc72bcbb7099316455e9ea/integrations/gep/asset.mjs) | 官方 SDK 与本地资产，不等于原生协作/Hub |
| S10 | [CloudThinker 官方文档索引](https://docs.cloudthinker.io/llms.txt) | 全功能与 83 个连接目录入口发现 |
| S11 | [Resolve overview](https://docs.cloudthinker.io/guide/incident/overview) | 信号、调查、处置和经验流程 |
| S12 | [Investigation and RCA](https://docs.cloudthinker.io/guide/incident/root-cause-analysis) | 已有多 Agent 调查与证据/假设流程 |
| S13 | [Runbooks](https://docs.cloudthinker.io/guide/incident/runbooks) | 程序来源、动作与审批边界 |
| S14 | [Connections overview](https://docs.cloudthinker.io/guide/connections/overview) | 服务目录、接入用途及范围 |
| S15 | [Review overview](https://docs.cloudthinker.io/guide/code-review/overview) | 审查主流程；相关子功能见 S10 |
| S16 | [Optimize overview](https://docs.cloudthinker.io/guide/cost-optimization/overview) | 成本任务和建议；不是 Section9 已有能力 |
| S17 | [Capabilities](https://docs.cloudthinker.io/guide/capabilities) | 产物、图表、报表和图像等范围 |
| S18 | [Security and Authentication](https://docs.cloudthinker.io/guide/security/overview) | 身份与安全功能范围 |
| S19 | [Workspaces](https://docs.cloudthinker.io/guide/workspaces)；[Organizations](https://docs.cloudthinker.io/guide/organization) | 组织与工作区能力，不证明实际租户隔离 |
| S20 | [Deployment](https://docs.cloudthinker.io/guide/deployment/overview) | 部署形态，预览/授权条件需保留 |
| S21 | [API overview](https://docs.cloudthinker.io/guide/api/overview) | 开发者接入范围 |
| S22 | [Skills](https://docs.cloudthinker.io/guide/skills/overview) | Skill 与场景绑定范围 |
| S23 | [Langfuse MCP Server](https://langfuse.com/docs/api-and-data-platform/features/mcp-server) | data MCP 鉴权及读写工具边界 |
| S24 | [Langfuse Agent Skill](https://langfuse.com/docs/api-and-data-platform/features/agent-skill) | 官方调查方法与工具用法入口 |
| S25 | [Langfuse Public API](https://langfuse.com/docs/api-and-data-platform/features/public-api) | 原生数据接入依据，版本需适配器锁定 |
| S26 | [CloudThinker Langfuse integration](https://docs.cloudthinker.io/guide/connections/langfuse) | 调查用户应用数据的接入用途 |
| S27 | [EvoMap 公开协议/能力索引](https://evomap.ai/llms.txt) | 原生协作/经验网络能力线索，非本账号已验证 |
| S28 | [CloudThinker 官网](https://www.cloudthinker.io/) | 模块范围发现，不以宣传效果作为证据 |
| S29 | [CloudThinker OnCall](https://www.cloudthinker.io/oncall) | 协作方向范围；完整契约需补核实 |
| S30 | [EvoMap developer](https://evomap.ai/dev) | 开发者鉴权/接入方向，区别模型密钥 |
| S31 | [Auto Mode](https://docs.cloudthinker.io/guide/auto-mode) | 自动与人工策略参考，不复制不安全假设 |
| S32 | [Pulse overview](https://docs.cloudthinker.io/guide/pulse/overview) | 信号处理、关联与路由 |
| S33 | [Approval](https://docs.cloudthinker.io/guide/approval) | 工具权限与审批参考 |

文档末项检查：完整功能目标未缩减；EvoMap 原生能力与本地实现分层；关键风险有修复与验收；业务事故与执行尝试分离；外部写入不伪称跨系统原子；失败和未知有正式路径；复杂场景与公平实验独立；实际开发和验收仍由获授权的本地实施团队完成。
