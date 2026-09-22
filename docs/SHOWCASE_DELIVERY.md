# Section9 双屏 Showcase 交付记录 · 2026-09-22

已实现真实两侧独立运行与只读双屏。最后一轮复合故障两侧都通过独立业务验收，浏览器 21 项检查通过；保留了此前的连接失败、超时、人工 reset 和 UI 验收失败。当前证据支持本机可操作演示，**不支持稳定性承诺或蜂群优于单 Agent 的结论**。

## 打开与复跑

- [原操作控制台](http://127.0.0.1:9019/)
- [本次 SWARM 固定链接](http://127.0.0.1:9019/showcase/swarm?pair_id=pair_eae71235e7994294)
- [本次 BASELINE 固定链接](http://127.0.0.1:9019/showcase/baseline?pair_id=pair_eae71235e7994294)
- 不带 query 的 `/showcase/swarm` 和 `/showcase/baseline` 绑定当时 active/latest Pair，然后将固定 ID 写入 URL。

在仓库根目录执行：

```sh
./scripts/start.sh
./scripts/showcase-acceptance.sh
.venv/bin/python scripts/reset-showcase.py
```

分别是启动、完整验收、重置当前 Pair。交付保留9019主服务与本地观测栈运行；旧的9024独立evaluation进程已停止以减轻主机负载，仍可按README按需启动。验收会有界调用远程模型，顺序执行组件检查、单侧 reset、原控制台回归、prompt/cost/loop 成对运行、composite 双屏浏览器实测和双路径遥测核对。任何脚本失败返回非零，失败记录保留。原控制台的 `scripts/reset.sh` 只重置原控制台，Pair reset 单独作用于选中的 Pair。

演示步骤：在 `/` 侧栏的 Pair showcase 选择案例，点“创建”；分别打开 SWARM 与 BASELINE 固定链接，回控制台点“开始”。两侧显示相同 spec、不同 run，阶段与办公室活动跟随真实事件。点阶段、角色或底部证据条看原始证据；Logbook 可以筛选、暂停自动滚动，暂停不影响接收与执行。重跑点“创建”获得新的 Pair。Pair 固定 L2、记忆检索关闭，原控制台 L0/L1 和记忆开关只作用于原控制台。

## 来源与版本

起点为 GitHub/local main `4b79089461d8e788a3f4caa619a584380360cfd4`。实现提交 `35bccf7`，键盘焦点修复 `425c2f0`，最终事件投影修复 `4ae82eb`。最终模型/浏览器实测绑定完整源码提交 `4ae82eba6bf7959706e3cc43c10a98db44e7b5e2`。之后的交付提交只增加验收脚本、证据与说明（不改变上述运行代码或前端构建）；最终服务重启后以 `/api/health` 的当前提交和以下内容 hash 对照。

实测时 `dirty=true`：当时有尚未提交的本轮运行 artifacts，源码已提交。完整 dirty digest、backend/frontend/lock/fixture/contract 身份都在各 run 的 PairSpec 中；不把旧源版本的试验改写为最终源版本。

| 内容 | SHA-256 |
|---|---|
| backend（递归包含 workers/pairs） | `b357f815a5073ba62fc5fa603608eaec24ebf5b7553bbc178576ad51d28b0119` |
| frontend 构建 | `5b57d93e69b097234bef5c5d35eb57d7ad4f28fcd2f0bf347a0c061d30e756f5` |
| 依赖锁文件 | `23c053f7c44608012923799d7475088e6944f6c755eedaee69e4e11719e1ee24` |
| fixture | `4c5b0b75f618cc55d0cfda3e623faa13b64d6a4d1b8ae8dc11b04e18e50b5526` |
| acceptance contract | `09b5f4bd0bdf0c8533eb7acf4b822e2452d7df8dcaebe988aab216125674b68d` |

最后一轮 Pair `pair_eae71235e7994294`，spec hash `647a6dce912e1b26ac4d08f0ed41b561b60fc95e21639141e557c6750b5a8c5e`；SWARM run `run_2cba5ab82ab04e58`，BASELINE run `run_bf80ec53bdaf4977`。完整冻结配置、业务 fixture、故障、预算、模型参数、两侧原始事件、计划、动作和 checks 在 [terminal.json](../artifacts/showcase-browser/2026-09-22T06-45-38.956Z/05-terminal.json)。该轮两侧实际返回模型标识记录为 `gpt-5.6-luna`，共同启动释放偏差 3.989ms。这个偏差不代表模型内部同时开始。

## 已实现及验证边界

| 能力 | 实现/检查类型 | 结果与证据 |
|---|---|---|
| PairSpec 不可变、幂等创建、永久两 run ID | 真实 SQLite / API 组件检查；模型部分用替身 | 151 项测试通过；[执行记录](../artifacts/showcase-validation/components.json) |
| 两侧配置、任务、身份、grant、预算、取消独立 | 真实 SQLite 权限检查；共享网关使用测试 transport | 跨 scope 对象被拒；对侧 revision/fence 不变；错误 tested hash 不结案 |
| 同 generation 1 单侧 reset | 真实服务、两个真实在途模型请求 | reset 0.292s；SWARM reset/unknown=1，BASELINE 继续原请求并 resolved，15.721s；[完整证据](../artifacts/showcase-reset/1790058671287957000/report.json) |
| 原 console 权限与接管 | 真实浏览器、模型、OS 暂停和配置变更（425c2f0；最终仅再改只读投影） | 聊天真值、L0 拒绝、L1 精确批准、L2 fencing 接管、旧 grant/reset 拒绝、禁言切换、RCA 7项通过；[报告](../artifacts/browser-acceptance/2026-09-22T06-41-11.678Z/report.json) |
| 复合故障两个真实闭环 | 本机应用与独立业务请求；远程 EvoMap 模型 | 最终 SWARM 31.905s / 7183 tokens；BASELINE 25.007s / 5338 tokens；两侧 unknown=0 |
| 展屏只读和作用域 | 真实浏览器网络记录与 API | Showcase 无 POST/PUT/DELETE；两个独立 active run；刷新/历史切换固定 ID，迟到旧响应不能覆盖新 Pair |
| SSE 和可读日志 | 浏览器离线/恢复、暂停滚动/新事件、刷新 | 无重复 ID；阶段引用真实事件；当前基线仅一个推理角色 |
| 视觉与键盘 | Playwright + 人工截图复核 | 1280×800、1440×1000无遮挡；额外1024×640检查允许纵向滚动；抽屉 Tab 与 Escape；不是原生浏览器缩放认证 |
| 遥测 | 真实 Collector 与本地 Langfuse | 两侧各有匹配；共15条完整 trace_id/pair/run/arm 匹配；[报告](../artifacts/showcase-telemetry/20260922T064659Z/report.json) |
| journal 重放/重建、初始化失败/重启中断 | 临时数据库和组件替身 | 幂等、跨 scope 防重标注、重建保持事件身份、遗留 reservation 标 unknown、单侧 reset 后服务退出仍清理存活侧 |

语义、成本、loop、composite 四种 Pair 案例都已在最终运行代码上各完成一轮两侧真实闭环，共8个 arm run，全部 resolved 且 unknown=0。三类新增独立样本见[真实案例记录](../artifacts/showcase-cases/1790059849089594000/report.json)，没有借用历史 legacy 的四类单运行测试。独立验收与结案 CAS 的重要边界在组件检查中验证；本次 paired 没有额外进行真实模型“只修复合故障一部分”的人工干预试验。原控制台禁言通信层已有组件与历史真实 ablation 证据，本轮回归实测的是开关，不重报成一轮新的完整 ablation。

## 所有正常 paired 样本（按各自版本保留）

表内为“业务状态 / wall-clock 秒 / 已知 tokens”，unknown 单列；每个 Pair 的两侧同 spec，不同 Pair 的版本不同，**不合并计算算法优势**。同 seed 仅绑定当前固定 fixture，不保证远程模型确定性。

| Pair | 源码提交 | SWARM | BASELINE | unknown 数 SWARM / BASE | 浏览器套件 | 记录 |
|---|---|---|---|---|---|---|
| `pair_5009eaf5f3dd4bef` | `4b79089` | resolved / 42.539s / 7317 | resolved / 26.64s / 5280 | 0 / 0 | 未通过 | [2026-09-22T06-07-44.913Z](../artifacts/showcase-browser/2026-09-22T06-07-44.913Z/report.json) |
| `pair_492144569b624bec` | `4b79089` | resolved / 68.777s / 9651 | resolved / 37.519s / 5303 | 0 / 0 | 通过 | [2026-09-22T06-14-36.399Z](../artifacts/showcase-browser/2026-09-22T06-14-36.399Z/report.json) |
| `pair_f986ccf5a4334436` | `35bccf7` | failed / 66.284s / 7440 | failed / 180.165s / 3143 | 3 / 2 | 未通过 | [2026-09-22T06-35-47.183Z](../artifacts/showcase-browser/2026-09-22T06-35-47.183Z/report.json) |
| `pair_f29b0ee70e894cfe` | `425c2f0` | resolved / 35.683s / 7398 | resolved / 19.718s / 5258 | 0 / 0 | 通过 | [2026-09-22T06-42-42.652Z](../artifacts/showcase-browser/2026-09-22T06-42-42.652Z/report.json) |
| `pair_eae71235e7994294` | `4ae82eb` | resolved / 31.905s / 7183 | resolved / 25.007s / 5338 | 0 / 0 | 通过 | [2026-09-22T06-45-38.956Z](../artifacts/showcase-browser/2026-09-22T06-45-38.956Z/report.json) |

| `pair_a02217a7c65e4626` (prompt) | `4ae82eb` | resolved / 26.228s / 5750 | resolved / 19.657s / 4468 | 0 / 0 | API 实测 | [cases](../artifacts/showcase-cases/1790059849089594000/report.json) |
| `pair_2ad7ee2118e74219` (cost) | `4ae82eb` | resolved / 29.169s / 9446 | resolved / 21.459s / 7913 | 0 / 0 | API 实测 | [cases](../artifacts/showcase-cases/1790059849089594000/report.json) |
| `pair_5675273ccdd14106` (loop) | `4ae82eb` | resolved / 28.19s / 6733 | resolved / 18.819s / 4858 | 0 / 0 | API 实测 | [cases](../artifacts/showcase-cases/1790059849089594000/report.json) |

最终源版本每类只有 1 个正常 Pair（共4个 Pair、8个 arm run），不能称为稳定通过。开发过程中保留的早期试验不能冒充最终版本的重复样本。当前四类样本均为固定 fixture、有限修复动作的浅层故障；蜂群额外的诊断与通信开销是其更慢的合理解释，但未做因果隔离。用户提出的“复杂 Agent 治理会改变结果”作为待验证假设保留：后续应加入分散证据、可并行子问题、相互影响的修复与独立制衡，并同时比较正确率、误修率、影响范围和业务恢复。复杂任务上的优势尚未实测。

## 保留的失败与降级

- 首次 UI 试验发现 baseline 多列了基础设施角色；修复前报告位于 [06-07 记录](../artifacts/showcase-browser/2026-09-22T06-07-44.913Z/report.json)。两侧业务结果成功不等于 UI 验收通过。
- 首次 reset 脚本因旧 model.completed 事件没有 request_id 报 KeyError；原证据位于 [失败记录](../artifacts/showcase-reset/1790058205697541000/report.json)，已修复字段读取并完整复跑，没有删除它。
- [06-32 原控制台失败](../artifacts/browser-acceptance/2026-09-22T06-32-08.266Z/report.json)：执行者租约过期、模型 ConnectError，未走到 L1 审批。此前通过的 L0 拒绝与真实聊天仍保留；其后7项控制台回归通过。
- [06-35 Pair](../artifacts/showcase-browser/2026-09-22T06-35-47.183Z/05-terminal.json)：SWARM 66.284s 验收失败、unknown=3；BASELINE 180.165s RUN_TIMEOUT、unknown=2。当时同时观察到主机高负载和模型连接故障，不能据此断定唯一根因。系统没有误结案。该轮浏览器又发现实时刷新抢走抽屉焦点，已修复并复跑。
- 本机 Langfuse/Collector 曾超时；只读诊断看到容器无 OOM/重启，ClickHouse 约3GiB、瞬时CPU约48%，主机还有其他较高负载。后续健康恢复并匹配到最终15条 span。共享 Mac 与供应商缓存仍是未控制的资源条件；本系统没有硬件隔离。

## 范围与限制

- 推理是远程 EvoMap API，本机运行应用、协作控制和存储。没有宣称离线推理；未在本次新增 paired 路径重做完整断网实验。
- 优先保留并复用 Star-Office-UI 房间与像素角色素材；这里是 Section9 自建只读投影，不把自建 PairCoordinator 称为官方 swarm。素材版本与许可见 [归因](../frontend/public/vendor/star-office/ATTRIBUTION.md)。原 AgentMED 未修改。
- GEP 本地输出按 arm 分目录；两侧本轮检索均关闭，不共享结论。Hub 远端发布/检索未实现、开发者 OAuth 未启用、Jev 无真实产物保持 disabled，均不是本机 Showcase 启动前提。
- 服务重启会将未完成 Pair 标为中断，不自动恢复旧 lease。只有显式新建 Pair 才能重跑；active 指针可指向已结束结果。
- Journal 默认单次查询上限10,000条，当前试验规模远低于此；未做大规模长跑压力测试。Langfuse只读核对查询最多最近100条，缺匹配会降级，15条匹配不是“所有 span 永不丢失”的承诺。
- 原生浏览器缩放、屏幕阅读器完整审计与多轮稳定性实验尚未完成；都未预填通过。没有需要用户登录才能打开本机双屏的阻塞；远程供应商和本机资源仍可能造成运行降级。

## 关键截图

[SWARM 1280×800](../artifacts/showcase-browser/2026-09-22T06-45-38.956Z/06-swarm-1280.png) · [BASELINE 1280×800](../artifacts/showcase-browser/2026-09-22T06-45-38.956Z/06-baseline-1280.png) · [SWARM 1440×1000](../artifacts/showcase-browser/2026-09-22T06-45-38.956Z/06-swarm-1440.png) · [BASELINE 1440×1000](../artifacts/showcase-browser/2026-09-22T06-45-38.956Z/06-baseline-1440.png)。[浏览器完整报告](../artifacts/showcase-browser/2026-09-22T06-45-38.956Z/report.json)保留21项检查、来源身份、两个run与失败字段；原始事件与可核对业务回答在同目录JSON。
