# Hermes Team：官方基线与云端 Docker 交付计划

2026-10-03 Issue #29 简化收尾：审批测试删除已无调用者的队列查询和国际化模拟依赖，保留全部行为断言；审批、真实队列隔离和回复回归共 24 项通过。此次仅调整测试，无运行行为变化。

日期：2026-10-01。状态：本机官方 Docker + Lark 基础收发已完成；功能移植、Linux 云端和多成员验收待继续。

清单标记：`[x]` 已有可回读证据；`[ ]` 尚未完成；部分完成项会在条目后写明已完成范围和剩余范围。

## 目标与验收标准

团队成员在 Lark 群内 @机器人建立话题，围绕当前话题使用工具；IT 按文档在 Linux 云服务器通过 Docker Compose 部署同一套能力，并可升级、诊断和回滚。

上下文只关注当前话题和 SOUL.md / AGENTS.md / 技能等规则。不同话题隔离，不主动检索其他话题、不自动把讨论写成公共记忆。会话保存和压缩沿用官方方案；旧聊天历史不迁移。现有机器人凭据和模型配置已复用到新独立运行目录；旧任务状态保留，迁移范围后续确定。

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
6. 权限分三层。本体、管理员名单、进门开关、凭据和插件安装，机器人不能改。无人值守时危险命令的批准开关也属于这一层。技能、长期记忆、人设、模型切换和定时任务，只允许已登记管理员在 Lark 里让机器人改；谁都可以查看已有定时任务。一般任务任何人可做，产物放独立存储，每天 4:22 删除超过 7 天的文件。Lark 进来的人默认自动记下并放行，不逐人发验证码；开关可关回官方验证码。不在 Hermes 里另做外部人拒绝。任务审批仍绑定当前发起者。详见 `hermes-team-deploy#15` 与 `hermes-team-deploy#25`。

## 分阶段实施

### 0. 基线与脚手架

- [x] 新建独立 GitHub 仓库和 CodeProjects 目录。
- [x] Core 直接取自官方，配置 origin/upstream，不导入 CN 历史。
- [x] 落盘计划、目录和工程约束。
- [x] 清点旧功能，逐项判定官方已具备 / 插件 / 必要补丁 / 不迁移；差异表见 `docs/FEATURE-DIFF.md`，对应 GitHub Issue #4。

### 1. 最小官方端到端

- [x] 固定官方 Core 提交 `3057b821787cae30a423525790c87e634b1c546e`，检查官方 Docker、插件及 Feishu 接口。
- [x] 在独立运行目录复用现有机器人身份，跑通官方 Hermes → Lark 收到请求 → 回答；回读消息 ID 为 `om_x100b64e851773ca0ff4f0c487d3c7f5`。
- [x] 确认群/话题标识和消息归属；`my bots` 已确认是授权普通群（真实群 ID 仅保存在本地证据；`chat_mode=group`、`group_message_type=chat`）。首次 @ 的真实回读仍只有 `root_id/parent_id`、没有 `thread_id`，因此不会自动开启原生话题；已用运行中的 Hermi 身份在两个已有原生话题内完成连续回复、引用锚定回读和隔离验证。精确证据见 `artifacts/issue-5/evaluation.json` 及同目录回读文件。
- [x] 记录基线测试、日志及已知限制；已停止旧后台并接管现有机器人。证据见 `docs/DEPLOYMENT.md` 和本地 `artifacts/lark-e2e-receive-2.json`。

### 2. Linux Docker 最小交付

- [x] 提供 `deploy/Dockerfile`、`deploy/compose.yaml`、`deploy/.env.example`、`rules/SOUL.md` 和构建入口 `scripts/build.sh`；Feishu 依赖已固化到镜像。启动时把镜像里的 Lark、Meegle 和团队扩展放进运行目录并启用，不用再单独安装。
- [x] 可交给 IT 的空白件已有：机器人凭据栏 `deploy/bot.env.example`，管理员名单和进门开关 `extensions/lark-access/config.example.json`。模型继续用官方自带模板，不另写一份。安装步骤见 `docs/IT-INSTALL.md`。
- [ ] 先验证目标 Linux 架构；已在 macOS Docker Desktop 的 Linux arm64 容器完成构建、运行和 Lark 收发；独立 Linux 服务器和 amd64 尚未验证。
- [x] Compose 的状态目录、UID/GID 已参数化，设置持久挂载和重启策略；新网关不依赖 launchd，实际网关进程以 `hermes` 用户运行（初始化仍使用 root）。
- [ ] 健康检查脚本和安装前检查已有，日志输出会藏掉密钥。云端目录权限和执行隔离还要在真实 Linux 服务器上验收。
- [x] 完成本机模型调用、Lark 连接、独立目录挂载和容器启动检查；测试产物在本地 `artifacts/`。
- [x] 凭据、网络、目录权限和隔离检查已写成 IT 可执行的安装前检查，日志脱敏由检查脚本和单元测试覆盖。云服务器上再跑一遍仍属于后续验收。
- [ ] 发布固定标签/摘要镜像。本机已按官方版本 `3057b821787cae30a423525790c87e634b1c546e` 重新构建 `hermes-team-official`，镜像里已有话题、进门和个人授权。还没发布出去，也没在亚马逊云上启动。当前正在跑的容器仍是构建前的那份。

### 3. 恢复团队 Lark 行为

- [ ] 话题里最近 @ 过机器人的人接着说就是对机器人说；他改 @ 别人且没有 @ 机器人时整条话题安静并记下上下文，任何人再 @ 机器人后恢复。同时 @ 机器人和别人仍算叫机器人。多成员同时在场的真实对话还没验。
- [ ] 同话题后来的请求排队，不打断正在做的事。只有这件事的发起人能停止、批准和回答确认。
- [ ] 进度与最终回答复用消息，覆盖迟到更新、取消、失败、长答案及附件。
- [ ] 引用消息内容与原生话题归属；本轮已验证已有原生话题内 Q1 的精确引用复述及 `thread_id/root_id/parent_id`，主群无 `thread_id` 的引用输入、附件行为仍未覆盖。
- [ ] 同事进门、三层权限和超过 7 天的文件清理已在测试群验证（`hermes-team-deploy#15`）。Lark 与 Meegle 以技能接入（`hermes-team-deploy#9`）。Lark 公共操作使用机器人身份；个人授权由专门入口按发起人管理，共享机器人身份仍不可在对话中修改。看自己的 Lark 任务和 Meegle 已按说话人分开（`#23`，`extensions/personal-auth/`）；两个真人分别回读还要等人各自授权。词典（`#21`）和原图附件（`#22`）以后再做。
- [ ] 不机械复用旧 adapter；保留官方新实现，按行为契约移植。

### 4. Tasks/ACP 与任务资产

- [ ] 将现有桥接改为 Linux 容器服务，通过 Compose 管理；验证依赖许可和架构。
- [ ] 保留专用身份与审批保护，跑通真实 Tasks 请求和结果回读。
- [ ] 每个定时任务独立脚本、配置、测试、状态与发布；不修改 Core 业务结构。
- [ ] 验证 Core 升级、任务 A 发布不会覆盖任务 B；明确脚本解释器/依赖契约。
- [ ] 迁移或重建旧任务的范围届时确定，保留正文翻译等已需业务能力。

### 5. 云端验收与 IT 文档

- [ ] 文档覆盖首次安装、Lark 平台必要配置、健康检查、日志、备份、升级和回滚。
- [ ] 多成员实测：同话题换人、跨话题隔离、同事自动放行、越权拒绝、管理员可改共享设定、审批归属。
- [ ] 验证断网重连、容器重启、任务失败、图片附件和长答案。
- [ ] 区分镜像回滚与数据兼容性；需要备份时先完成备份验证。
- [ ] 提供一条可复制的安装路径、镜像版本及验收证据，才标记可交付。

### 6. 官方更新流程

- [ ] fetch upstream → 审查发布与接口变化 → 合并必要补丁 → 定向回归 → 构建候选镜像 → 隔离 Lark 验收 → 发布版本。
- [ ] 维护补丁清单，官方已实现的补丁删除；向上游提 PR 须用户另行确认。
- [ ] 任务发布不包含在引擎升级流程中。

## 下一步顺序
- Session 协作：工作会话完成或受阻主动回报；主会话负责等待、核验证据并在当前授权目标内继续推进，不依赖用户传话。规则已写入各入口 AGENTS.md；规则本身不代表已建立后台监控。

- 测试授权：用户已指定 Lark 测试群 **mybots**（界面名称 “my bots”）；群聊话题、引用和跨话题隔离验收可在该群执行，范围规则见 `AGENTS.md`。
- Session 归属：后续任务必须在 Codex 的 `hermes-team` 项目和统一工作区创建；公共区域/Projectless Session 不作为正式入口。


1. Issue #6、#7、#23 已改为待验证（`Pending Verification`）：对谁说话、排队，以及按说话人分开的个人授权，本机检查都已完成。还差第二个人在测试群发言，或两位同事各自授权后再回读。这三张单保持打开，先不做。
2. 权限和进门 `hermes-team-deploy#15` 已完成。官方 Lark / Meegle 工具已装进当前机器人（`#9`），飞书固定为机器人身份。测试群里已让机器人用自己的身份发了一条消息并回读，没有个人授权。Meegle 仍没有可用凭证，第 9 号因此还不能关。
3. 进度与最终回复（`#8`）已改为待验收：同一轮进度和最终回答共用一条消息。取消、失败、超长回答和读不了的附件还没在测试群回读。没有会话在改代码。
4. `#10` 的单仓库安装说明已推送，`#11` 的云端模型、群聊收发和重启恢复已通过。下一步补健康检查对历史错误的处理、执行隔离和多人场景；这些验收尚未完成。
5. 管理员在 Lark 里管理定时任务（`#25`）待验收。飞书会话原先没有定时任务工具；已经补上，并且只允许管理员改任务。无人值守批准开关仍然不能在对话里改。还差在测试群让管理员再问一次现有定时任务。

## GitHub Issue 与 Project 工作流

- [x] 建立私有跨仓库 Project：[Hermes Team](https://github.com/users/LiuYangArt/projects/3)，关联 `hermes-team` 与 `hermes-team-deploy`。
- [x] 首批任务已登记；差异清点 `LiuYangArt/hermes-team-deploy#4` 已关闭。实际待办与状态从 Project/Issue 回读，不在规则中固定任务总数。
- [x] 完整任务生命周期写入 `AGENTS.md` 的“GitHub Issue / Project 工作流”：查重建单 → 归属及父子关系 → 开工 → 验收证据 → 关闭并同步 Project → 回读；另含阻塞、取消、重复和重开规则。
- [x] 统一工作区与 Core 的 AGENTS.md 指向同一规范，避免三份流程各自变化；本次规则任务：[Deploy #20](https://github.com/LiuYangArt/hermes-team-deploy/issues/20)。
- [x] 文档检查通过：两个仓库 `git diff --check`、`python3 scripts/check_scaffold.py`；规则交付与后续本地提交分开记录；未推送或变更运行服务。检查与远端任务回读证据放 `artifacts/issue-workflow/`。

后续以 Issue 记录任务与验收事实，本计划保留阶段顺序、架构和摘要。只确认通用接口缺口后才建 Core 实现 Issue。Project 状态由 agent 显式同步；本地 Stop hook 不验证 GitHub 状态，也未因此新增自动化。

## 旧功能评估注意

上游开放 PR 不等于主线已有功能。话题/引用/进度与 #94141、#98376、#60719、#81226、#115172 有重叠，实施时重新核对状态及实际代码。
之前报告曾将 Git 左右提交数量反读，不能据此估计工作量；以官方快照、接口变化和行为测试判断。

## Codex 交付记录

- [x] 增加工作区级 Stop hook：有 Core/Deploy 改动时，在任务结束前检查对应计划、说明文档或任务清单是否同步。
- [x] 按会话保存开始快照，避免把回合开始前已有的脏改动误算为本回合完成；任务目录之间的文档不互相抵销。
- [x] 15 项临时双仓库测试通过，覆盖只读回合、公共改动、单任务隔离、提交后改动、删除文档、中断续跑、空白/旧清单误放行和重复安装；详见 docs/CODEX_HOOKS.md。
- [ ] 首次在 Codex 中运行 `/hooks`，审查并信任 `.codex/hooks.json`；这是本机配置生效所需的一次性操作。

## 本阶段不做

不合并旧 CN 分支、不迁移聊天历史或旧插件、不自动创建 PR。当前授权覆盖停止旧后台、复用现有 Lark 应用与模型配置到独立运行目录并启动新后台。旧任务与 Tasks/ACP 暂不迁移，其状态保留；停止旧后台期间不会运行旧定时任务。

## 2026-10-01 本地提交与验收复核

- [x] 审查现有规则、差异表及 Codex hook 源码和测试；运行 15 项 hook 测试、脚手架和两仓库差异检查。
- [x] 用户授权分别提交 Core 规则与 Deploy 文档/hooks；不推送，不创建分支，不将 artifacts 或真实群 ID 纳入提交。
- [x] Issue #5 已有原生话题连续性、Q1 精确引用复述和双话题隔离均有真实回读；首次普通群聊 @ 自动开话题及不带 @ 的后续参与仍未实现。当前先评估 Deploy 薄平台适配器并固定 Core 基线；只有适配器接缝经实证不足时才建立通用 Core Issue。

## 2026-10-01 Issue #5 补验与架构结论

- [x] `my bots` 中的 A/B 两个原生话题由受管 Hermi 容器身份处理；A 回读 `ORCHID742/靛蓝`，B 回读 `BIRCH593/未约定`，上下文没有串线。容器日志与 Lark API 回读均保存在 `artifacts/issue-5/`，源码与运行镜像关键文件 SHA 一致。
- [x] A 话题内 Q1 引用回复精确复述被引用原文“A已记住。”，并回读 A 的代号与颜色；请求和回答的 `thread_id/root_id/parent_id` 均保持在 A。该证据覆盖已有话题内引用，不覆盖主群无 `thread_id` 的引用输入。
- [x] A2 不带 @ 的后续消息未产生 Hermi 回答，也未进入持久处理会话；这与官方默认 @ 门控行为一致，但没有逐条拒绝日志，未把无回答误判为某个单一内部层的根因。
- [x] 只读对照旧 CN `thread_router.py`、`thread_state.py`、`reply_state.py` 与官方 adapter：旧实现把无 thread 的群消息用 `message_id` 建本地 topic，并维护成员/队列/回复状态；官方只在已有 `message.thread_id` 时设置 thread metadata。
- [x] 方案归属：话题参与、成员状态和别名应在 Deploy 扩展。优先评估继承当前 FeishuAdapter 的薄平台适配器，覆盖批处理前归一、首答锚定/发送回读和别名绑定；保留官方连接、鉴权、收发与错误处理并固定 Core 基线。`pre_gateway_dispatch` 和 `on_processing_*` 单独不足，但本轮没有证据证明必须修改 Core。
- [x] 测试群第一次 @ 自动开出原生话题。同一话题里不带 @ 的下一句仍走这条讨论，并答出首轮约定的代号。另一个新的 @ 开了第二条话题，回忆时没有答出前一条的代号。证据在本地 `artifacts/issue-5/cedar-thread-2.json`、`maple-thread-2.json`。
- [x] 主群里没有话题编号的引用回到了原话题；发送失败没有发到群顶层；重启后同一话题仍答出自己的代号。临时登记已清掉。证据在本地 `artifacts/issue-5/`。

- [ ] Issue #5 本回合自动文档核验未完成：Stop 缺少开始快照，配置重装未改变定义，诊断复跑仍阻止结束。独立测试通过不代表自动核验通过；证据 `artifacts/issue-5/docs-hook-failure.json`，后续正常回合需先确认开始事件和快照。

## 2026-10-03 云服务器接入验收（Issue #11）

- [x] 已在独立 Linux x86_64 服务器启用现有 Hermes 容器，复用本机模型配置及 SOUL.md，模型密钥保存在独立受保护状态目录；按新应用身份核实并配置管理员。
- [x] 补齐 Lark 消息接收事件并发布应用 1.0.4。真实测试群请求由云端机器人回复，容器重启后同一会话再次答出测试代号；请求、回复及会话恢复日志已回读。
- [ ] #11 的完整执行隔离验收仍未完成，Issue 保持 Open / Pending Verification。此次通过的是运行配置、模型、收发和重启恢复，不代表全部云端交付项通过。
- 详细边界与证据入口见 `docs/IT-INSTALL.md`；脱敏验收摘要与实际消息回读分别存放文档及忽略的 `artifacts/issue-11/`。

## 2026-10-03 本地运维入口和任务复核

- [x] 统一工作区增加本地服务器配置技能及 AGENTS.md 入口，记录云端与本机机器人的对应关系。技能位于两个 Git 仓库和 Docker 构建上下文之外，禁止上传；本仓库只保留入口规则。
- [x] #1 的必需子任务 #4、#5 均已完成，基线阶段满足关闭条件。
- [x] #11 补记独立 x86_64、非 root 网关、重启策略和真实收发证据；执行隔离仍未验收，保持待验证。
- [ ] #10 更新安装说明已推送、云端已有运行实例的事实；健康检查受历史错误影响，原先全通过的表述需修正，保持待验证。
- [ ] #13 已有云端重启恢复证据，进入待验证；多人、断网、失败、图片和长答案仍待验证。#6、#7、#8、#9、#23、#25 保留原有未完成项。

6. 2026-10-03：Deploy #27 已将 `rules/skills/plain-language-writing/` 安装到云端 ASTRA 与本机 Docker hermi 的 `data/skills/`。技能只约束需要交付或留档的文档、Issue、Lark Task、Meegle 正文；日常聊天继续使用各自 SOUL。两端 `skills list`、`skill_view` 和模型草稿验证通过，未修改 Core、SOUL 或源技能；证据见 `artifacts/issue-27/`。

7. 2026-10-03：Deploy #28 将 Lark Lingo 原生技能安装到云端 ASTRA 与本机 Docker hermi 的 `data/skills/`。技能默认使用受管机器人身份、先查 repo/classification/已有词条再生成可审核草稿；不因安装技能获得用户授权或直接写入词典。证据见 `artifacts/issue-lingo/`。

8. 2026-10-03：#29 修复批准卡片的请求绑定和过期反馈路由。Core 只传递内部 request_id；Deploy 话题扩展按 request_id 精确结算、校验原卡并在旧卡失效时回显原卡中的过期提示。构建时应用已登记的最小通用补丁，继续使用固定官方基线。审查后去掉发卡前队列快照判断，避免超时竞争进入纯文本批准回退；精确结算仍由官方锁保护。云端已重建并在 mybots 完成拒绝回读；完整等待 5 分钟后点击旧卡的失效反馈已在原话题回读，正常批准和拒绝也已通过。最终中文提示明确仅本次点击没有执行，避免误称已在别处处理的命令从未执行，证据见 `artifacts/issue-29/`。

## 2026-10-03 Issue #23 个人授权入口修复

- [x] 复用 #23 记录普通成员个人授权入口；#8 补记多个技能进度消息残留，本轮不处理。
- [x] 个人授权扩展增加设备授权入口、独立令牌目录、账号核对、过期与退出处理；Meegle 用已核实 Lark 账号的工作邮箱核对。
- [x] 云端只读复现 `not_configured`：容器有机器人环境变量，但持久 `.env` 缺少应用字段，CLI 未绑定。启动脚本需通过 s6 的环境入口继承变量，并固定 CLI 配置与令牌目录。
- [x] 云端已完成普通成员扫码授权并核对本人 Lark 身份；卡片回调已在 ASTRA 开发者后台以长连接方式启用并回读 `card.action.trigger`。
- [x] 个人查询命令包装修复已部署云端。复用已核验的授权，经 Hermes 工具调度入口查询本人任务，返回码 0、API `ok=true`、身份为 user，无误审批；`date` 保持可用。此证据覆盖工具调用链，尚不代替新的群聊端到端回合。
- [ ] #7 危险审批的管理员边界已部署：卡片的首次和二次检查使用受保护管理员名单；文字批准和待批时的裸批准词由权限扩展拦截。云端运行环境中的 3 项审批入口测试通过，真实多人验收仍未完成。
- [x] 用户于 2026-10-03 接受当前交付并要求关闭 #23。第二位成员扫码与独立结果的真实验收未执行，不记为通过；多人验证仍由 #13 跟踪。下一会话处理 #8 的进度消息复用与残留。

## 2026-10-03 Issue #8 并发进度收尾修复

- [x] Deploy #8 已修复同一轮多个进度/流式发送者并发首发造成的多条消息：`lark-topics` 现在按轮持锁串行发送、编辑和取消/失败收尾；首次发送取消或超时会等待有限回执，结果不确定时封闭该轮，禁止再次首发。
- [x] 进度与直接流式编辑共享每轮 18 次编辑预算，为 Lark 单消息 20 次编辑上限预留最终收尾；重复内容不消耗额度，迟到进度不覆盖已收尾结果，最终超长分片不重复首块。
- [x] 本地 `test_lark_replies`、`test_lark_topics` 和新增 `test_lark_reply_adapter` 共 35 项通过；云端 ASTRA 已重建并回读关键扩展文件哈希与本地一致。
- [x] 云端真实 `my bots` 回合已完成一次多技能读取：真实请求和最终回复均回读，数据库记录 `skill_view` 3 次、`terminal` 1 次，单回合只有 1 条 bot 回复；证据见 `artifacts/issue-8/test-a-thread.json` 与 `artifacts/issue-8/test-a-tools.json`。
- [x] 云端真实长答案完整回读 180 行，分为 8000 和 4239 字符的两条消息，均在同一原生话题；取消回合的原进度已更新为停止提示。证据见 `artifacts/issue-8/test-b-summary.json`、`artifacts/issue-8/cancel-thread.json`。
- [x] 真实入口失败回合执行 `false`，回读失败退出码 1 且未重试；不可读附件回读明确要求重新上传替换件；引用回归回读了被引用消息原文，且回复保持在同一话题。证据见 `artifacts/issue-8/final-main.json`、`final-attachment-threads.json`、`quote-thread.json`。

## 2026-10-03 Issue #23 查询结果整理回归

- [x] 用户反馈 Meegle 查询后生成 Python 统计脚本触发审批，已重开 #23。直接 CLI 查询的修复不等于完整查询流程通过。
- [x] 归属 Deploy 公共工具环境与个人授权扩展的结果处理指引：镜像增加 Debian jq，优先 CLI 投影/服务端过滤，本地 JSON 用 jq 整理；不放行任意 Python、不关闭 Tirith、不写项目业务字段规则。
- [ ] 云端重新部署并验证查询、筛选、统计和真实群聊回答不出现危险审批；危险命令仍保留原审批边界。

## 2026-10-03 Issue #23 Meegle 授权持久化补丁

- [x] 定位真实回归：个人授权文件保留在持久卷，但 Meegle CLI 的加密凭据使用容器主机名派生密钥；默认 Compose 主机名每次重建随机变化，导致 CLI 报 `no local token` 并重新弹授权。
- [x] Deploy Compose 固定 `hermes-team-official` 主机名；不扩大权限、不绕过凭据校验。首次切换后需由当前用户重新完成一次 Meegle 授权，之后重建保持同一主机名。
- [ ] 云端重建后完成一次重新授权，并回读真实 Meegle 查询结果。

## 2026-10-04 定时任务创建者授权修复（Deploy #31）

- [ ] 新建定时任务必须声明 `personal_services`，并在创建前用创建者已完成的个人授权做在线核验；个人授权范围在创建时固定，后台不能扫码、退出或换用操作者授权。
- [x] Deploy 已增加个人授权与定时任务的隔离测试 31 项；旧任务数据保留，未直接清除或替换运行状态。
- [ ] 云端发布后完成真实 Lark 回读；原任务来源保存的是数字开头的 Lark user_id，需通过原会话的同一用户别名核实并固定创建者 open_id，保留原任务编号和业务规则。
- [ ] Core #3 已修复 Feishu SDK 对象回执被误当字典导致的重复发送；补丁在镜像构建时应用。
