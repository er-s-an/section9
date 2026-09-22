# Section9 演示脚本

脚本只演示本地真实服务；模型返回、事件进度与验证结果以页面/API 实际响应为准。不要把页面静态构建或测试 double 当作生产证明。

## 7 分钟完整演示

### 0:00–0:45：启动与边界

```sh
cd /Users/xiejiachen/Documents/ChatGPT/rebuild/section9
./scripts/start.sh
./scripts/infra-status.sh
```

打开 [操作台 9019](http://127.0.0.1:9019)，说明 Langfuse [9030](http://127.0.0.1:9030) 与 Collector 是本机观测栈；模型推理仍是远程 EvoMap Luna。展示 `GET /api/health` 的实际依赖状态。

### 0:45–1:30：健康请求

在聊天入口提交一个真实产品问题。等待真实模型响应，指出 answer、usage、elapsed_s、revision 与 trace_id 来自 API；不手写或补齐答案。若模型失败，展示失败状态。

### 1:30–3:10：Prompt 故障闭环

点击或调用：

```sh
cd /Users/xiejiachen/Documents/ChatGPT/rebuild/section9
curl -sS -X POST http://127.0.0.1:9019/api/inject \
  -H 'content-type: application/json' \
  -d '{"scenario":"prompt","condition":"swarm","seed":42}'
```

观察事件流中的 injected、观察、诊断、计划、审批、执行与验证。说明 fixer 不能仅凭场景名得出结论，计划必须绑定 revision/hash；独立验证包含售后语义、未受影响的 X200 30 小时事实、终止工具错误与预算检查。

### 3:10–4:15：通信隔离与权限

将 communication 设置为 muted，展示同伴消息与同伴推理在通信入口被阻断，Agent 仍能基于自己的观测尝试修复；禁言组可能成功，也可能因修复不完整被验收拒绝。切换 L0/L1/L2 时展示计划/审批边界。所有 worker 使用独立 token 与 `/agent` 端点。

### 4:15–5:00：失联接管与历史记录

重置后点击“演示 fencing 剧本”，观察 A 被暂停、B 接管、A 恢复后旧请求被拒，再打开运行记录与 RCA。需要展示经验复用时，打开“下轮 Playbook”，注入语义退化，验收后 reset 并再次注入；Playbook 库会展示真实命中、成功和复用计数。说明只有 verified run 才能写入本地 GEP Gene/Capsule/EvolutionEvent；`publish_state` 是 `local_only`，不会注册或发布到 EvoMap Hub。

### 5:00–5:45：真实遥测核验

```sh
cd /Users/xiejiachen/Documents/ChatGPT/rebuild/section9
./.venv/bin/python scripts/check-telemetry.py
```

展示 `artifacts/telemetry/` 中脱敏的 trace_id、name、run_id、timestamp 和 metadata_complete。API 查询使用 `fields=core,basic,metadata,time`；旧 LF-only 结果不证明 Collector→控制端贯通，展示 `artifacts/telemetry/telemetry-check-20260921T210738Z.json` 中 8 个同 trace_id 双侧入库记录。空结果或 metadata_incomplete 都要原样报告。

### 5:45–7:00：独立 evaluation 与边界

说明 9024/9026 使用独立 evaluation 数据与 worker；正式 evaluation 共 36 次，35 pass、1 个 muted/composite fail：single 8/8、muted 7/8、swarm 12/12、memory 8/8。`memory_jev` 由于 Jev disabled 保持 disabled；小样本不支持条件优势结论。Attract 三轮已完成并停止自动模型调用，耗时为 19.867/21.611/20.710 秒，总计 278.831 秒。已有镜像和依赖下的停栈重启到真实业务可用实测 38.035 秒；首次下载、供应商缓存控制与完整离线推理仍未覆盖。

## 90 秒短演示

1. 打开 9019，展示 `/api/health` 与当前依赖状态（10 秒）。
2. 发起真实 chat，展示 answer、usage、elapsed_s、trace_id（20 秒）。
3. 注入 prompt 场景，展示事件从故障到独立验证（35 秒）。
4. 运行 `scripts/check-telemetry.py`，展示最新同 trace_id 双侧入库报告与 run_id；旧 LF-only 记录不作贯通证明（15 秒）。
5. 说明本地控制、远程 Luna、Jev disabled、Hub OAuth pending、官方 GEP SDK + 本地 authority 不等于 native swarm，以及已测 warm-cached 启动、未测首次下载、缓存和断网边界（10 秒）。Star Office 代码/逻辑按 MIT 归因，美术资产仅限非商业示范，详见 `docs/STAR_OFFICE_SOURCE_AUDIT.md`。

若任一步没有真实响应，短演示应停在该状态并报告 `NOT_RUN`/失败原因，不跳转到预录成功结果。
