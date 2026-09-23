# Section9 接力交接：先真实复跑，再继续开发

更新：2026-09-24。**主线已经收敛，请沿用现有对象、前端和运行时，不再另造客服、事故表、任务图或办公室。**

## 1. 先看什么

- [主线与复用边界](DEMO_MAINLINE.md)
- [本次真实验收、失败与限制](DEMO_ACCEPTANCE.md)
- [EvoMap 原生能力实证](EVOMAP_VALIDATION.md)
- 完整规划在 `docs/parity/source/` 的 MD/JSON。目标不是完成证明。
- 原有 `/api/product` 发布/恢复流程的复跑说明在 [PRODUCT_EXTERNAL_APP_DEMO.md](../PRODUCT_EXTERNAL_APP_DEMO.md)。不要和新主线拼成一次事故。

本轮交付为 **自由业务任务 → 应用 Langfuse → 原生会话协作调查 → 审核/退回新版本 → 同证据 single/swarm**。M2 的完整通用动态规划/工具循环仍未交付，生产执行与恢复也不属于这个演示的完成项。

## 2. 在这台 Mac 上接手

- 仓库：`/Users/xiejiachen/Documents/ChatGPT/rebuild/section9`
- 分支：`codex/local-product-readiness`。远端 `https://github.com/er-s-an/section9`；用 `git log -1` 和 `git rev-parse origin/codex/local-product-readiness` 查看交付提交。
- 主页面：<http://127.0.0.1:9160/demo>
- 本轮控制面数据库：`.runtime/demo-mainline/product.sqlite`
- 业务应用：`127.0.0.1:9150`，由本轮控制面按需启动、确认进程归属；业务 DB `.runtime/demo-mainline/demo-target/business.sqlite`。
- Langfuse：`127.0.0.1:9030`。真实应用 trace 由业务侧 SDK 上报。
- 原有 `9019` 服务未停，不能把它当作新主线。
- 原始验证报告：`.runtime/demo-validation/`；只留本机，不提交整包。
- 预先存在的 `frontend/test-results/` 保留在本地，不属于本次源码交付。

启动（如 9160 已在运行，直接打开网页，不要再启动一个）：

```sh
cd /Users/xiejiachen/Documents/ChatGPT/rebuild/section9
S9_DATA_DIR=.runtime/demo-mainline S9_PORT=9160 S9_MODEL_TIMEOUT=120 \
  .venv/bin/python -m uvicorn s9.api:app --host 127.0.0.1 --port 9160
```

停止时对这个终端按 Ctrl-C；生命周期会停止自己拥有的 9150 应用。不要 `pkill python`、不要清空 `.runtime`、不要对原 9019 服务执行 reset。修改 Python 后需要重启此服务；修改前端后 `cd frontend && npm run build`，再浏览器刷新。

## 3. 新机器准备

以下是可复用的安装路径，**本轮没有完成独立新机器冷安装验收**。Mac/Seatbelt 是现有运行时前提；不要在 Linux 上关闭隔离来绕过启动失败。

1. 克隆本仓库、切换交付分支，运行 `./scripts/install.sh`；按 [LOCAL_SETUP.md](../LOCAL_SETUP.md) 配置本机模型和观测栈，`./scripts/infra-start.sh` 启动本项目 Docker 服务。真实值只写被忽略配置。
2. 若没有业务上游 checkout，按下面固定版本克隆；已有干净 checkout 直接复用，不覆盖：

```sh
git clone https://github.com/Pragatheswar-72/support-agent .runtime/external-projects/support-agent
git -C .runtime/external-projects/support-agent checkout --detach 860a29ff80f6e9b866e4cf1098c042e0da425f24
.venv/bin/python scripts/provision_support_agent_adapter.py
.venv/bin/python scripts/instrument_support_agent.py
```

`provision` 仅在 adapted checkout 不存在时运行。`instrument` 会复用 adapted checkout、安装固定 Langfuse 4.15.4、跑原应用测试并记录本地 adapter commit；不同机器 commit 可不同，以本机 manifest 为准。本机当前 `5d9e86e8ae2f90122d6bfecb3f1ec38c32047a79`。不要手工改 checkout 后仍沿用旧版本绑定。

3. 模型：`.env` 中 `EVOMAP_MODEL_API_KEY`，可选 `S9_MODEL`/`S9_MODEL_URL`。Langfuse：`infra/.env` 中 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`/`LANGFUSE_INIT_PROJECT_SECRET_KEY`；服务会读取这些名称并把需要的凭据传给业务应用。
4. EvoMap 三个 native node 凭据在仓库外 `~/.config/section9/evomap-nodes.json`，目录 0700/文件 0600。同机直接复用；新机通过安全渠道配置授权身份，或明确注册新身份：`.venv/bin/python scripts/register_evomap_nodes.py --register`。不带参数只报是否配置。注册脚本尚未单独在空身份机器验收；HTTP 200 也可能拒绝注册，脚本检查 acknowledged，不要循环注册。模型 key 与 node secret 是两种不同凭据。
5. 构建并按上节启动。只在 loopback 运行；没有登录/RBAC，不直接暴露公网。

## 4. 你必须亲自做的真实复跑

不要只打开历史或读这里的成功结论。至少自行输入一个未预设的业务问题，观察实际输出：

1. 打开 `/demo`，查询订单 1001 或 1002；先明确“只查询，不申请退款”。也可问物流或 FAQ。业务用的是演示种子数据，不是真实客户。
2. **业务结束前**应出现实际步骤；完成后展开“调用依据”，核对参数、结果、上下文。确认 Langfuse 不是 Section9 自造旁路 trace。
3. 可勾选证据，然后发起协作核查。观察调查员、复核员、协调员的不同产出；点击办公室人物/角色查看详情。等待原生 session 回读，不能把接口报错时的本地结果当作远端成功。
4. 用真实审核意见退回一次，再按意见重新调查；确认新版本和原版意见都保留，再决定认可。**认可只读建议不授权自动改业务。**
5. 运行单助手对比。相同证据、模型、每 run 100000 token 预算；比较证据质量、缺事实处理、错误断言、耗时和全部消耗。不要宣称 swarm 必胜。
6. 换一个任务，在调查中展开“演练成员离线与接力”，暂停调查员。进行中的晚到结果会被拒绝；页面可能先显示中断，点击继续调查，确认实际其他 native 成员接手。此故障组不能用于和无故障 single 直接排名性能。
7. 刷新/返回/选择历史，检查版本、证据与任务保留。确认断线时有提示，业务不被自动重发。

辅助真实验证（会调用已配置模型及原生服务）：

```sh
.venv/bin/python scripts/verify_demo_mainline.py --live --question '请查订单 1002 的状态与退款资格，只查询，不申请退款。'
.venv/bin/python scripts/verify_demo_mainline.py --live --takeover
```

保留每次报告，包括失败。脚本不会自动重试业务；接力选项只允许一次显式调查恢复。`--task <id>` 复查已有任务时不证明本次看到了业务执行中的事件，也不等于重新跑模型。审核版本的浏览器步骤需要另做。

## 5. 故障与回退

| 情况 | 做法 |
| --- | --- |
| 9150 被其他进程占用 | 停止检查，确认归属；不对未知进程发请求/杀进程 |
| 模型超时、unknown usage | 保留用量与原尝试；人工继续调查前先看错误，不重发业务副作用 |
| Langfuse 尚未同步/断线 | 查看本地观测服务；无 verified 证据不能调查。当前未提供专门的 UI 重采 trace 按钮；可在恢复后新建只读任务，历史不删除 |
| 原生返回 413/缺少消息 | 会失败关闭，不伪造远端确认；减少证据范围。已有分块与内容哈希校验 |
| 业务完成而观测失败 | 页面保留业务结果，单独标记观测失败；不要重复申请退款 |
| 新版本 UI/服务异常 | 切回已发布的源码版本、重建再重启；保留当前 DB/报告备份。旧 9019 是历史备用演示，明确标注其不等价于本次 native 主线 |
| 模型说得不确定 | 正常展示 needs_data/abstain；提出核验要求，不改成预设成功答案 |

## 6. 下一棒工作，按顺序

1. **独立产品验证**：换题、多案盲评、验证新机启动，输出真实失败。现有代码直接复用。
2. **协作效果**：补缺证据主动检索、经验证动态计划、成员能力选择；接 EvoMap 已有原语，别另造身份/消息服务。验收以可追溯的新增证据和质量改进为准。
3. **旧新桥接**：把旧批准/执行/对账/恢复适配到 v1；映射明确 ID，回归证明一致才退役旧写入口。
4. **运行稳健性**：持久任务队列、多进程互斥、真正多用户权限、trace 补采与权限最小化。现在是单进程本机操作台，异步锁不等于分布式调度。

可拆给队友的独立任务：A 盲评与真实用例；B 旧执行适配映射；C 新机部署复跑；D UI 可用性与办公室状态。各自不新建业务权威表，变更共享契约先在主线上对齐。
