# Star-Office-UI 素材来源审计

审计日期：2026-09-22。仅审计并复制少量素材，未修改 Section9 前端源码、依赖或锁文件。

## 来源与许可

- 官方仓库：<https://github.com/ringhyacinth/Star-Office-UI>
- 固定来源 commit：[`f29c107e9728a72f2635f10b4e8203b29b37221d`](https://github.com/ringhyacinth/Star-Office-UI/commit/f29c107e9728a72f2635f10b4e8203b29b37221d)
- 上游许可原文：[`LICENSE`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/LICENSE)。它只将代码/逻辑置于 MIT；角色、背景、家具、动画、按钮皮肤等美术资产明确为**仅限非商业**的学习、演示和交流用途。GitHub API 将仓库 license 标成 `Other/NOASSERTION`，因此这里没有把图片称作 MIT 素材。
- 上游 README 对访客动画的归属是 LimeZu 的 [Animated Mini Characters 2 (Platformer) [FREE]](https://limezu.itch.io/animated-mini-characters-2-platform-free)，并要求二次发布/演示保留归属、遵循原作者条款。原作者条款未被本项目重新授权；任何公开再分发或商业使用前都必须重新核对。

## 已复制的最小素材

文件位于 [`frontend/public/vendor/star-office/`](../frontend/public/vendor/star-office/)，只复制了 4 个角色动画 WebP、上游 `LICENSE.txt` 和本归因文件。4 个图片均是透明 RGBA、128×64；按上游 Phaser 加载方式以 32×32 分帧，因此是 4 列 × 2 行、8 帧的像素角色图集。图片文件的 SHA-256 用于确认复制内容：

| Section9 文件 | 上游路径与直接来源 | 图集/动画元数据 | SHA-256 |
|---|---|---|---|
| `guest_anim_1.webp` | [`frontend/guest_anim_1.webp`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/guest_anim_1.webp) | 128×64；32×32 frame；8 frames；`guest_anim_1_idle` = 0..7，8 fps，repeat=-1 | `66eda7c39f233f7e1fd74696f4737a3e1ec51de0a63575f4616fb1646c74b254` |
| `guest_anim_2.webp` | [`frontend/guest_anim_2.webp`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/guest_anim_2.webp) | 同上；`guest_anim_2_idle` = 0..7，8 fps，repeat=-1 | `43ab6870067f634320a7090210de948b456ef231b50dca37e535c1144873ed37` |
| `guest_anim_3.webp` | [`frontend/guest_anim_3.webp`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/guest_anim_3.webp) | 同上；`guest_anim_3_idle` = 0..7，8 fps，repeat=-1 | `5f2f627a8f654987f952f32bb4e5b283555a06641f1e57faa206d198cd046825` |
| `guest_anim_4.webp` | [`frontend/guest_anim_4.webp`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/guest_anim_4.webp) | 同上；`guest_anim_4_idle` = 0..7，8 fps，repeat=-1 | `0f14865c3e1f89efd7172b1a6fef7146bae5374d94a8fb6e45ce10a59dbedd0e` |

上游运行时证据：[`frontend/index.html`#L4020-L4025](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/index.html#L4020-L4025) 明确以 `{ frameWidth: 32, frameHeight: 32 }` 加载 6 组访客图集；[#L4060-L4067](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/index.html#L4060-L4067) 明确将每组 0..7 帧做成 8 fps、无限循环的 idle 动画。访客选择与锚点证据在 [#L3560-L3576](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/index.html#L3560-L3576)：根据 `avatar` 的 `_1.._6` 选择图集，origin 为 `(0.5, 1)`，普通访客 scale 为 `4.0`。

## Section9 接入边界

前端代理可直接以 `guest_anim_N` 为 Phaser spritesheet，使用上述 32×32 / 0..7 / 8 fps 映射；本次没有改接入代码。原仓库代码可以按 MIT 另行参考，但这里没有复制其大段 HTML/JS。图片只能用于 Section9 本机非商业 demo；若项目变为商业或公开发布，必须替换这些美术资产或先取得兼容授权。

## 首次公开源码上传核对（2026-09-22）

LimeZu 官方页面明确允许素材用于免费/商业项目，但禁止再分发或转售素材。四张原始角色图集已加入 `.gitignore`，不随源码上传；`scripts/fetch-office-assets.py` 在本机获取上述固定来源并逐张校验哈希。项目截图/演示记录保留。上游背景/家具的非商业条款不变。
