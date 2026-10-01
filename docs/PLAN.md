# Hermes Team：官方基线与云端 Docker 交付计划

日期：2026-10-01。状态：开始官方 Docker 最小部署；用户已授权接管现有 Lark 机器人。

## 目标与验收标准

团队成员在 Lark 群内 @机器人建立话题，围绕当前话题使用工具；IT 按文档在 Linux 云服务器通过 Docker Compose 部署同一套能力，并可升级、诊断和回滚。

上下文只关注当前话题和 SOUL.md / AGENTS.md / 技能等规则。不同话题隔离，不主动检索其他话题、不自动把讨论写成公共记忆。会话保存和压缩沿用官方方案；旧聊天历史不迁移。凭据及现有任务状态的切换策略稍后明确，不在本阶段删除。

## 两个工程的职责

| 工程 | 责任 |
| --- | --- |
| hermes-team | 保留官方完整 Git 历史、官方 upstream；仅实现无法通过插件完成的必要补丁 |
| hermes-team-deploy | 团队插件、规则模板、Docker 构建和 Compose、IT 手册、独立任务包 |

旧 hermes-agent 仅作为功能和历史参考，不能整目录覆盖新 Core。GitHub 账号已有同网络 fork，因此新 Core 是独立仓库，不显示 fork 标签，但沿用官方历史及 upstream 更新关系。没有导入 CN 提交。

## 本地工程路径与移植参考入口

以下是当前 Mac 上的实际路径。后续 agent 从新工程继续工作时，应自行跨目录读取旧实现与测试，不要求用户手动切换工程。这些路径用于开发参考，不得硬编码到云端部署配置或镜像中。

| 用途 | 本地绝对路径 |
| --- | --- |
| 统一工作入口 | `/Users/apple/CodeProjects/hermes-team-workspace`（`core/`、`deploy/` 分别链接到下列新工程） |
| 新官方 Core，必要通用补丁的目标 | `/Users/apple/CodeProjects/hermes-team` |
| 新部署工程，扩展、规则、任务和文档的目标 | `/Users/apple/CodeProjects/hermes-team-deploy` |
| **已做 Lark 团队定制的旧 Hermes CN 完整源码，移植来源** | **`/Users/apple/CodeProjects/hermes-agent`** |

旧工程参考快照：2026-10-01 核对时 HEAD 为 `ac0b6703c0`，工作区无未提交改动。开始移植前重新执行 `git -C /Users/apple/CodeProjects/hermes-agent status --short` 和 `git -C /Users/apple/CodeProjects/hermes-agent log -12 --oneline`，以实际源码与历史为准，不只参考远端 CN 仓库。

### 旧功能具体从哪里找

| 参考内容 | 旧工程入口 | 新工程归属 |
| --- | --- | --- |
| 团队维护说明、资产清单及安装逻辑 | `/Users/apple/CodeProjects/hermes-agent/team/AGENTS.md`、`/Users/apple/CodeProjects/hermes-agent/team/README.md`、`/Users/apple/CodeProjects/hermes-agent/team/sources.json`、`/Users/apple/CodeProjects/hermes-agent/team/manage.py` | 先用于清点，不整体复制安装器 |
| Lark 收发、话题、引用、进度及最终回复 | `/Users/apple/CodeProjects/hermes-agent/plugins/platforms/feishu/`，重点为 `adapter.py`、`thread_router.py`、`thread_state.py`、`reply_state.py` | 优先官方已有能力，其次新部署工程 `extensions/`；必要通用 Core 补丁登记到 `docs/PATCHES.md` |
| 团队权限、请求者身份、词典和执行约束 | `/Users/apple/CodeProjects/hermes-agent/team/governance.py`、`/Users/apple/CodeProjects/hermes-agent/team/lingo.py`、`/Users/apple/CodeProjects/hermes-agent/team/sandbox.py` | 按通用能力拆到新部署工程 `extensions/`，不夹带单任务规则 |
| 旧插件与技能 | `/Users/apple/CodeProjects/hermes-agent/team/plugins/`、`/Users/apple/CodeProjects/hermes-agent/team/skills/` | 逐项审查并按用途归入扩展或独立任务，不整体启用旧插件 |
| Lark Tasks / ACP 桥接 | `/Users/apple/CodeProjects/hermes-agent/team/task-bridge/`、`/Users/apple/CodeProjects/hermes-agent/team/acp/` | 公共桥接在新部署工程 `extensions/`，容器编排在 `deploy/`；任务专属规则归对应任务 |
| Meegle 分诊、翻译、负责人判断 | `/Users/apple/CodeProjects/hermes-agent/team/jobs/meegle-triage/` | `/Users/apple/CodeProjects/hermes-team-deploy/jobs/meegle-triage/` |
| 每日摘要任务 | `/Users/apple/CodeProjects/hermes-agent/team/jobs/daily-summary/` | `/Users/apple/CodeProjects/hermes-team-deploy/jobs/daily-summary/` |
| 团队行为回归测试与排障记录 | `/Users/apple/CodeProjects/hermes-agent/team/tests/`、`/Users/apple/CodeProjects/hermes-agent/team/postmortems/` | 公共行为测试放新部署工程 `tests/`；单任务测试随所属 `jobs/<job-name>/` |

上表是查找入口，不是完整改动清单。旧功能还可能涉及 `/Users/apple/CodeProjects/hermes-agent/gateway/`、`/Users/apple/CodeProjects/hermes-agent/hermes_state.py` 等通用代码；从入口追踪调用和 Git 历史，避免只复制 adapter 而漏掉其依赖。

### 源码与运行资产的边界

- 旧运行资产：`/Users/apple/.local/share/hermes-team`；旧共享工作区：`/Users/apple/HermesTeamWorkspace`。仅在核对部署差异时按需读取，不能当作新工程源码或整目录导入。
- 新运行资产：`/Users/apple/.local/share/hermes-team-official`；新容器：`hermes-team-new`。后续移植在新工程实现、构建和验收，旧 CN 源码只作参考。
- 状态目录及旧工程 `team/.local/` 可能包含凭据、个人授权、内部内容和日志，不能提交、打包进镜像或直接复制进计划文档。
- 每移植一项，记录旧实现位置、对应测试、官方是否已覆盖、新归属与验收结果。单个任务的脚本、提示词、依赖和业务规则必须留在该任务目录，不能因旧代码曾混入本体就照搬其结构。

## 设计边界

1. 官方框架负责对话、工具执行、Cron、存储；不要另造引擎。
2. Lark 团队行为优先插件；缺少扩展入口时提炼小型通用补丁，记录原因、官方基线、测试、重叠 PR。
3. Docker 镜像固定 Core 提交及依赖版本。IT 获取发布镜像，不现场解决 Git 合并。
4. 配置、凭据、规则及运行数据持久化；任务包使用独立目录及单任务发布流程，公共升级不覆盖。
5. 发布边界与执行隔离不同；任务对共享解释器、CLI 和官方 API 的依赖必须纳入升级回归。
6. 普通成员、管理员、机器人、Tasks/ACP 身份分离；审批绑定当前发起者。

## 分阶段实施

### 0. 基线与脚手架

- [x] 新建独立 GitHub 仓库和 CodeProjects 目录。
- [x] Core 直接取自官方，配置 origin/upstream，不导入 CN 历史。
- [x] 落盘计划、目录和工程约束。
- [ ] 清点旧功能，逐项判定官方已具备 / 插件 / 必要补丁 / 不迁移。

### 1. 最小官方端到端

- [ ] 固定已审查的官方提交，检查官方 Docker、插件及 Feishu 接口。
- [ ] 在独立运行目录复用现有机器人身份，跑通官方 Hermes → Lark 收到请求 → 回答。
- [ ] 确认群/话题标识和消息归属，明确新话题及引用回复的预期行为。
- [ ] 记录基线测试、日志及已知限制；按本次授权停止旧后台并接管现有机器人。

### 2. Linux Docker 最小交付

- [ ] 提供 Dockerfile、Compose、`.env.example`、规则与权限配置模板。
- [ ] 先验证目标 Linux 架构；记录 amd64/arm64 支持矩阵，不未经测试声称均支持。
- [ ] 无 macOS 路径、UID 或 launchd 硬编码；非 root 运行、持久卷权限、重启策略和健康检查。
- [ ] 预检凭据是否配置、出站网络、目录权限、所需隔离能力；日志脱敏。
- [ ] 发布固定标签/摘要镜像；IT 在干净服务器按文档完成启动和 Lark 验收。

### 3. 恢复团队 Lark 行为

- [ ] 按成员 @参与、/listen 旁听及恢复；不同话题上下文隔离。
- [ ] 同话题按成员排队，当前请求者才能停止、审批和回答澄清。
- [ ] 进度与最终回答复用消息，覆盖迟到更新、取消、失败、长答案及附件。
- [ ] 引用消息内容与原生话题归属；明确无法读取附件时的行为。
- [ ] 群聊 Lark CLI 固定机器人身份，管理员操作与普通业务操作分离。
- [ ] 原图上传任务附件并回读；项目词典和业务工具按插件接入。
- [ ] 不机械复用旧 adapter；保留官方新实现，按行为契约移植。

### 4. Tasks/ACP 与任务资产

- [ ] 将现有桥接改为 Linux 容器服务，通过 Compose 管理；验证依赖许可和架构。
- [ ] 保留专用身份与审批保护，跑通真实 Tasks 请求和结果回读。
- [ ] 每个定时任务独立脚本、配置、测试、状态与发布；不修改 Core 业务结构。
- [ ] 验证 Core 升级、任务 A 发布不会覆盖任务 B；明确脚本解释器/依赖契约。
- [ ] 迁移或重建旧任务的范围届时确定，保留正文翻译等已需业务能力。

### 5. 云端验收与 IT 文档

- [ ] 文档覆盖首次安装、Lark 平台必要配置、健康检查、日志、备份、升级和回滚。
- [ ] 多成员实测：同话题换人、跨话题隔离、成员拒绝、管理员允许、审批归属。
- [ ] 验证断网重连、容器重启、任务失败、图片附件和长答案。
- [ ] 区分镜像回滚与数据兼容性；需要备份时先完成备份验证。
- [ ] 提供一条可复制的安装路径、镜像版本及验收证据，才标记可交付。

### 6. 官方更新流程

- [ ] fetch upstream → 审查发布与接口变化 → 合并必要补丁 → 定向回归 → 构建候选镜像 → 隔离 Lark 验收 → 发布版本。
- [ ] 维护补丁清单，官方已实现的补丁删除；向上游提 PR 须用户另行确认。
- [ ] 任务发布不包含在引擎升级流程中。

## 旧功能评估注意

上游开放 PR 不等于主线已有功能。话题/引用/进度与 #94141、#98376、#60719、#81226、#115172 有重叠，实施时重新核对状态及实际代码。
之前报告曾将 Git 左右提交数量反读，不能据此估计工作量；以官方快照、接口变化和行为测试判断。

## 本阶段不做

不合并旧 CN 分支、不迁移聊天历史或旧插件、不自动创建 PR。当前授权覆盖停止旧后台、复用现有 Lark 应用与模型配置到独立运行目录并启动新后台。旧任务与 Tasks/ACP 暂不迁移，其状态保留；停止旧后台期间不会运行旧定时任务。
