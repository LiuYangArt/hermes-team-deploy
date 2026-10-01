# Docker 部署

当前 Compose 使用相邻的官方 Core 工程 `../hermes-team` 构建镜像，并复用受保护的 Hermes 状态目录挂载 Lark 凭据与运行状态。部署工程源码、任务包和真实凭据分离。

本机启动：

```bash
docker compose -f deploy/compose.yaml build --build-arg HERMES_GIT_SHA="$(git -C ../hermes-team rev-parse HEAD)"
docker compose -f deploy/compose.yaml up -d --no-deps hermes-team
```

上线前先停止旧容器，避免两个进程同时写同一状态目录。`hermes-team-new` 验证通过后，旧的 `hermes-team` 保留为停止状态用于回滚。

运行状态目录必须包含受保护的 `.env` 和 `data/`；不要提交或复制其中的凭据、会话、日志和任务状态。
