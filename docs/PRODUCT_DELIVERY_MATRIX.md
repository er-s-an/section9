# Section9 外部应用产品交付矩阵

记录日期：2026-09-23。范围是本机 macOS Apple Silicon 的 Section9 本地测试环境、首个外部候选 `support-agent`、Langfuse 只读 trace 与本地协作；不是公网服务、生产修复或官方 EvoMap 远端会话声明。

状态含义：**PASS** 只表示对应列出的范围有本轮证据；**PARTIAL** 表示已验证子集且仍有差距；**BLOCKED** 等待外部授权/设备/平台能力；**NOT_RUN** 表示没有执行。替身测试、保存的旧记录和当前真实服务分别注明，不能互相替代。

## G0–G5 产品蓝图

| 门槛 / 要求 | 实现位置 | 本轮验收命令或入口 | 证据 | 结果与阻塞 |
|---|---|---|---|---|
| G0 外部项目、manifest、版本、模型与权限边界 | `integrations/support_agent/manifest.py`、`s9/product/`、`s9/connectors/`、`scripts/provision_support_agent_adapter.py` | 产品工作区“接入并核验版本”；闭环报告中的 connect；候选目录运行 `pytest -q` | `closure-runs/delivery-v1-run01-reset-20260923.json`、`delivery-v1-run02-service-restart-20260923.json`；candidate local HEAD `703b291799a5baeeb6ff975b035d53780596d216` | **PASS（本机候选）**。上游基线 `860a29ff80f6e9b866e4cf1098c042e0da425f24`，MIT；适配器单独记录，`langchain-openai 1.6.3`。`/health` 实测；`/ready` 返回 404，明示不支持。未验证其它项目的零源码 UI 接入，也未测凭据撤销/限流矩阵。 |
| G1 真实业务、trace、数据范围和证据 | `s9/connectors/http_app.py`、`s9/connectors/langfuse.py`、`s9/product/service.py`、manifest 中的两个只读样本 | `sh scripts/product-acceptance.sh --model-token-budget 16000`；产品工作区订单/退款探针 | 三份 closure 报告、incident `g1-business-probe.json`、`g3-recovery.json`、Langfuse observations | **PASS（本地种子数据）**。订单 1001 只读状态、退款资格 1002（eligible=false）；未执行付款、退款或发信。其它客户数据、未知 tool args 和生产 trace 未测试。 |
| G2 真实代码定位、独立复现/补丁/验收 | `s9/product/regression.py`；保护测试 `integrations/support_agent/regression_test.py`；修复由候选上游 `708fd1d` 的 `src/agents/base.py` 提供 | closure 中 regression；upstream candidate 目录的完整 pytest | closure 报告内 baseline/candidate/full-suite/protected-path checks | **PARTIAL**。实际重复工具调用 fallback 回归在受保护测试中表现为旧版失败、候选通过；测试以确定性 fake model 重放代码路径。它不是自然发生的客户生产事故，不能写成“已修复线上故障”。外部模型接入改动与该修复分开。未测复合故障只修一半、其它路由和正常数据全集。 |
| G3 具体批准、执行、对账和业务恢复 | `s9/product/service.py`、`s9/product/models.py`、产品审批/执行/观察 UI | 三次 closure；产品工作区按钮；配置变更/重启/停止负例 | `delivery-v1-run01-reset-20260923.json`、`delivery-v1-run02-service-restart-20260923.json`、`acceptance-20260922T213107Z-65002.json`；浏览器 `product-workspace-g3-recovered-final.png` | **PASS（本地测试环境）**。批准绑定配置/source；批准后配置变化被 409 拒绝；重复同键回放同一 attempt，不同审批冲突被 409 拒绝。恢复条件为 5 次 `/health` 检查 + 一个业务真值探针 + source/config 仍匹配；三轮观察分别 4.299/4.220/4.348 秒，不代表长期稳定、生产恢复或回滚演练。 |
| G4 协作、反证、经验、失联接管与官方能力 | `s9/workers.py`、`s9/store.py`、`s9/api.py`、`scripts/verify-product-collaboration.py` | 三个真实本地协作 run 的只读复核 | `g4-local-collaboration-final-v2.json`；run `run_5f13bf02f1754ddf`、`run_7c96e8205ce64435`、`run_f53cb04389b24031` | **PARTIAL**。本地结构化消息、通信禁言、旧 epoch fencing/接管、批准经验复用分别在预期场景中观察到；scenario-specific checks 全过。通用反证目录状态仍为 unknown。官方 native session、目录发现、经验交换为 unsupported，未登录/未认证/未发布，不伪造官方能力。 |
| G5 安装、启停、身份/隔离、预算、备份恢复 | `scripts/install.sh`、`scripts/start.sh`、`scripts/stop.sh`、`scripts/verify-linux-container.sh`、`s9/product/lifecycle.py` | `scripts/product-acceptance.sh`；Linux disposable container harness；`./scripts/check-clean-start.py`（独立配置，无 provider） | `g5/backup-verification-delivery-v1-postfix.json`、`g5/linux-container-cold-install-*.log`、`artifacts/audit-remediation/clean-start-1790113050948515000.json`、`failures/linux-runtime-requires-macos-sandbox.json`、`failures/acceptance-backup-cli-import.json` | **PARTIAL**。macOS 本机服务实际运行；空凭据、隔离数据的新 checkout 启动、source 变化 identity 拒绝和安全停止通过。worker 身份/跨项目 scope/错误 Origin/未绑定批准均 403。Linux 冷装完成安装、前端构建、32 个产品/connector 测试；真实 Linux 服务启动被明确阻断：核心 workers 目前依赖 macOS `sandbox-exec`，没有安全等价的 Linux sandbox。独立物理机为 **NOT_RUN/BLOCKED**。修正 CLI import 后备份快照完整性、246 records/241 events、恢复数据库及所有文件哈希通过；不证明快照与随后变化的 live DB 字节相同。 |

## 原 PRD P0/P1 对照

| PRD 项 | 本轮要求简述 | 检查/证据 | 当前状态 |
|---|---|---|---|
| FR-1 P0 基础设施 | 冻结依赖、可安装、启停、reset、观测组件 | `pytest -q`、前端 build、`scripts/product-acceptance.sh`、Linux 冷装日志 | **PARTIAL**：macOS 本机通过；首次 Node 22 Docker Hub pull 超时；缓存 Alpine 的安装/build/tests 通过，Linux 运行受 macOS 沙箱依赖阻断。冷启动 ≤3 分钟未满足/未声明。 |
| FR-2 P0 外部业务/确定真值故障 | 真实 Agent 业务探针与可复现修复 | 三次 closure：订单 1001、退款资格 1002、G2 protected regression | **PARTIAL**：两类只读业务真值及一项受保护工具循环回归通过；PRD 四类 chaos 故障与复合跨视野故障没有全部实施。 |
| FR-3 P0 蜂群、fencing、独立业务判定 | 限权角色、接管后旧执行者拒绝、外部真值为结案标准 | G4 `swarm` run、原有安全测试、三次业务恢复回执 | **PARTIAL**：本地 fencing/业务 truth 有证据；完整 PRD 复合故障全自动闭环未证明。 |
| FR-4 P0 EvoMap 官方集成 | 官方原语、真实账号和会话 | `g4-local-collaboration-final-v2.json` `official_remote_status` | **BLOCKED**：本地 GEP 协作不是官方 Hub/A2A 会话；最小解锁是用户提供官方参赛身份授权与必要的 session/discovery 权限，再单独验收。 |
| FR-5 P1 经验复用 | 已批准经验检索、重新验证、复用 | memory run 与 G4 v2 报告 | **PARTIAL/PASS（本地样例）**：一个批准经验在限定场景复用成功；未证明跨组织发布、官方经验交换或 20 秒稳定秒修。 |
| FR-7 P0/P1 呈现层 | 像素办公室、真实状态、可操作产品页与追溯 | Ego Browser `product-workspace-final-running.png`、`product-workspace-g3-recovered-final.png`、`runtime-stopped-history-labels.png`、`runtime-stopped-probe-refused.png` | **PARTIAL**：真实 API 驱动接入/事故/审批/恢复工作区已操作；停止态截图显示 runtime 已停止、能力为“未知”并注明历史状态；最终运行截图显示 runtime running，而 readiness 不支持使连接中心降级。全页面笔记本/键盘/所有空态和其他 route 的新一轮可用性回归未完成。Star-Office 资产许可仍按 `docs/STAR_OFFICE_ATTRIBUTION.md` 限定。 |
| FR-8 P1 实验与证据 | 公平基线、消融、多次样本、失败保留 | G4 v2 三个 scenario reports；旧 evaluation 记录 | **PARTIAL**：本轮闭环三次是可复跑性检查，不是性能比较；没有新的完整五行多次消融，不声称 swarm 优于 single。 |
| FR-9 P0 演示工程 | 真实办公室事件、降级展示、演示路径 | 当前浏览器产品页；现有 `docs/DEMO.md` | **PARTIAL**：产品核心流程可操作；Open Hour 彩排/团队讲解、三种故障降级演示不在本轮已测范围。 |
| FR-10 P0 提交物 | 比赛提交页面、队员信息、截止交付 | 无本轮提交行为 | **NOT_RUN / 不在本地产品验收授权范围**。 |

## 独立复核与失败保留

只读独立 Agent 最终复核记录见 `artifacts/external-support-agent/independent-review-20260923.md`。复核者对 Section9 做了 GET-only 快照读取，并检查三份 closure、G4 v2、G5 postfix、clean-start 报告及相关源码；未 POST、运行模型、直连 DB 或写状态。其确认三份 closure 的最终 checks、五点恢复窗口及 G4 场景级检查；指出 run01 closure 的 aggregate usage 字段被脱敏，故不能单独从该字段复核总量。补充文件 `closure-runs/delivery-v1-run01-usage-summary.json` 是从保留的三项响应 usage 重建的 2,110 tokens，原报告保持不变。复核者自己的 GET 快照看到 runtime running，因此没有在该次 GET 中触发 stopped UI；独立保存的 Ego Browser 截图 `browser/runtime-stopped-history-labels.png` 显示 stopped/unknown 状态，最终运行截图 `browser/product-workspace-final-running.png` 则显示当前 runtime running、连接降级（readiness unsupported）。

复核仍确认以下限制：固定 `local-console-operator` 不是个人身份认证；G4 通用 contradiction review 为 unknown、官方 native session/discovery/exchange 为 unsupported；Linux 服务运行被 macOS sandbox 依赖阻断；独立物理机未运行。备份结论只覆盖保存快照还原与完整性，不是 live DB 字节相等。首次 acceptance wrapper 的 G5 backup CLI import 失败保留于 `failures/acceptance-backup-cli-import.json`；修复后 backup/restore/hash/integrity 对唯一新目标单独复测通过，未为此再次调用模型。该 Agent 是复核者而非重新执行三次 closure 的复跑者。

失败与阻塞不删除，保存在 `artifacts/external-support-agent/failures/` 和 `artifacts/external-support-agent/g5/`。关键截图位于 `artifacts/external-support-agent/browser/`。证据源中“ready”只表示其捕获时点的状态；候选运行时停止后，产品 UI 会把历史能力降为未知并注明时间。
