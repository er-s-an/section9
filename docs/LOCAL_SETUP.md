# Section9 本地安装与启停

本文档描述当前 Mac 上的开发者入口。它不是冷安装证明：只在本机验证过，独立的新 Mac、首次 Docker 镜像下载和资源峰值仍待验收。

## 安装一次

```sh
./scripts/install.sh
```

该命令初始化被 Git 忽略的本地配置，执行锁定的 Python/npm 安装并构建前端。角色素材是可选下载；失败会给出 warning，前端保留 fallback。安装完成后，启动和查看已有历史不再重复下载依赖或素材。

## 只读体检

```sh
./scripts/doctor.py
./scripts/doctor.py --json
```

体检只读取配置名/格式、依赖目录、端口（包括应用的 `S9_PORT+2` agent 入口）、Docker daemon 资源和实际 `/api/health`；不会调用模型、写数据库、启动 Docker 或打印凭据。空 `EVOMAP_MODEL_API_KEY`、非法端口/并发/超时/预算会明确失败并给出修复动作。Docker 未运行只标记观测能力不可用；低于约 8GiB/4CPU 会 warning，不会硬阻断。已构建的 `frontend/dist` 可供历史浏览；缺少 Node/GEP 依赖时，重新构建和官方 SDK 资产回写不可用。

## 启动与就绪

```sh
./scripts/start.sh
./scripts/readiness.py
./scripts/readiness.py --business
```

`start.sh` 只启动本项目应用，不同步依赖、不下载素材、不启动 Compose；运行时配置检查允许空 key，因此本地历史可以读取。`readiness.py` 默认只检查实际 `/api/health`，状态为 `service_alive` 或 `business_unchecked`；只有显式 `--business` 才调用现有 `/api/chat` 并校验固定退货 fixture 的 `structured.eligible=true`，失败为 `degraded`。空 key 永远不会报告业务就绪，也不会把 HTTP 200 单独当作业务成功。

## 停止

```sh
./scripts/stop.sh --app-only   # 只停本项目应用，保留观测栈供现有测试脚本使用
./scripts/stop.sh               # 停应用及本项目 section9-observe Compose，保留卷
```

完整停止只针对固定 Compose 项目名 `section9-observe`，不会停其他项目，也不会删除卷。再次启动观测栈需按既有 `scripts/infra-start.sh` 显式启动；应用启动不会替用户启动其他项目。

## 边界

历史读取使用本地数据，不依赖远程模型 key。模型、数据库、观测服务和素材的可用性分别报告；本地健康不等于业务模型已验收。当前文档与脚本没有新增配置字段，也没有改变核心 API 契约。
