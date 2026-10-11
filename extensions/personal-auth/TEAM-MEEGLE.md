# 团队 Meegle 代办

## 身份和权限

`team_meegle` 使用受保护配置中的 PM 授权执行操作。请求人来自当前 Lark 消息，不接受模型指定。管理员与 PM 独立；管理员不会因管理机器人而获得 PM 的全部业务权限。

普通成员可以开单、添加评论、修改所有工作项的状态，并读取执行这些操作所需的字段、状态、单子和人员信息。不能修改已有评论、其他工作项字段或删除单子。PM 本人可以执行官方 CLI 已提供的业务操作；授权、凭据和本机配置仍走受管入口。

创建默认采用配置中的项目和类型。用户明确指定其他池子时先读取类型及字段定义，再传入目标。请求人不等于负责人，更不等于 PM；默认池子的描述由代码补充真实请求人。其他池子只有在运行配置登记对应描述字段后才补写，避免猜测字段。所有操作都保存独立请求记录。

## 配置

将 PM 的真实 Lark open id 写入受保护的 `lark-access/config.json` 的 `pm`，与 `admins` 并列。业务默认值放同一运行目录下的 `team-meegle/config.json`，权限为 0600：

```json
{
  "default_project": "PROJECT_KEY",
  "default_type": "WORK_ITEM_TYPE",
  "default_pool_name": "DEFAULT_POOL_NAME",
  "description_field": "DESCRIPTION_FIELD_KEY",
  "description_fields": {"OTHER_TYPE": "DESCRIPTION_FIELD_KEY"},
  "pm_name": "PM_DISPLAY_NAME",
  "host": "meegle.com"
}
```

配置文件不存在时不启用团队代办。文件存在但不合法时拒绝执行。真实身份、项目 ID、授权和请求记录不进入源码、镜像或 Git。

## 操作流程

1. `status` 返回真实请求人、PM、默认目标和本地绑定状态。在线有效性在实际调用前核验。
2. `inspect` 查看当前角色可用命令。`run` 接收点号命令和 CLI 短横线参数，例如 `workitem.create`、`project-key`。
3. 缺少 PM 授权时，操作保存为待授权，并自动在当前话题 @PM，发送链接、二维码和有效期。`inspect` 也会自动通知。相同授权按话题去重；二维码发送失败后，`auth_start` 只补发未成功部分。授权入口由工具直接发到 Lark，不交给成员转发。
4. 首次先核验 PM 的 Lark 身份和工作邮箱，再核验同邮箱的 Meegle 账号。普通成员不必扫码。错误账号不能成为团队执行账号。
5. 扫码后，PM 在原话题 @机器人回复“已授权”。agent 调用 `auth_complete`，自动恢复当前话题中已保存、当前成员有权恢复的请求，保留原请求人。首次命令查看也保存原请求；后续 `inspect/run` 携带返回的 `request_id`，由工具核对原话题和原提出人。其他话题需通过 `resume` 明确恢复，不因一次授权批量执行所有群内待办。
6. 写入成功保存结果并回读。执行前先持久保存执行状态；超时、重启或返回不确定时不自动重放。

授权需由 PM 本人操作，当前没有常驻轮询。PM 在原话题 @机器人回复“已授权”后，由 agent 完成核验并恢复。请求按消息及内容去重；重新发出的独立消息不视为同一请求，agent 应先查看上一请求的结果。

## 执行边界

用户明确要求保留 ASTRA 现有能力，本功能不新增终端、脚本、浏览器、委派或文件访问限制，也不增加账号执行隔离。普通成员的三类操作规则在 `team_meegle` 入口校验；这是正常代办流程的业务规则，不声称能防止任意脚本绕过。

定时任务不切换团队授权，继续绑定创建者；团队代办工具拒绝后台调用。原有管理员和个人授权规则继续生效。

## 验证及发布

```sh
python3 -m unittest discover -s tests -p 'test_team_meegle.py' -v
python3 -m unittest discover -s tests -p 'test_personal*.py' -v
python3 -m unittest discover -s tests -p 'test_lark_access.py' -v
python3 scripts/check_scaffold.py
git diff --check
```

本地测试使用模拟 CLI，不写业务数据。产物位于 `artifacts/issue-pm-delegation/`。正式验收还需云端加载、真实普通成员请求、PM 扫码、开单及回读、评论、状态修改、越权拒绝、重启与并发。业务写入测试需明确测试单范围；未获准前只做只读查询和模拟。

本功能随既有 `personal-auth` 扩展发布，不引入 Core 补丁、不改变任务包发布。发布前比较云端实际扩展与源码，保留已存在的运行差异；只发布明确选择的文件。默认业务配置独立保存，公共升级不覆盖。
