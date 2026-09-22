# Star-Office-UI 调研与归因

本次审计固定官方仓库 [ringhyacinth/Star-Office-UI](https://github.com/ringhyacinth/Star-Office-UI) 的 `master` commit [`f29c107e9728a72f2635f10b4e8203b29b37221d`](https://github.com/ringhyacinth/Star-Office-UI/commit/f29c107e9728a72f2635f10b4e8203b29b37221d)。上游许可将代码/逻辑置于 MIT，但背景、家具、角色、动画等美术资产限定为非商业学习、演示和交流用途。

Section 9 当前采用并归因于 Star-Office-UI 的非商业背景/家具素材见 [`docs/STAR_OFFICE_SOURCE_AUDIT.md`](./STAR_OFFICE_SOURCE_AUDIT.md)。商业发布需替换这些美术资产或先取得兼容授权。

目录中已有 4 张访客 idle spritesheet：`frontend/public/vendor/star-office/guest_anim_1..4.webp`。它们是本机 gitignored 获取的 LimeZu 角色素材，不随公开源码分发。上游归因页面：[Animated Mini Characters 2 (Platformer) [FREE]](https://limezu.itch.io/animated-mini-characters-2-platform-free)。LimeZu 官方条款允许用于免费/商业项目并允许修改，但禁止原始素材再分发或转售；因此启动脚本只在本机按固定来源获取并校验，不重新托管图集。

角色动画使用 32×32 分帧、8 帧、8fps idle 映射；角色状态、颜色描边、任务标签和 producer 消息由 Section 9 实时 snapshot/SSE 提供，未从素材推断任务或行为。
