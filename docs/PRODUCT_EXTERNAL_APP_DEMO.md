# 外部 Agent 产品工作区：启动与复跑

本指南对应本机 Section9 + `support-agent` 本地测试环境。它使用配置中的真实模型服务，执行订单/退款的只读业务请求；每次完整验收最多预留 16,000 provider tokens（订单、退款、恢复各最多预留 5,000），未知 usage 会阻止后续探针并判失败。不是生产环境，不会触发真实退款或付款。

## 打开与操作

从仓库根目录启动：

```sh
./scripts/install.sh
S9_CREDENTIAL_REVISION=local-dev-rotation-2026-09-23 ./scripts/start.sh
```

打开 [http://127.0.0.1:9019/](http://127.0.0.1:9019/)，点“产品工作区”。确认项目 `support-agent`、环境“本地测试环境”。工作区会自动每 5 秒刷新真实 API 状态；候选运行时停止时，顶部显示“已停止/降级”，连接能力只显示最近记录，不当成实时健康。

建议演示顺序：

1. 点“接入并核验版本”：核实运行源码、health、依赖与 upstream tests；`/ready` 的 404 保持为 unsupported。
2. 点“订单状态真值探针”和“退款资格边界探针”：分别核验种子订单 1001 已送达、订单 1002 不具退款资格；展开事故证据看实际工具参数、结果与 Langfuse observation。
3. 点“独立回归验收”：查看固定保护测试的原版/候选结果、上游完整测试、保护路径和 patch hash。
4. 选有 ready verification 的事故，点“批准本地测试发布”，再“执行本地测试发布”。这记录的是明确标记的 `local-console-operator` 测试身份，不是个人登录，也不是你的生产批准。
5. 点“观察并核验恢复”：要求连续 5 次 health 检查、一个业务真值探针、批准的 source revision 与运行配置均保持一致。当前观察约 4 秒；不代表长期稳定性。
6. 打开“协作与经验”查看本地能力及官方远端能力边界。若官方显示未启用，不要把本地消息说成 EvoMap Hub 会话。

主要证据：

- 三次核心闭环：`artifacts/external-support-agent/closure-runs/delivery-v1-run01-reset-20260923.json`、`delivery-v1-run02-service-restart-20260923.json`、`acceptance-20260922T213107Z-65002.json`。三轮均 19 个 API 步骤全过，用量分别 2,110 / 2,117 / 2,282 tokens。
- 备份/还原校验：`artifacts/external-support-agent/g5/backup-verification-delivery-v1-postfix.json`。第一轮 acceptance wrapper 的 CLI import 失败在 `failures/acceptance-backup-cli-import.json` 保留；修复后单独复测备份/还原，避免重复付费模型调用。
- 独立 Mac 空数据启动校验：`artifacts/audit-remediation/clean-start-1790113050948515000.json`（复用本地锁定依赖和 build，不是全新下载安装证明）。
- 浏览器截图：`artifacts/external-support-agent/browser/product-workspace-g3-recovered-final.png`、`runtime-stopped-history-labels.png`、`runtime-stopped-probe-refused.png`。
- 全部失败记录：`artifacts/external-support-agent/failures/`；失败文件是验收历史，不要删除来“清绿”。

## 常用维护命令

停止只属于本项目的 Section9 Web/API 服务，保留历史数据和其它 Compose 服务：

```sh
./scripts/stop.sh --app-only
```

重置内置实验室的当前 run、worker leases 与缓存；不删除外部产品 incident/evidence：

```sh
./.venv/bin/python scripts/service.py reset
```

重跑一次有预算约束的真实闭环，并在同一批验收后备份、还原、校验产品数据库与证据文件：

```sh
sh scripts/product-acceptance.sh --model-token-budget 16000
```

该命令会生成新的唯一报告和日志，不覆盖旧失败；完整工作时间受模型与 Langfuse 延迟影响。若要单独做产品证据备份/恢复：

```sh
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=".runtime/manual-backup-$stamp"
restored=".runtime/manual-restore-$stamp"
.venv/bin/python scripts/product-lifecycle.py backup --output "$backup"
.venv/bin/python scripts/product-lifecycle.py restore --backup "$backup" --target "$restored"
.venv/bin/python scripts/verify-product-backup.py --live data/product.sqlite --backup "$backup" --restored "$restored" --report "artifacts/external-support-agent/g5/manual-backup-verification-$stamp.json"
```

## 配置和边界

- `EVOMAP_MODEL_API_KEY`、`SECTION9_MODEL_URL`、`SECTION9_MODEL` 与 Langfuse keys 仅存在被忽略的本地配置文件。文档、报告和截图不得记录 key 值；`S9_CREDENTIAL_REVISION` 是用于旧批准失效的非秘密版本标签，凭据轮换后也要更新它。
- 默认只绑定 loopback。API 的本地测试操作者是受信任本机边界，不提供个人身份登录；不要映射到公网或当作多用户生产认证。
- 外部 candidate 运行数据在 `.runtime/external-projects/support-agent` 与 scoped 本地数据目录；业务探针使用固定隔离订单，没有真实支付侧效应。
- 当前 worker sandbox 只实现 macOS Seatbelt。Linux 冷装能安装、构建、通过产品/connector 测试，但真实服务 startup 会 fail closed；不允许以无隔离 worker 方式绕过。
- EvoMap 官方 remote session/discovery/experience exchange 未授权且未启用；独立物理 Linux 设备未提供。两项均不属于已通过交付。
