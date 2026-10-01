# Docker 部署

给 IT 的安装步骤在仓库首页 `README.md`。只克隆本仓库即可。`scripts/build.sh` 会下载 `deploy/.env` 里钉住的公开程序版本，再构建基础镜像，然后通过本目录 Dockerfile 加入 Feishu 和本仓库里的话题、进门、个人授权。容器每次启动把它们放进运行目录并启用。个人授权留在运行目录，不进镜像。构建范围不含 jobs/、运行状态或凭据。词典（#21）和原图附件（#22）以后再做。

1. 将 `deploy/.env.example` 复制为 `deploy/.env`，设置独立状态目录和用户编号。不要改程序地址和版本号。
2. 把 `deploy/bot.env.example` 复制为状态目录里的 `bot.env` 再填写。模型用官方自带模板写成 `data/config.yaml`，不要另写一套。管理员名单和进门开关用 `extensions/lark-access/config.example.json`，复制到 `data/lark-access/config.json`。不要挂载旧 CN 的整个状态目录，否则会加载旧插件与任务。
3. 明确初始化机器人规则：将 `rules/SOUL.md` 复制到新状态目录 `data/SOUL.md`。升级镜像不会重新覆盖规则。
4. 执行以下命令（部署仓库根目录）：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
./scripts/build.sh
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --no-deps hermes-team
```

程序版本以 `deploy/.env.example` 里钉住的公开提交为准，构建脚本会下载它。不要改成另一份仓库的提交。

同一应用切换前停止旧网关，避免双重接收。新容器名 `hermes-team-new`；旧容器保留为停止状态以供回滚。没有端口向宿主机公开，Lark 使用出站 WebSocket。

日志：`docker logs --tail 100 hermes-team-new` 及状态目录 `data/logs/`；日志可能包含用户内容，分享前脱敏。镜像初始化需要 root，网关由官方入口降权到 hermes 用户。

本地验证产物在 `artifacts/`。云服务器、amd64/arm64、多成员行为和 Tasks 支持必须分别验收，不能以本机启动成功代替。
