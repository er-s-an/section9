# Section9：可验证的本地 Agent 故障响应实验室

Section9 是一个固定小智业务 fixture 上的本地 Agent 故障响应实验室，模型推理依赖远程 API。它把真实模型请求、受控故障注入、角色协作、审批与独立验证连接成一条可复核的闭环。业务状态由 SQLite WAL 与事件记录保存；Langfuse 只负责遥测诊断，不能替代控制面、验收或审计记录。

![Section9 Agent 办公室](artifacts/office-reuse/final-layout/office-1440.png)

办公室复用 Star-Office-UI 固定版本的完整房间、桌椅与角色素材，真实状态接入 Section9。点击房间里的角色查看本轮任务与消息。2026-09-22 改版实测和复用边界见 [办公室验收记录](docs/OFFICE_DELIVERY.md)。

当前入口（本机）：

- 操作台：<http://127.0.0.1:9019>
- 蜂群只读展屏：<http://127.0.0.1:9019/showcase/swarm>
- 单 Agent 只读展屏：<http://127.0.0.1:9019/showcase/baseline>
- Worker/Agent API：<http://127.0.0.1:9021>
- 独立 evaluation 进程：<http://127.0.0.1:9024>
- evaluation worker 进程：<http://127.0.0.1:9026>
- attract 导览进程：<http://127.0.0.1:9020>
- attract worker 进程：<http://127.0.0.1:9022>
- 本地 Langfuse：<http://127.0.0.1:9030>
- Collector OTLP/HTTP：<http://127.0.0.1:9431/v1/traces>

运行前需要在受保护位置配置远程模型 key。启动脚本会获取并校验固定版本的角色素材；LimeZu 原始图集不在此仓库再分发，详见素材归因。普通角色不读取操作台 SSE、全量日志、当前场景真值或其他角色的私有推理。Jev 当前 disabled；EvoMap Hub 发布与远端检索尚未实现，不是登录后即可启用；推理使用远程 EvoMap Luna API，并非本地或断网推理。

## 双屏 Showcase

两个展屏对应一个 Pair 的两套独立运行实例，分别保存配置、任务、权限、usage 与验收；并发准入共享，预算独立。页面只读，打开或刷新不启动模型请求。原 `/` 侧栏的 Pair showcase 可创建、开始或重置；创建后先打开 SWARM 和 BASELINE 固定链接，再点击“开始”。重跑必须创建新 Pair，旧失败和中断不会覆盖。

七阶段、角色位置、日志和底部五个证据抽屉由真实事件投影。单 Agent 基线只显示一个推理角色。完整契约见 [Showcase 架构](docs/SHOWCASE_ARCHITECTURE.md)，版本、原始实测、截图和限制见 [本次双屏交付](docs/SHOWCASE_DELIVERY.md)。

```sh
./scripts/start.sh                            # 启动
./scripts/showcase-acceptance.sh               # 完整复跑，会消耗远程模型预算
.venv/bin/python scripts/reset-showcase.py     # 重置当前 Pair；保留历史
```

原控制台的 `scripts/reset.sh` 只重置原控制台，不会重置 Showcase 的 Pair。

## 启动与停止

新克隆需要 Python/uv、Node.js/npm 和运行中的 Docker Desktop。先执行 `python3 scripts/init-local-config.py`，再在本机 `.env` 填入自己的 `EVOMAP_MODEL_API_KEY`。初始化仅创建缺少的文件，保留已有配置；密码随机生成并以 0600 保存。详细说明见 [本地配置](docs/LOCAL_SETUP.md)。模型推理使用远程 EvoMap API。

```sh
cd /Users/xiejiachen/Documents/ChatGPT/rebuild/section9
./scripts/start.sh
./scripts/reset.sh
./scripts/stop.sh
```

`start.sh` 会同步锁定依赖、构建前端、启动本地 Langfuse/Collector，再启动 9019 服务。观测栈的单独生命周期可用 `scripts/infra-start.sh`、`scripts/infra-status.sh`、`scripts/infra-stop.sh`；停止不会删除观测卷。evaluation 单独启动：

```sh
./.venv/bin/python scripts/environments.py start
./.venv/bin/python scripts/environments.py status
```

Evaluation 使用独立的 `data/evaluation`、9024 API 和 9026 worker 进程；attract 使用独立的 `data/attract`/memory 边界、9020 API 和 9022 worker，激活后最多三轮，每轮目标 90 秒。

## 观测与配置

本地 Langfuse 使用官方 v4 Docker 组件，OTLP Collector 将 span 分别排入 backend 与 Langfuse 队列。Langfuse v4 的本地页面在 [9030](http://127.0.0.1:9030)，官方说明见 [Docker Compose 部署文档](https://langfuse.com/self-hosting/deployment/docker-compose) 与 [OpenTelemetry 集成文档](https://langfuse.com/integrations/native/opentelemetry)。

真实 key、数据库密码、headless 初始化凭据只在 `infra/.env`（权限 0600，已被 gitignore）中保存；模板只有空变量名，见 [.env.example](.env.example)。不要把 `infra/.env`、模型 key 或 worker token 放入 README、截图、日志或提交。

检查真实 span：

```sh
./.venv/bin/python scripts/check-telemetry.py --run-id <本次运行的run_id>
```

它读取本地 Langfuse v4 Observations API，严格匹配指定运行的完整 trace_id，并将记录写入 `artifacts/telemetry/`。旧 LF-only 记录不能证明 Collector→控制端贯通；最终以 root 生成的同 trace_id 双侧入库报告为准。它不会用测试 POST 200 伪造“已观测”。

## 当前整改与验收

本轮修复跨事故 lease、迟到消息/计划、错误模型 JSON、reset 在途取消、业务答案与成本验收、启动身份、计分屏未知用量及版本分组。最新实测、失败记录和复现入口见 [审查整改报告](docs/AUDIT_REMEDIATION.md)。计分屏默认只显示当前实现版本；缺少完整源码绑定的旧记录单列为 legacy。

一条验收命令：`./scripts/acceptance.sh`。它会真实调用远程模型、操作浏览器、短暂停止/恢复本项目 Collector，并保留一轮故意 reset 的 evaluation 失败与未知 usage。该安全实验不是性能样本；不会从统计中删除。四类正常故障中任何一轮失败、来源不一致或双路遥测不匹配，命令均返回非零。

## 历史版本证据（不可视为当前版本通过）

整改前历史 evaluation 共 36 次：`single` 8/8、`muted` 7/8、`swarm` 12/12（每格 3 次）、`memory` 8/8，合计 35 pass、1 个 muted/composite 失败。失败和 unknown usage 均保留在原始 run 文件中；小样本不能推出任何条件优势。`memory_jev` 因 Jev 未启用保持待测，不能用 `single_memory` 替代。evaluation 使用 frozen seed snapshot，结果写回独立 evaluation-results；真实 demo memory 记录单独看待，不能与 frozen evaluation 混算。

```sh
cd /Users/xiejiachen/Documents/ChatGPT/rebuild/section9
./scripts/acceptance.sh
./scripts/reset.sh
```

模型为远程 `evomap-gpt-5.6-luna`，不是本地推理。真实官方 GEP SDK 与本地 authority 已接入，但不冒充 EvoMap 官方 native swarm；Hub 远端发布/检索未实现，Jev 未启用。浏览器验收已实际检查权限、接管、旧 grant 拒绝和 reset；四类 integration 检查多次通过。

历史新增 4 次 swarm 的实测耗时为 18.963、19.932、21.215、21.221 秒。Attract 9020 已完成 3 次闭环：19.867、21.611、20.710 秒，总运行 278.831 秒；业务验证、主配置/事故/evaluation score/memory 不变、自动调用停止和独立进程/database/memory 检查均通过，共享同一台 Mac，非硬件隔离。

真实 demo memory 已连续两次复用同一 playbook：19.157、18.481 秒，2/2 成功、`reuse_count=2`；reset 实测 0.063 秒。离线 OS 阻断实测得到真实 `ConnectError`、usage `null`/`unknown`，本地控制状态与 memory 仍可读；这不是本地推理证明。warm-cached image stop/start 的启动实测见 `artifacts/startup/report.json`，首次下载未测。

交付图像和视频的来源审计见 [docs/STAR_OFFICE_SOURCE_AUDIT.md](docs/STAR_OFFICE_SOURCE_AUDIT.md) 与 [docs/STAR_OFFICE_ATTRIBUTION.md](docs/STAR_OFFICE_ATTRIBUTION.md)：代码/逻辑按上游 MIT 说明，角色和其他美术资产仅限非商业示范。A3 海报和 90 秒原速历史录屏位于 `artifacts/delivery/`，字幕非实时。成员资料、如需 Hub 闭环还需开发与授权，商业素材权利仍由用户处理。

完整交付结果、实测表格、限制和证据路径见 [交付说明](docs/DELIVERY.md)，操作顺序见 [演示指南](docs/DEMO.md)。历史同 trace_id 双路遥测曾核对 8 条；当时 55 项组件/边界检查通过，实际浏览器与模型闭环另有原始记录。服务启动会申请跟随本项目进程的 macOS 防空闲睡眠状态；停止服务后自动释放，不修改系统电源设置。
