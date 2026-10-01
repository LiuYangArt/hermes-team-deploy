# 本机官方后台切换记录

日期：2026-10-01。状态：镜像构建中，尚未切换。

## 归属与运行目录

- Core：相邻 `hermes-team`，官方基线，当前提交 `3057b821787cae30a423525790c87e634b1c546e`。
- 本次源码改动只属于部署工程的 `deploy/`、公共规则 `rules/` 及文档。
- 新运行目录：`~/.local/share/hermes-team-official`，独立 data/ 与 workspace/。
- 复用现有应用身份和模型配置；不复制旧会话、旧插件、旧任务、个人授权或旧全局业务提示词。
- 旧 `~/.local/share/hermes-team` 保持原样；旧容器切换后保留用于回滚。
- 新容器 `hermes-team-new`，Compose project `hermes-team-official`。

## 切换顺序

构建新镜像 → 校验配置 → 停止旧网关和依赖它的两项 Tasks/ACP 服务 → 启动新容器 → 核对网关、应用身份、Lark 连接及模型请求 → 验收真实 Lark 对话。

如新容器无法提供服务，先停新容器，再启动旧 `hermes-team` 并恢复原先运行的 Tasks/ACP 服务。不得同时启动两套相同应用身份的网关。

旧 Cron 与 Tasks/ACP 不属于本次最小官方机器人，停旧后台后暂停，源码及运行状态保留。后续按计划分别迁移，不把任务脚本混进公共镜像。

## 构建诊断

首次构建卡在 Docker Hub 元数据读取；进程证据显示 `docker-credential-desktop get` 长时间未返回。使用临时、无认证 Docker 客户端配置拉取公开镜像后恢复正常。没有修改全局 Docker 登录配置。

构建日志：`artifacts/docker-build.log`（本地、不入 Git）。Compose 检查：`docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet`。
