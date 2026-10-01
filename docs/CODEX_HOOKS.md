# Codex 完成前文档检查

归属：本项目开发工作流。实现位于 `scripts/codex/task_docs_hook.py`，不进入 Core、机器人规则或发布镜像。只在统一工作区 `/Users/apple/CodeProjects/hermes-team-workspace` 的 Codex 配置中启用。

## 行为

- `UserPromptSubmit` 记录两个仓库及工作区入口文件的开始快照，并提醒本回合的记录要求。
- `Stop` 比较实际文件内容。有公共改动时必须更新 `deploy/docs/PLAN.md`；公共代码/配置改动还要更新 `deploy/docs/` 下对应说明。
- 只修改 `jobs/<name>/` 时，只要求该任务内更新说明文档和包含 Markdown 复选项的任务清单（支持 `-`、`*`、`+` 和 `[X]`）；同一个 Markdown 文件可以同时承担两者。不会要求修改公共计划或另一任务。
- 不通过时返回官方 `decision: block`，让 Codex 自动继续补齐文档。续跑以及首次 Stop 前中断后的同会话提交均沿用原始快照，不能因 `stop_hook_active` 而跳过检查。
- 纯只读回合不需要人为修改文档。开始前已有的脏文件、仅删除文档、更新其他任务的文档，均不能替代本回合所需记录。回合中提交代码也不会掩盖改动。
- 忽略文档中的纯空白变化；任务清单必须有清单项的实际新增或状态/文字变化，已有旧复选框不算更新。
- 文档需说明实际变化、验证结果、未完成项和阻碍；不能为过检查增加无关内容或虚报验收。

## 安装与首次启用

本机已生成工作区 `.codex/hooks.json`。换路径后从统一工作区运行：

```sh
python3 deploy/scripts/codex/task_docs_hook.py --workspace "$PWD" --install
```

安装会保留其他 hooks；重复运行不会重复追加同一个命令。运行依赖 macOS `/usr/bin/python3` 和 Git，无额外包。

首次信任必须由用户在 Codex 中完成。CLI 0.157.0 实际加载在 Folder access 信任提示处停止；尚未验证真实回合中的自动续跑。操作入口：

```sh
codex -C /Users/apple/CodeProjects/hermes-team-workspace
```

1. 如出现 `Trust this folder?`，核对是本统一工作区后选择 `Trust and continue`。
2. 输入 `/hooks`，审查本工作区 `.codex/hooks.json` 中的 `UserPromptSubmit`、`Stop` 两个命令并信任。命令应只调用部署仓库中的 `scripts/codex/task_docs_hook.py`。
3. 回到桌面项目开始一个新回合，让新配置被加载。若桌面端仍显示待审查，完成同样的 hook 信任后再开始。

授权无二维码或有效期；hook 定义变更后 Codex 会要求重新审查。不要通过改写 trusted_hash、全局免信任或管理员策略绕过这个步骤。

## 验证与排查

从统一工作区执行：

```sh
python3 -m unittest discover -s deploy/tests/codex -v
python3 deploy/scripts/check_scaffold.py
git -C deploy diff --check
```

15 项临时仓库测试通过，覆盖只读、漏文档、补齐放行、已有脏改动、续跑、提交、任务隔离、删除、特殊文件名、重复安装、中断续跑、纯空白和旧清单未变。安装后两个配置命令的输入输出检查通过，证据为 `deploy/artifacts/codex-docs-hook/installed-command-check.json`；这不等于 Codex 已信任并真实触发。测试日志：`deploy/artifacts/codex-docs-hook/tests.log`。

运行状态：`deploy/artifacts/codex-docs-hook/<session-hash>.json`（Git 忽略，仅存文件路径/指纹和最后检查结果，不保存提示词或凭据内容）。`last_result: ["PASS"]` 表示该快照检查通过；缺少运行记录时先核查 hooks 是否加载并信任。

局限：这是可关闭的本机开发检查，不是操作系统隔离或语义审查；只能确认规定位置有内容变化，不能证明文档写得正确。共享工作目录的并发修改无法自动归属到某个聊天；远程业务状态、Git 忽略文件和未落盘的工具任务清单不在检查范围。没有开始快照或检查出错时报告失败；需修复并重新开始正常回合，不能将其视为验收成功。

依据：[OpenAI 官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks)，2026-10-01 核对。
