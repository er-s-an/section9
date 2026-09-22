# 办公室改版与 GitHub 上传

2026-09-22，先上传完整本地交付基线 `55e8b3e` 到 `er-s-an/section9` 的 `main`，再提交办公室改版。仓库不包含密钥、数据库或原始 LimeZu 角色图集。GitHub 源码上传不代表公网服务部署。

## 实际复用

房间背景、电脑桌、沙发和本地角色图集来自 Star-Office-UI 固定 commit `f29c107e9728a72f2635f10b4e8203b29b37221d`，来源、哈希与许可见 [素材审计](STAR_OFFICE_SOURCE_AUDIT.md)。从最初的横排角色改为房间内的监测工位、会诊与修复区、机房、档案与待命室。

现有 Section9 React 控制台使用 `OfficeScene` 适配这些素材，没有整体移植上游 Flask/Phaser 运行时。角色的工作位置、状态、心跳、任务说明和消息读取真实 snapshot/SSE；位置转换是状态可视化，并不表示后端执行了物理移动。没有随机 demo 对话或按时间播放的事故剧本。角色详情按 generation 和 run_id 双重过滤，reset 后迟到的旧运行消息不会显示为新轮次活动。

## 已验证

真实模型/浏览器记录：[report.json](../artifacts/office-reuse/2026-09-22T02-20-07.139Z/report.json)，对应完整 [运行记录](../artifacts/office-reuse/2026-09-22T02-20-07.139Z/composite-run.json)。

- 7 个真实 Agent 分布在办公室内，角色可通过键盘打开详情。
- 暂停/恢复 A、禁言/恢复通信均调用真实控制 API，并显示对应后台状态。
- 真实复合故障 `run_9689b1a29d3a40a4` 完成诊断、修复、独立业务验收与结案：**22.772 秒**。本轮只有这 1 个新增模型闭环样本，不宣称稳定性提升，不加入正式 36 次 evaluation 样本。
- 诊断员详情显示真实 `dialog.received`、任务完成消息、时间与 event_id。
- reset 后从第 35 轮切换到第 36 轮，旧运行消息不再出现在角色详情。
- 浏览器无 pageerror 或 4xx/5xx 资源响应。

最终布局记录：[final-layout/report.json](../artifacts/office-reuse/final-layout/report.json)。1280 与 1440 宽度均无横向溢出，所有角色按钮在房间内，详情面板位于房间下方；办公室范围 axe WCAG 2 A/AA 检查无违规。启用 reduced motion 时位置过渡为 `0s`。

3 项纯前端事件边界测试通过：当前 generation 携带旧 run_id 的迟到事件、reset 空运行、字符串序号的数值排序。它们是组件检查，不是模型闭环证据。

## 保留的失败记录

- [首次浏览器检查](../artifacts/office-reuse/2026-09-22T02-19-23.939Z/report.json) 在禁言 API 刚成功、React 尚未更新时提前断言失败。改为等待实际 UI 状态后重跑通过；这次未发起模型闭环。
- [axe 初始化失败](../artifacts/office-reuse/final-layout/report-before-axe-context-fix.json) 因测试工具要求显式 browser context；修正检查脚本后通过。首次布局检查本身已通过。

复跑命令（真实闭环会调用配置的远程模型）：

```sh
node --test scripts/office-events.test.mjs
node scripts/browser-office-layout.mjs
node scripts/browser-office.mjs
```

当前截图：[完整控制台](../artifacts/office-reuse/final-layout/office-1440.png)、[办公室场景](../artifacts/office-reuse/final-layout/office-room.png)。此前 A3 海报和 90 秒录屏展示旧布局，保留为历史记录。

## 内置浏览器窄窗口补验

在用户当前 Codex 内置浏览器的 599px 视口中，页面宽度实测为 599px，房间宽 579px，7 个角色按钮均完整位于场景内；控制台移至场景下方，注入按钮可换行。截图已通过当前浏览器工具直接查看，结构测量记录见 [compact-browser.json](../artifacts/office-reuse/compact-browser.json)。本次未追加模型调用；375px 手机窗口未单独验收。
