# Hermes Team 云端部署工程

状态：正在实施官方最小机器人 Docker 部署，实际验收见 docs/DEPLOYMENT.md。

- Core：https://github.com/LiuYangArt/hermes-team
- 官方：https://github.com/NousResearch/hermes-agent
- 本仓库：团队扩展、容器编排、IT 运维文档。
- 从 [实施计划](docs/PLAN.md) 开始，逐项完成验收后再推进。

## 目录

- `extensions/`：Lark 团队扩展；优先官方插件接口。
- `deploy/`：Docker Compose 和配置模板。
- `jobs/`：各任务独立的源码、规则和测试；不随机器人更新发布。
- `tests/`：部署与团队行为验收。
- `docs/`：计划、架构和 IT 手册。
- `artifacts/`：本地验证证据，不提交。

初始阶段验证：`python3 scripts/check_scaffold.py`。
本地构建与启动入口见 [Docker 部署](deploy/README.md)。云端 IT 交付仍按计划逐项验收。
