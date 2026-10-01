# Docker 部署

`scripts/build.sh` 从相邻 `hermes-team` 构建官方基础镜像，再通过本目录 Dockerfile 使用官方依赖锁加入 Feishu extra；构建范围不含部署仓库的 jobs/、运行状态或凭据。当前实现是官方最小机器人，新团队扩展与独立任务尚未迁移。

1. 将 `deploy/.env.example` 复制为 `deploy/.env`，设置独立状态目录、宿主用户 UID/GID 及实际 Core 提交。
2. 在受保护状态目录创建 `bot.env`（Lark 应用凭据），`data/config.yaml`（模型与平台配置）和 `workspace/`。不要挂载旧 CN 的整个状态目录，否则会加载旧插件与任务。
3. 明确初始化机器人规则：将 `rules/SOUL.md` 复制到新状态目录 `data/SOUL.md`。升级镜像不会重新覆盖规则。
4. 执行以下命令（部署仓库根目录）：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
./scripts/build.sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-deps hermes-team
```

更改 Core 后先将 `HERMES_CORE_REVISION` 更新为当前提交，再构建和启动。镜像标签必须与实际提交一致，构建不会替你 checkout。

同一应用切换前停止旧网关，避免双重接收。新容器名 `hermes-team-new`；旧容器保留为停止状态以供回滚。没有端口向宿主机公开，Lark 使用出站 WebSocket。

日志：`docker logs --tail 100 hermes-team-new` 及状态目录 `data/logs/`；日志可能包含用户内容，分享前脱敏。镜像初始化需要 root，网关由官方入口降权到 hermes 用户。

本地验证产物在 `artifacts/`。云服务器、amd64/arm64、多成员行为和 Tasks 支持必须分别验收，不能以本机启动成功代替。
