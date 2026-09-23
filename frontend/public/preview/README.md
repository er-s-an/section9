# Section9 协作空间预览

此目录保存 9027 交互预览的可复用源码；六步骤和角色行为是预设展示，未连接实时后端。

从仓库根目录运行：

```sh
python3 -m http.server 9027 --bind 127.0.0.1 --directory frontend/public
```

打开 http://127.0.0.1:9027/preview/ 。如果该端口已在使用，可换为 9028。

背景 `../vendor/section9/orange-lab-concept.png` 是为本项目生成的概念环境图，来源为本项目的 AI 图像生成设计过程。
该背景也复用于 `/demo` 的实时协作办公室；这里只共享素材，不共享预设步骤。

角色与家具从相邻的 `vendor/star-office` 读取，许可和获取方式见其中的 `ATTRIBUTION.md`；角色原始图集受 Git 忽略，本目录不复制或再分发。

验证：

```sh
S9_PREVIEW_URL=http://127.0.0.1:9027/preview/ node scripts/browser-product-preview.mjs
```

实际后端产品仍从 http://127.0.0.1:9019/ 打开。本预览不构成真实协作或模型验收记录。
