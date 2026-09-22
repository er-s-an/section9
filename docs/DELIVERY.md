# Section 9 交付说明

仓库绝对路径：`/Users/xiejiachen/Documents/ChatGPT/rebuild/section9`。

2026-09-23 外部应用产品增量（support-agent、G0–G5 状态、当前可复跑命令及阻塞）以[交付矩阵](PRODUCT_DELIVERY_MATRIX.md)和[外部应用演示/运维指南](PRODUCT_EXTERNAL_APP_DEMO.md)为准。下方的本地实验室与海报/录像记录是更早的 Section9 证据，不能替代本轮外部项目验收。

启动与验收：

```sh
cd /Users/xiejiachen/Documents/ChatGPT/rebuild/section9
./scripts/start.sh
./scripts/acceptance.sh
./scripts/reset.sh
```

`9019` 是本机 operator UI/API。当前机器的模型凭据已配置在被 Git 忽略的受保护文件中；模型调用实际走远程 `evomap-gpt-5.6-luna`，不代表本地推理或断网可用。

当前正式 evaluation 共 36 次：`single` 8/8、`muted` 7/8、`swarm` 12/12（3 次/格）、`memory` 8/8，35 pass、1 个 muted/composite fail。失败 run、events、usage 和 manifest 均保留。`memory_jev` 因 Jev disabled 保持待测。新增 4 次 swarm 的耗时为 18.963、19.932、21.215、21.221 秒；小样本只支持描述性报告，不能推出不同条件的优势。

浏览器 acceptance 已真实检查 L0 写入拒绝、L1 精确审批、OS pause/takeover、旧 grant 拒绝、reset 使旧 grant 失效、通信切换、run detail/evidence drawer。browser-extra 还检查了 cost specialist、reset 取消旧 tool loop，以及 Collector degraded/recovered。四类 integration 检查多次通过。

Memory demo 单独记录，不与 frozen evaluation 混算。官方 `@evomap/gep-sdk` 用于 GEP asset hash/schema 协议，本地 authority 负责控制和审计；这不冒充 EvoMap 官方 native swarm。Hub 授权仍 pending，Jev 未启用。原 AgentMED 只复制复用，未修改、未 push。

Attract 已完成 3 个真实周期：19.867、21.611、20.710 秒，总运行 278.831 秒。三轮业务结果、主配置/事故/evaluation score/memory 不变、自动调用停止和独立 isolation checks 均通过；共享同一台 Mac，非硬件隔离。memory UI 两次复用耗时 19.157/18.481 秒，2/2 成功，reset 0.063 秒。离线 OS 阻断为真实 ConnectError，usage 为 null/unknown，本地控制状态与 memory 可读但不代表本地推理。

冷启动依赖/镜像缓存下已实测：operator 15.003s、依赖全绿 34.672s、真实 healthy 客服 38.035s；`artifacts/startup/report.json` 记录该结果，首次镜像下载未测。Telemetry 旧 LF-only 记录不证明 Collector→控制端贯通；最终报告 `artifacts/telemetry/telemetry-check-20260921T210738Z.json` 已核对 8 个相同 trace_id 两侧入库；修复后的最终浏览器复合故障用时 19.800 秒，全部独立业务检查通过。成员资料、Hub 授权和商业素材权利仍是用户事项。Star Office 归因必须同时引用 `docs/STAR_OFFICE_SOURCE_AUDIT.md` 与 `docs/STAR_OFFICE_ATTRIBUTION.md`：代码/逻辑按 MIT 说明，美术资产仅限非商业示范，不得把全部素材写成 MIT。A3 海报和 90 秒原速历史录屏位于 `artifacts/delivery/`，录屏字幕不是实时字幕。

## 可复查证据

总索引：[evidence-index.json](../artifacts/delivery/evidence-index.json)。原始报告、运行记录、模型用量、关键截图和视频均保留在 `artifacts/`。组件与安全边界检查 **55 passed**；真实浏览器运行单独保存，不以组件测试替代业务证明。

- 最新完整页面与逐次记录：[final-browser](../artifacts/final-browser/)；首页截图 `06-ready-home.png`，笔记本截图 `05-laptop-1280.png`。
- 权限 / fencing / reset：[2026-09-21T20-48-59.445Z/report.json](../artifacts/browser-acceptance/2026-09-21T20-48-59.445Z/report.json)。
- 成本专家 / 旧循环取消 / Collector 故障恢复：[2026-09-21T20-50-16.591Z/report.json](../artifacts/browser-extra/2026-09-21T20-50-16.591Z/report.json)。
- 复合故障只修一半拒绝结案、配置变化后拒绝结案：`artifacts/live-safety/`。
- [A3 海报](../artifacts/delivery/section9-A3-poster.pdf)、[90 秒历史录像](../artifacts/delivery/section9-90s-replay.mp4)、[7 分钟演示指南](DEMO.md)。

## 实测与边界

| 条件 | 成功 / 样本 | 闭环范围 | 中位耗时 | 已报告 token |
|---|---:|---:|---:|---:|
| 单 Agent | 8 / 8 | 11.669–14.152 秒 | 12.828 秒 | 35,427 |
| 禁言 | 7 / 8 | 11.857–17.054 秒 | 13.740 秒 | 42,077 |
| swarm | 12 / 12 | 17.328–21.221 秒 | 19.160 秒 | 68,847 |
| swarm + Playbook | 8 / 8 | 15.908–21.290 秒 | 19.237 秒 | 46,739 |

正式样本共 193,090 provider tokens；未报告金额，不把 Langfuse 未配置价格时的 $0.00 当作免费。正式样本没有 unknown usage；单独的断网实验保留了 unknown usage。首次异常观测的实测范围为 1.214–4.729 秒。Playbook 的两个交互演练低于 20 秒，但正式记忆组并非每次都低于 20 秒，不能承诺稳定秒修，也没有蜂群优于单 Agent 的证据。

保留了 1 次正式禁言复合故障失败、早期模型校准失败、一次成本专家演练的预算预留争用失败，以及测试工具定位错误。争用和 Collector gzip 接收问题修复后分别复跑成功，历史失败没有改写成通过。旧测量来自较早实现快照；新增运行带 implementation_hash；它们用于描述性验收，不做严格受控的速度优越性结论。

已实现并验证的是本地闭环、权限、协作隔离、复核与证据。降级边界是远程模型断连时只能读取本地界面/历史/记忆，无法完成模型驱动修复；进程与数据隔离共享物理 Mac。受外部条件阻塞的是 Hub OAuth 与真实发布、Jev 真实产物/运行条件，以及正式提交所需成员信息。本验收记录生成于首次 GitHub 上传前；GitHub 源码上传不代表公网服务部署或比赛提交。

## GitHub 与办公室改版（2026-09-22 上午）

首个源码提交 `55e8b3e` 已上传 https://github.com/er-s-an/section9 的 main。后续办公室改版、截图、浏览器脚本和本地初始化说明均随本轮增量提交；详细验收与范围见 [OFFICE_DELIVERY.md](OFFICE_DELIVERY.md)。GitHub 上传不是公网运行部署。原海报和 90 秒录屏展示的是上传前旧办公室布局，作为历史闭环记录保留；当前办公室截图以新记录为准。
