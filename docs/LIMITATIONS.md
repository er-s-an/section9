# 当前限制与未完成验收

以下状态按证据区分，不把代码路径、测试或演示素材写成生产事实。

| 项目 | 当前状态 |
|---|---|
| 本地控制/API | 最终 acceptance 已通过；9019 operator、9021 worker 的浏览器和状态机检查均有报告 |
| Langfuse/Collector | 旧 LF-only 记录不证明 Collector→控制端贯通；最终同 trace_id 双侧报告已验证 8 条；见 `artifacts/telemetry/telemetry-check-20260921T210738Z.json` |
| 模型 | 远程 EvoMap Luna 推理；不是本地模型，也不是全断网证明 |
| Jev | disabled；第五 evaluation 行 `memory_jev` 保持待测/disabled |
| EvoMap Hub | 不注册、不 publish；Hub OAuth pendingauth |
| Evaluation | 共 36 次正式运行：single 8/8、muted 7/8、swarm 12/12、memory 8/8，35 pass、1 个 muted/composite fail；`memory_jev` 因 Jev disabled 保持待测；小样本不能推出条件优势 |
| Attract | 9020/9022 独立进程与 database/memory 边界；3 次闭环 19.867/21.611/20.710 秒，总运行 278.831 秒；自动调用已停止，共享同一 Mac，非硬件隔离 |
| 冷启动 | 依赖/镜像缓存下停掉本项目容器再启动：operator 15.003s、依赖全绿 34.672s、真实 healthy 客服 38.035s；首次镜像下载未测 |
| Provider cache | 未控制、未完成对照测量；同一 Mac 共享资源，样本不足以推断条件优势 |
| 硬件隔离 | 未完成；evaluation/attract 是进程和数据隔离，不是硬件隔离或多租户安全边界 |
| 断网 | OS 阻断已实测为真实 ConnectError，usage 为 null/unknown，本地控制状态与 memory 可读；这不是本地推理证明 |
| 安全 | 本地受信任操作者假设；不要把 9019/9021 当公共部署入口 |

已有真实 demo 包括 swarm prompt 闭环 `run_55c5f8847af64658`（19.024 秒，独立验证通过）以及单独的 memory demo；它们不与 evaluation 混算。memory UI 两次复用分别为 19.157/18.481 秒，2/2 成功，reset 为 0.063 秒。evaluation 原始 run 与 summary、最终 acceptance、browser-memory、offline、Attract report 均保留在 `artifacts/`；失败记录不被转成成功。浏览器 acceptance/browser-extra 与 live-safety 检查均有独立 report。

真实运行依赖远程 `evomap-gpt-5.6-luna`，不是本地推理。离线 OS 阻断得到真实 `ConnectError`、null/unknown usage，本地控制状态与 memory 仍可读；这不等于离线推理。官方 GEP SDK + 本地 authority 只证明协议复用与本地审计边界，不等同于官方 native swarm；Hub 授权 pending，Jev disabled。原 AgentMED 只复制复用，未改动、未 push。

warm-cached image stop/start 的启动实测见 `artifacts/startup/report.json`，首次下载未测；供应商 cache 未控制，不能从小样本推导性能优势。真实 key、数据库密码、模型 key、worker token 与 Langfuse headless 凭据只应放在受保护的本地配置位置（`infra/.env` 及 root 受保护配置）；[.env.example](../.env.example) 只有变量名空值。不要将其复制到文档、截图、日志或提交。
