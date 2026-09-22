# 独立审查后的阶段一整改

2026-09-22。基准为 `main@eb79f972ed4aa3e85859a031750794054c09f487`；本次改动位于本地 `codex/local-product-readiness` 工作树，尚未提交或上传。这里描述代码实现与本机证据，不将它签为独立用户可用产品。远程推理仍由 EvoMap 提供。

## 已实现

| 审查项 | 实际改动 | 边界 |
| --- | --- | --- |
| F1 安装与运行 | install / doctor / start / readiness 分离；启动不安装、不下载；空 key 可读历史；数字、双端口、Docker 资源检查；默认 stop 停应用、evaluation 与本项目 Compose，保留卷 | 独立 Mac、非开发者冷安装仍待测；Docker 观测栈用 infra-start 显式启动 |
| F2 验收权限 | 严格 VerifyRequest；持有任务/epoch/instance/generation/通信版本才可发起；服务端 job 绑定 scope/action/config/contract；结果入库事务内再次检查 | 失去权限时拒绝结案，真实用量保留；单 Agent 通过其 single 任务验收 |
| F3 重启账本 | 请求发出前持久化 provider_state；primary 和所有 Pair/arm 共用幂等恢复；确定未发送归零，可能发送或旧记录未知并保守保留预算 | SIGKILL 证明使用合成预留记录，不冒充真实 Provider 在途强杀证明 |
| F4 禁言 | 删除调用模型前的固定 blocked；模型可尝试修复或明确 abstain；隔离消息、记忆、同伴方案；通信版本改变使旧上下文失效 | 原 muted 历史不能作为新实现的公平消融证据 |
| F5 操作作用域 | primary 控制与 Pair 固定 L2 分区；精确 reset 目标；Pair 轮询、等待/结果/错误；验收 false 明确显示失败；素材失败时显示原创 fallback | 没有合并独立的办公室设计概念原型，没有新增虚假 Agent 动画 |
| F6 日志与交付 | 固定 watermark 完整投影；快照仅附最新 200 条；日志支持稳定分页；每 500 事件一页的 ZIP 带文件哈希、数量、版本和用量 | 只读导出会刷新可重建 journal；不改变执行权限，不调用模型 |

## 本次验证

- 187 项组件/API/竞态测试通过。新增真实 ASGI 请求检查验收开始前和入库前的拒绝；单元替身与下面真实闭环分开。
- 真实 SIGKILL 子进程 + SQLite 重开验证 queued/sent/legacy/验收用途的合成预留恢复、幂等性和 arm 隔离。
- TypeScript/Vite build 通过；Python `ruff --select F`、脚本语法、Git diff whitespace 和受保护凭据扫描通过。
- 浏览器使用真实 9019：客服业务答案、L0 执行拒绝、L1 精确审批、A 暂停/B 接管/A 旧写拒绝、reset 旧令牌拒绝、RCA 与运行记录打开，共 7 项通过。L0→L1 闭环 33.991 秒，fencing 闭环 47.399 秒；故意 reset 的运行保留为 reset，非成功样本。
- 有界真实 muted/prompt 运行 1 次：resolved，28.652 秒，5,255 已知 tokens，unknown=false；有真实 repair 模型返回、dialog.dropped，无 dialog.received。
- 同 Mac 禁外网 + 空 key 隔离服务：复制历史可读，聊天明确 MODEL_UNCONFIGURED，未新增 usage。不是冷安装，不是本地推理。
- 复合 Pair 的 Langfuse 与本地遥测匹配 18 条，两个 arm 的标识绑定可查。
- 完整 stop 实测 13.7 秒：应用及本项目 Compose 停止，5 个数据卷保留，其他运行容器不变。warm restart 的首次就绪检查 ReadTimeout；后续应用、Collector、Langfuse 恢复在线。首次失败报告保留，不能把这个结果称为稳定快速启动。
- 四个 Pair 的完整 ZIP 已下载并核验全部成员 SHA-256、事件总数和 500 条分片边界，总计 381,009 字节。

### 四类故障真实复跑：4 Pair / 8 arm

| 故障 | Swarm | baseline | 已知 tokens（Swarm / baseline） |
| --- | --- | --- | --- |
| prompt | resolved / 39.035s | resolved / 26.107s | 5,890 / 4,463 |
| cost | resolved / 107.103s | failed / 100.998s | 13,670 / 11,244 |
| loop | resolved / 54.548s | resolved / 41.231s | 7,478 / 5,008 |
| composite（prompt + loop） | resolved / 102.814s | resolved / 94.401s | 9,187 / 6,128 |

合计 7/8 验收通过；每格仅一次，不是稳定性或性能优势证明。cost baseline 的配置确已恢复，但一次验收请求的预算预留不足，未调用 Provider，等待期限后失败；业务验收没有把它算成功。该 Pair 的失败与所有请求事件都保留。8 次 arm 的未知 Provider usage 均为 0；验收请求未完成仍使业务证明失败。

开始时另有一次真实交互请求 ConnectTimeout，usage=unknown，已单独保存；连通性检查及一次有界业务重试后恢复。没有自动重试到成功后删除失败。本次不能宣称 60 秒或 20 秒指标稳定达标。

## 证据入口

- `artifacts/product-stage1/20260922/component-tests.txt`：187 passed。
- `artifacts/product-stage1/20260922/provider-timeout.json`：首次真实超时与未知用量。
- `artifacts/browser-acceptance/2026-09-22T13-00-28.276Z/report.json`：真实 UI 与权限闭环；同目录关键截图。
- `artifacts/showcase-cases/1790082184097163000/report.json`：四类故障全部原始结果（含失败）。
- `artifacts/product-stage1/muted-1790082549537960000/report.json`：禁言后的真实模型决策与结案。
- `artifacts/product-stage1/2026-09-22T13-17-44.043Z/browser-report.json`：重启后的真实浏览器作用域/重置/1280 布局/导出检查及截图。
- `artifacts/product-stage1/lifecycle-1790082704283664000/report.json` 与 `artifacts/product-stage1/20260922/lifecycle-readiness-followup.json`：完整启停、保卷与就绪超时后的恢复。
- `artifacts/product-stage1/exports/index.json`：四个 Pair 的小索引；同目录完整 ZIP。
- `artifacts/product-stage1/offline-history/report-20260922T130547Z.json`：同机离线历史证明，包含来源 identity。
- `artifacts/showcase-telemetry/20260922T130910Z/report.json`：本次 18 条遥测匹配。

实际闭环的 backend_source_hash 为 `08c32d09aa0ef66cd5e4ef0979fa7ccd4c61f53d00f5643ccfaca65cde353e15`，frontend_build_hash 为 `9dd570607d0d1982a3c1eccf7e1e862ac74b5982471bcf74d26e889cdb00cb33`。来源记录明确 dirty=true；基准 Git commit 不能单独代表本次工作树代码。后续文档和检查脚本追加不改变上述应用源码/构建身份。

## 复现命令

在 Section9 根目录执行：

```sh
./scripts/start.sh                  # 已安装应用；不隐含付费模型请求
./scripts/product-acceptance.sh     # 阶段一复跑，真实浏览器与远程模型；任一失败返回非零
./scripts/reset.sh                  # 仅 primary，保留历史
```

Pair 在界面中按 Pair/arm 明确重置，或运行 `.venv/bin/python scripts/reset-showcase.py`。独立验证完整启停：`.venv/bin/python scripts/verify-local-lifecycle.py`。观测栈：`./scripts/infra-start.sh --pull never`（仅缓存镜像）；首次镜像获取按 LOCAL_SETUP 的独立安装边界处理。

演示：打开 `/`，确认模型/观测依赖状态；在 primary 注入故障并看真实事件，切 L0/L1 验证拒绝与审批；另建 Pair 后显式开始，打开两个只读展屏。历史 Pair 可直接查看 RCA、分页日志和完整证据 ZIP，不会调用模型。不要把 primary 自治开关当作 Pair 控制。

## 尚未满足的交付条件

1. 独立新 Mac、未参与开发者的冷安装、首次镜像下载及资源峰值：外部待验。
2. 单次成本基线预算拒绝、实测长尾和稳定性：失败已定位并保留，需要后续预算策略审查及重复样本，不能靠放宽验收隐藏。
3. 真实 Provider 请求在途时强杀服务的重启回归：未测；当前为进程级合成预留 + 真实模型正常闭环两类分立证据。
4. 更复杂治理任务、正式公平 benchmark、独立反驳、多能力按需加入：阶段二待实现/验证。现有串行诊断→修复不能包装成已证实的复杂蜂群优势。
5. 新办公室信息层级与演出方案仍为独立设计原型。素材商业授权、Hub 发布/检索、开发者 OAuth、Jev 未启用等原边界保持。

下一阶段先冻结困难任务的业务真值、heldout 与失败分类：跨日期/订单 cohort/SKU 的政策检索错误，验证全局回滚的新增损害，并给单 Agent 相同原始工具和多步机会。确认任务足够区分协作价值后，再将真实能力发现/认领/证据交接接入办公室表现。正式样本不得预填优势；本次 4 Pair 仅作为本机运行检查。
