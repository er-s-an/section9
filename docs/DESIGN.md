# Section 9 frontend design

前端采用“本机运营控制室”而不是营销页：左侧是像素化客服响应室，右侧是可操作的控制面板，底部展示真实客服会话与 SSE 事件。角色使用 `public/vendor/star-office` 中经审计的本地 spritesheet，按原始 32×32 帧和 crisp 像素渲染；状态描边、文字与消息仍完全来自 Section9 snapshot/SSE。橙色仅用于动作和警示，明亮炭灰背景用于低照度实验室层次。

## 交互边界

- 所有动态状态来自 `GET /api/state` 与 `GET /api/events?after=...`；SSE 断开后仍使用定时快照刷新，并在顶栏标记离线。
- 注入、聊天、自治等级、禁言、审批、代理暂停/恢复、成本专家、fencing、Attract、reset 均通过合同中的真实接口发送，响应后刷新快照。
- 运行记录、Playbook 和五行计分通过独立页签读取对应 API。空数据显示“待测/暂无记录”，不会用演示数值代替。
- CSS 绘制场景角色、桌面和设备，避免引入无法核验来源的媒体。所有状态同时显示文字、时间或 revision/generation。

## 可访问性和退化

焦点态、输入标签、禁用态和错误条均保留；`prefers-reduced-motion: reduce` 会停用循环动画。桌面目标宽度为 1280–1440px，窄屏仍保持主要操作可见。后端不可用时 UI 显示离线与错误，不假装在线。
