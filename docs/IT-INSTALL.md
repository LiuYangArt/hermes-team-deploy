# 安装说明

安装步骤在仓库首页，不在这份内部记录里。

把 https://github.com/LiuYangArt/hermes-team-deploy 发给 IT。他克隆这一个仓库，按首页做即可。

## 2026-10-03 已有云服务器接续配置

独立 Linux x86_64 容器已启用，使用部署交接时的固定镜像。运行配置和 SOUL.md 与本机受管 Docker 的文件哈希一致；同时复制实际使用的 `DEEPSEEK_API_KEY` 到服务器受保护的 `data/.env`。仅复制 config.yaml 内的模型密钥不足以供该内置模型提供商鉴权。

管理员必须使用目标 Lark 应用识别的用户标识，通过该应用读取群主和群成员核对。管理员名单负责团队工具权限；消息接入允许名单另设在 bot.env。本次只将指定管理员加入 `FEISHU_ALLOWED_USERS`，群策略保持 `allowlist`。

Lark 已有长连接但没有订阅 `im.message.receive_v1` 时，@机器人也不会进入处理。本次补充消息事件并发布应用 1.0.4，发布页面确认权限变更为 None，可用范围沿用原设置。

验证结果：

- `hermesctl preflight` 通过；`hermes chat -Q --oneshot --max-turns 2 --run-budget 90` 实际模型调用退出 0。
- 授权测试群的第一条成功请求得到“云端收发正常，代号已记住”；容器重启后原话题收到 `CEDAR103`，网关日志确认同一持久会话恢复且历史长度为 2。
- 真实回复由目标应用发出，话题保持一致；证据在本地忽略目录 `artifacts/issue-11/thread-final.json`，配置哈希及管理员核对在 `aws-config-verification.json`。
- 启动与重启日志中出现过连接超时及关闭连接时的错误；之后已连接且实际回复成功。当前健康脚本会受历史错误影响，不能仅凭该脚本判断当前不可用，须结合本次启动时间后的连接日志及真实请求回读。
- 未执行多成员权限对照、业务任务写入、完整 Linux 执行隔离验证；这些不计为本次已通过。Issue #11 保持待验收。

当前配置备份在服务器独立状态目录的 `data/backups/`，不进入 Git。日常入口仍为服务器现有 `hermesctl`，不将管理员身份或真实凭据写入文档。
