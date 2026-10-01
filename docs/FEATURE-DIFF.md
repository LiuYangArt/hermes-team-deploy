# 旧 CN 团队能力差异表

日期：2026-10-01。来源：旧工程 `/Users/apple/CodeProjects/hermes-agent`，HEAD `ac0b6703c0`；目标为当前官方 Core `3057b821787cae30a423525790c87e634b1c546e` 与部署工程。

结论分为：**官方已具备**（先验证，不复制旧实现）、**部署扩展**（公共团队能力归 `extensions/`）、**独立任务**（只归 `jobs/<job-name>/`）、**待确认通用补丁**（先做最小复现，证实官方接口不足后才改 Core）、**本阶段不迁移**（保留旧状态，不进入当前交付）。

| 能力 | 旧实现与测试证据 | 当前判断 | 新归属 | 验收/下一步 |
| --- | --- | --- | --- | --- |
| Lark 基础收发、机器人身份 | `plugins/platforms/feishu/adapter.py`；`team/tests/test_lark_identity.py` | 官方基础链路及本轮群聊发送身份已通过真实回读 | 官方 Core + `extensions/` 配置 | 已回读群聊、@对象和消息归属；多成员待验收 |
| 原生话题、引用锚定 | 旧 `thread_router.py`、`thread_state.py`；提交 `560495e001`；`team/tests/test_lark_threads.py` | 官方已识别 `message.thread_id`、`reply_to_message_id` 并可锚定回复；普通引用不会自动变成话题 | 官方 Core 先验收；成员语义另归扩展 | 已有双话题隔离、引用文本和归属已通过；自动开题待实现 |
| 成员参与、旁听、恢复 | 旧 `thread_state.py`、`thread_router.py`；`team/tests/test_thread_conversations.py` | 官方缺少 `/listen`、成员状态/generation 持久化和旁听背景语义 | `extensions/` | @参与、`/listen`、再次 @恢复及重启恢复 |
| 同话题排队与请求者绑定 | 旧 `thread_router.py`、`thread_state.py`、`governance.py`；相关团队测试 | 官方有通用会话排队，但缺少按话题/请求者隔离、审批/停止/澄清绑定 | `extensions/`；若无法接入生命周期再评估 Core 补丁 | 排队、停止、审批、澄清只允许当前请求者 |
| 进度与最终回复 | 旧 `reply_state.py`；提交 `93420a244`；`test_lark_reply_replacement.py` | 官方有 `send`/`edit_message`，但缺少旧 ReplyState 的进度锁定、审批等待和取消/失败收尾语义 | `extensions/`；先核对处理生命周期钩子 | 进度、最终、失败、取消、长答案均回读 |
| 机器人身份与权限边界 | `governance.py`；提交 `1eb158ffb2`；`test_lark_identity.py`、`test_team_governance.py` | 公共团队治理 | `extensions/lark-access/` + 受保护配置 | 已实现。测试群验证了自动记下、管理员改人设、非管理员拒绝和过期文件清理。见 #15 |
| 词典与共享资产治理 | `lingo.py`；提交 `9e1c8d3a2a`、`d3b3959a3e`；`test_team_lingo.py` | 公共能力，不能进入任务包 | `extensions/`，以后做，见 #21 | 固定机器人身份只读；覆盖群聊、私聊、ACP、其他工具入口及无会话查询 |
| 沙箱与产物路径 | `sandbox.py`、`seccomp-*.json`；`test_team_sandbox.py` | Linux 容器能力，macOS 现状不等价 | `deploy/` + `extensions/` | Linux 非 root、可读写边界、失败不泄露凭据 |
| 原图与任务附件 | `team/tests/test_team_runtime.py:114`（字节一致性）、`:139-149`（路径/清理）；Helius 测试只覆盖收费生图 | 原图上传与 Helius 生图是两项不同能力 | `extensions/`，以后做，见 #22 | 上传、回读、不可读时明确反馈；生图另行评估 |
| 文档评论/引用回复 | 官方 Core 已有 `plugins/platforms/feishu/feishu_comment.py`、`feishu_comment_rules.py` 及对应 `core/tests/gateway/test_feishu_comment*.py`；旧 CN 基线到旧 HEAD 无团队新增差异 | 官方已具备，当前不迁移旧实现 | 官方 Core | 用官方测试验收评论线程、权限和回复回读 |
| Lark Tasks / ACP | `team/task-bridge/`、`team/acp/`；`test_job_deployment.py` | 当前后台已暂停，尚未迁移 | `extensions/` + `deploy/` | Linux 容器服务、专用身份、真实请求回读 |
| Meegle 分诊与负责人判断 | `team/jobs/meegle-triage/`；其单元测试 | 不另建任务包。操作用官方 Meegle 技能 | 官方技能见 #9 | 需要日程时用 Hermes 定时任务挂上该技能 |
| 每日摘要 | `team/jobs/daily-summary/`；`test_cron_daily_summary.py` | 不另建任务包 | Hermes 定时任务 | 需要时用定时任务挂上已安装技能，不进入公共镜像 |
| 会议、审批、联系人等 Lark 技能 | `team/skills/lark-*` | 按真实需求逐项启用，不整体复制 | 独立扩展或任务 | 每项先明确使用场景、权限和验收 |
| 旧聊天历史、旧插件、旧个人授权 | 旧运行目录和 `team/.local/` | 本阶段不迁移 | 保留旧状态 | 不读入新镜像、不提交 Git |

## 已确认的实施边界

- 当前没有证据证明需要修改 Core；官方已提供 Feishu 话题识别、回复锚定、消息编辑、澄清和卡片承载能力。成员治理、旁听、请求者绑定和进度状态先做部署扩展。
- 已完成最小钩子与注册探针评估：`pre_gateway_dispatch` 和 `on_processing_*` 单独不足以完成首答锚定/别名绑定，但公开 `register_platform` 可替换工厂并保留平台字段。优先评估 Deploy 薄适配器；只有该接缝经行为回归证明不足时才建立 Core Issue。
- 旧 adapter、team 目录和安装器不整体复制；每项能力必须有新归属、行为测试和脱敏证据。
- 扩展实现前先完成 Issue #4 的差异清点；任务迁移必须等待对应独立 Issue。

## 2026-10-01 首次 @ 复核

Issue #5 的真实群消息回读显示：首次 @ 请求无 thread_id，机器人回复的 root_id/parent_id 指向原请求，但 thread_id 为空。官方对已有话题的支持不等于自动开话题。首次复核时，原生话题连续性、引用和双话题隔离尚待补验（结果见下节）；消息与身份 ID 只保存在本地 artifacts/issue-5/evaluation.json。

## 2026-10-01 Issue #5 补验补充

本轮在授权 `my bots` 群复用两个已有原生话题完成 A/B 连续回读、Q1 精确引用复述和双话题隔离；未重复发送首次群聊 @。首次普通群聊 @ 仍无 `thread_id`，A2 不带 @ 的后续消息未进入持久处理会话。详见 `docs/LARK-TOPIC-ASSESSMENT.md` 与 `artifacts/issue-5/evaluation.json`。

建议方案：先在 Deploy 实现固定当前 Core 基线的 Feishu 薄适配器，覆盖批处理前路由归一、首答原消息锚定、服务端 thread/root 回读绑定和已有准入语义的最小增量；保留官方传输、鉴权、会话和错误处理。`register_platform` 的临时探针已通过，但内部适配器接缝属于基线依赖，需随 Core 更新做行为回归。当前没有被证据证明必须修改 Core 的缺口。
