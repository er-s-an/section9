# Star-Office-UI 素材来源审计

审计日期：2026-09-22。固定来源为官方仓库 commit [`f29c107e9728a72f2635f10b4e8203b29b37221d`](https://github.com/ringhyacinth/Star-Office-UI/commit/f29c107e9728a72f2635f10b4e8203b29b37221d)。本次只写入 `frontend/public/vendor/star-office/` 和本文件/归因文件，未修改 JS、CSS、依赖锁或运行服务。

## 许可边界

- 上游 [`LICENSE`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/LICENSE) 只将代码/逻辑置于 MIT；背景、家具、角色、动画等美术资产明确为非商业的学习、演示和交流用途。GitHub API 将仓库标为 `Other/NOASSERTION`，因此本审计没有把图片称为 MIT 素材。
- LimeZu 官方角色页 [Animated mini characters 2](https://limezu.itch.io/animated-mini-characters-2-platform-free) 明确允许免费/商业项目使用和修改，但禁止素材再分发或转售。角色图集只保留本机 gitignored 副本，由既有固定版本脚本获取，不随公开源码上传或重新托管。

## 已复制的完整办公室与家具

这次选择了官方暖色完整房间图 `office_bg.webp`，没有复制冬季变体 `office_bg_small.webp`。实际查看的 `office_bg.webp` 是 1280×720 的单帧 RGB 像素办公室背景，包含书架、灯、画框、服务器机柜、地面和空置区域，没有角色或状态文字；它可作为 Section9 响应室底图。另加两个透明前景家具：电脑/台灯/咖啡杯木桌，以及像素沙发椅。

| Section9 文件 | 上游路径/官方 raw | 实际元数据 | SHA-256 |
|---|---|---|---|
| `office_bg.webp` | [`frontend/office_bg.webp`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/office_bg.webp) / [raw](https://raw.githubusercontent.com/ringhyacinth/Star-Office-UI/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/office_bg.webp) | 1280×720，RGB，单帧，83,246 bytes；上游 blob `b1038a28023f34727cbc580e1d11801b842210f2` | `d7f79669f8e35f7155197aa88545e0998bc5759612089ead5a1e46299dd93116` |
| `desk-v3.webp` | [`frontend/desk-v3.webp`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/desk-v3.webp) / [raw](https://raw.githubusercontent.com/ringhyacinth/Star-Office-UI/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/desk-v3.webp) | 276×214，RGBA，单帧，53,926 bytes；上游 blob `1cd58bc2342d3b69d66e077691feb4c1e329f4b8` | `66b732cbfe469ae94e5d53e34f43463b553f210fdb367c16063f3b80fbf70736` |
| `sofa-idle-v3.png` | [`frontend/sofa-idle-v3.png`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/sofa-idle-v3.png) / [raw](https://raw.githubusercontent.com/ringhyacinth/Star-Office-UI/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/sofa-idle-v3.png) | 256×256，RGBA，单帧，50,802 bytes；上游 blob `24b29d20b43b968b34b0be75be1fa1c0d4fd2437` | `5108ee932e1a7f5dafbf0044158173750002dd1d3acef684b7e66a09d7dc3ce5` |

上游当前 loader 在 [`frontend/index.html`#L3977-L3991](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/index.html#L3977-L3991) 实际引用 `office_bg_small.webp`，并把 sofa、植物、海报、咖啡机、服务器区作为层加载。本次按任务要求采用暖色 `office_bg.webp`，所以与当前 loader 所用冬季地图的精确站位对齐仍应在接入时核验；没有把这个未知伪装成已实测兼容。

## 可用站位与层级坐标

官方 [`frontend/layout.js`](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/layout.js#L18-L115) 按 1280×720 画布给出可复用坐标：`writing/researching=(320,360)`、`error=(1066,180)`、`breakroom=(640,360)`、门口 `(640,550)`；sofa 左上角 `(670,144)`、desk 中心 `(218,417)`、工作角色 `(217,333)`、coffee machine `(659,397)`、serverroom `(1021,142)`、error bug `(1007,221)`、sync animation `(1157,592)`。desk 使用 origin `(0.5,0.5)`、depth 1000；sofa 使用 origin `(0,0)`、depth 10；工作角色 depth 900。它们是上游配置证据，不代表本次已改接入或在 Section9 浏览器中实测。

## 既有本地角色素材

目录中已有 `guest_anim_1.webp` 至 `guest_anim_4.webp`，每张 128×64 RGBA，按 32×32 分帧为 4×2 共 8 帧；上游映射为 `guest_anim_N_idle` 帧 0..7、8 fps、无限循环，证据在 [`frontend/index.html`#L4020-L4025](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/index.html#L4020-L4025) 和 [#L4060-L4067](https://github.com/ringhyacinth/Star-Office-UI/blob/f29c107e9728a72f2635f10b4e8203b29b37221d/frontend/index.html#L4060-L4067)。它们仅为本机获取的 gitignored 文件，不属于本次公开源码素材交付。

## 验证与限制

已用 Pillow 读取新增三件图片的真实尺寸、色彩模式和单帧属性，并将复制文件与固定 commit 的官方 raw 下载逐字节核对；SHA-256 已记录在上表。没有复制整个仓库、冬季背景、服务器/植物/海报等大图集，也没有修改前端源码。若项目改为商业用途，Star-Office-UI 美术资产不能继续沿用；若要公开角色原始图集，还必须遵守 LimeZu 的禁止再分发条款。

## Section9 实际接入

OfficeScene 以 React DOM/CSS 适配上述房间、家具和角色图集；不声称整体移植上游 Flask/Phaser 应用或采用其随机 demo 移动脚本。SSE/snapshot 提供真实状态，职责工位与 working 状态决定目标位置；暂停、离线、心跳和事件编号均可查看。位置按暖色地图实测调整，详情位于场景下方，不覆盖办公室。浏览器证据与限制见 [OFFICE_DELIVERY.md](OFFICE_DELIVERY.md)。
