# Section9 审查整改与真实验收 · 2026-09-22

本轮针对审查 F1–F8 做定点修复，未重写项目。结论限于**当前 Mac、固定小智业务 fixture、远程 EvoMap Luna、有限配置修复动作**。不声明生产级可靠性、完全离线推理或蜂群普遍优势。

## 直接操作

- 地址：http://127.0.0.1:9019
- 启动：`./scripts/start.sh`
- 验收：`./scripts/acceptance.sh`
- 重置：`./scripts/reset.sh`
- 演示操作：[DEMO.md](DEMO.md)。界面可查看办公室角色、真实消息、RCA、逐次实验、审批拒绝和版本分组计分。

验收命令会使用真实模型，短暂停止/恢复本项目 Collector，并故意中断一轮 evaluation 来检查 unknown usage 保留。它不是纯单元测试命令；失败会返回非零，不把 HTTP 200 当业务通过。

## 已实现及核验

| 审查项 | 实现 | 证据 |
|---|---|---|
| F1 事故归属 | task/run/generation/instance 一致性；plan/grant 哈希绑定来源；终态撤销任务和授权；终态幂等重放也拒绝 | `tests/test_cross_run_authority.py`；真实 A 暂停→B 接管→A 旧写入拒绝 |
| F2 模型结构 | 严格校验 null、错误类型及动作数量；最多两次模型请求；失败终结当前事故，worker 能继续下一轮；进程异常重启限流 | `tests/test_worker_output.py`；初次真实 schema 失败及后续成功都保留 |
| F3 reset | 先换 generation，再取消排队/预算等待/HTTP 请求和旧 Core jobs；前端 reset 不被 chat busy 禁用，丢弃迟到响应 | 两槽/第三个等待槽/预算等待组件测试；真实浏览器 reset 40 ms；独立 evaluation reset 5.7 ms |
| F4 禁言条件 | UI 注入使用 `condition=muted`，manifest 与真实通信层一致；memory+muted 在 UI 和 API 拒绝 | 浏览器 21 项检查全过；消息 dropped 且无 received；API 422 不改变配置或产生事故 |
| F5 验收合同 | 用户可见答案与业务事实一致；独立正例、15 天负例、产品、终止工具四例；input≤2000/output≤1536/total≤3536、和数一致；unknown/整轮超预算/待结算不能结案 | 10 次正常真实实验全部通过；故意部分修复及配置版本竞态均被拒绝结案 |
| F6 启动身份 | 启动时冻结 checkout/SHA/dirty、后端、前端、锁文件、fixture、验收 hash；已有监听者不匹配则拒绝；独立 evaluation 同样校验 | 实际拒绝旧无 identity 服务；独立干净目录启动、空数值、修改源码后旧监听者拒绝、正常停止均通过 |
| F7 迟到来源 | message/plan 推理前冻结 task epoch、instance、generation、transport epoch；接管或禁言换代后拒绝；模型返回后再次校验 lease | 交叉事故、重新注册身份、mute→unmute、provider 期间接管测试；失败结果不交付，消耗保留 |
| F8 计分 | 当前实现默认独立分组；legacy 单列；已知 token 小计与 unknown 轮数同时展示；停留页面自动刷新且防迟到覆盖 | 当前 11 条记录中的 1 次故意 reset/unknown 可见；浏览器轮询与版本选择通过 |

**110 项组件测试通过**，ruff、TypeScript/Vite 构建和前端静态审计通过。异常模型与延迟 HTTP 测试使用明确标注的本地替身；下方实验、浏览器闭环和状态变更使用真实远程模型。

取消保证本地 HTTP 与槽位释放，不保证供应商服务器停止计费；未获 usage 的请求保留 unknown 和预算占用，不计成完整零成本。

## 当前实现的真实样本

同一个 evaluation 服务的完整 source identity 一致，模型、并发上限 2、预算 16000、业务 fixture 与验收合同一致。

| 条件/故障 | 正常样本 | 通过 | 秒数 |
|---|---:|---:|---|
| swarm / prompt | 2 | 2 | 23.273、21.659 |
| swarm / cost | 2 | 2 | 26.264、22.813 |
| swarm / loop | 2 | 2 | 23.577、26.650 |
| swarm / composite | 2 | 2 | 26.022、24.966 |
| single / composite | 1 | 1 | 19.241 |
| memory / prompt | 1 | 1 | 22.356 |

10 次正常试验消耗的已知 token 合计 68,401，正常试验 unknown 为 0。Playbook 样本确实复用了 `seed_quality_prompt`，`reuseapproved=true`。

另有 **1 次故意在途 reset**，`run_94eeec2a6fb542d9`，状态 reset、usage unknown，照常留在计分屏，因此当前总数是 **11 轮 / 10 resolved / 1 reset**，不能将其称为 11 次正常性能试验。muted 的本轮浏览器通信隔离检查属于 demo，不混入正式 evaluation，当前版本该格仍为待测。

这些是有限重复，不能推断长期稳定性，也不证明蜂群比单 Agent 更好。供应商缓存未控制，各环境共享同一物理 Mac。60 秒以内是上述样本的观察结果；20 秒以内并未普遍达到。

## 失败与限制如实保留

- 初次健康探针因答案缺末尾句号失败：`healthy-smoke.json`。随后仅增加空白/标点归一，未放宽业务结论。
- 初次诊断模型两次给出的 `proposed_checks` 类型不符，事故以 `WORKER_TASK_FAILED` 结束，worker 未死亡。已将提示中的 schema 与校验器对齐，原始记录为 `initial-worker-schema-failure.json`。
- 浏览器审计脚本第一次存在等待 UI 刷新不足的竞态，4 个检查失败。修正测试等待后 21/21 通过；两份报告都保留。
- 最终浏览器复跑曾遇一次真实 **ReadTimeout 61.62 秒**，业务探针无答案，事故失败且 usage unknown。原始记录为 `provider-timeout.json`；一次有界重跑后通过。远程 API 是仍存在的外部可用性依赖。
- 干净目录启动复用了本机已安装的 Python/npm 依赖与前端构建，不冒充首次联网下载安装测试。
- 独立业务探针/活性检查驱动控制；Collector 是双路遥测证据。最新复合故障 `run_9f5345bca8624a5a` 的 **8 条完整 trace_id** 在 Langfuse 与控制端匹配成功。
- 角色与工具预定义，成本专家加入是既有能力的发现；禁言条件下修复员依赖同伴证据，不能作为同能力消融优势证明。
- 本地 GEP SDK/资产回写可用；**Hub 发布与远端检索未实现**，并非只差 OAuth。Jev disabled。完整断网推理未提供，历史离线证据不算本轮重新验证。
- 密钥检查仅扫描待提交文件中的本机已配置秘密值；不声称完成 Git 全历史或截图 OCR 扫描。没有复制 CLAIMS.md 或提交 `.env`。

## 版本与证据位置

正常实验使用 `b90b02fe2ba349ae9177c505490478b408c92417` 的运行实现，之后提交仅完善测试脚本、文档和证据。后端、前端构建、依赖锁、fixture、验收五项 hash 保存在每条 manifest；最终运行身份会与这些 hash 比对，不能仅凭 HEAD 或 HTTP 200 认定同版本。

- [原始实验索引与完整来源](../artifacts/audit-remediation/run-index.json)
- [8 次 swarm 及逐次原始记录](../artifacts/audit-remediation/evaluation-swarm/summary.json)
- [single 对照](../artifacts/audit-remediation/evaluation-single/summary.json) / [memory 对照](../artifacts/audit-remediation/evaluation-memory/summary.json)
- [当前计分屏快照](../artifacts/audit-remediation/scoreboard.json)
- [主浏览器权限与接管验收](../artifacts/browser-acceptance/2026-09-22T04-56-23.322Z/report.json)
- [reset、禁言、版本与刷新浏览器验收](../artifacts/audit-browser/2026-09-22T04-46-46-933Z/report.json)
- [成本专家与 Collector 真实断开/恢复](../artifacts/browser-extra/2026-09-22T04-39-58.405Z/report.json)
- [真实计划布局：1280 / 1440 无横向溢出](../artifacts/audit-remediation/plan-layout/report.json)
- [当前 8 条完整 trace 关联](../artifacts/telemetry/telemetry-check-20260922T045206Z.json)
- [干净目录启动证据](../artifacts/audit-remediation/clean-start.json)
- [故意部分修复](../artifacts/live-safety/1790052080489851000/composite-partial-repair.json) / [验收后改配置](../artifacts/live-safety/1790052080489851000/verification-revision-race.json)
- [组件测试输出](../artifacts/audit-remediation/component-tests.txt) / [lint](../artifacts/audit-remediation/lint.txt)

办公室继续复用 Star-Office-UI 的适用素材；本轮修复真实长计划把面板撑宽的问题，未替换成普通卡片。素材许可与非商业边界仍见 [来源审计](STAR_OFFICE_SOURCE_AUDIT.md)。
