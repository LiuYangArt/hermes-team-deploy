# 本机官方后台切换记录

日期：2026-10-01。状态：已切换到新容器，2026-10-01 10:14（北京时间）完成单用户真实 Lark 收发回读。

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

## 已完成验收与范围

- 新容器 `hermes-team-new` 运行，旧 `hermes-team` 已停止并保留；旧 Tasks/ACP 两项 launchd 服务已停止并禁用。恢复旧服务时需先 enable，再 bootstrap。
- 官方基础镜像缺少 Feishu SDK，部署工程通过 `deploy/Dockerfile` 使用官方锁定依赖添加 `feishu` extra；没有修改 Core 业务代码。
- 本机平台为 Docker Desktop 下的 Linux arm64。实际网关进程以 hermes 用户运行，状态与工作区挂载到新的独立目录。
- 模型冒烟调用返回 `HERMES_OFFICIAL_OK`；证据：`artifacts/model-smoke.log`。
- 真实 Lark 测试先触发官方配对流程，批准当前测试用户后再次发送，机器人回复 `HERMES_OFFICIAL_OK`。
- 请求 ID：`om_x100b64e851f6c8a4e2f8ab625321453`；回复 ID：`om_x100b64e851773ca0ff4f0c487d3c7f5`；回读 `reply_to` 与请求 ID 一致。
- 写入及回读证据：`artifacts/lark-e2e-send-2.json`、`artifacts/lark-e2e-receive-2.json`；配对证据：`artifacts/lark-pairing-approve.log`。这些文件仅本地保存，分享前脱敏。

本次只证明一个已批准用户的真实基础收发与回复关联，未证明群聊原生话题、跨话题隔离、多成员审批或旧 Lark 定制已迁移。测试对话的群聊/私聊类型尚未单独回读核定，不能仅凭 chat_id 或发送了 @ 就称为群聊验收。

新后台没有设置 home channel；这只是当前配置状态，不能作为任务隔离的证明。旧任务暂停的原因是旧后台已停止且任务尚未迁移。

云端 amd64/arm64 支持、健康检查、完整配置/权限模板、任务发布隔离与回滚演练仍见 PLAN.md 的未勾选项。
