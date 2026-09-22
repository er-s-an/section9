# Star-Office-UI 调研与归因

本次只检查了官方 GitHub 仓库 [ringhyacinth/Star-Office-UI](https://github.com/ringhyacinth/Star-Office-UI)，截至 2026-09-22 的仓库描述为 MIT 代码、艺术资产仅限非商业学习。检查目标是确认办公室像素化布局、角色状态和桌面设备的可复用方向。

Section 9 仅接入已审计的 4 张访客 idle spritesheet，位于 `frontend/public/vendor/star-office/guest_anim_1..4.webp`，按 32×32、8 帧、8fps 的原像素图集使用。文件旁保留上游 `LICENSE.txt` 与 `ATTRIBUTION.md`；这些美术资产只用于当前本机非商业 demo。角色状态、颜色描边、任务标签和 producer 消息均由 Section 9 实时 snapshot/SSE 提供，未从素材推断任务或行为。2026-09-22 核对 LimeZu 官方页面：允许用于免费和商业项目，但禁止素材再分发或转售。因此 Git 忽略原始角色图集，启动脚本从固定上游版本本地获取并校验 SHA-256；本仓库保留使用截图和归因。商业使用仍需遵守 Star-Office-UI 对其美术资产的非商业限制。

检查入口：仓库 API `https://api.github.com/repos/ringhyacinth/Star-Office-UI`，官方仓库页面见上方链接。仓库当前可见元数据：默认分支为 `main`，语言为 HTML，仓库描述含 pixel-art / multi-agent / dashboard 关键词。
