# 必要补丁清单

以下记录无法仅通过扩展实现的通用补丁。构建仍使用固定官方源码；补丁在镜像构建时校验后应用，校验失败即停止构建。

## 2026-10-03 Issue #29 批准卡片精确绑定与话题反馈

- 用户行为：Lark 话题中的批准卡过期或已被处理后，反馈必须回到原卡所在话题；旧卡不能按会话 FIFO 误批准后来排队的命令。
- 官方缺口：Core 的 `send_exec_approval` 原先没有把队列里的 `request_id` 传给平台卡片；Feishu 回调按 session 解析队首，且过期反馈调用普通 `send`，扩展无法安全区分并发请求。
- 官方基线：`f8489405600c9a7d9d2f307dace086f18d7173ba`；Core 子任务 `LiuYangArt/hermes-team#2`。构建包为 `deploy/core-patches/exec-approval-request-id.patch`，与 Core 源码变更保持一致，不包含任务文件或状态。
- 修改范围：Core `gateway/run_turn_runner.py` 仅把 `request_id` 放入内部 metadata；Deploy `extensions/lark-topics/adapter.py` 保存并精确解析 request_id，校验原卡消息 ID，过期/重复点击复用原卡反馈；无请求编号时拒绝发卡。未改变权限、时限或队列策略。
- 验证：Core 审批通知测试 13 项通过；Deploy 专项测试 4 项通过；回复 16 项和话题路由 11 项通过；`py_compile` 与 `git diff --check` 通过。真实云端 my bots 已验证批准一次、拒绝、跨话题独立等待，以及完整 300.98 秒超时后点击原卡显示失效反馈；没有新增群主聊天提示。真实队列隔离与管理员入口另有 4 项通过，证据在 `artifacts/issue-29/`。
- 删除条件：官方平台接口同时传递并按请求编号解析审批，且支持保留原话题元数据后，删除本补丁并重跑并发、过期和重复点击回归。

## 2026-10-04 Cron SDK 回执兼容（Issue #3，父任务 Deploy #31）

- 用户行为：定时任务发送成功后，不能因 Feishu SDK 返回对象不是字典而重复发送并标记失败。
- 官方缺口：Core 仅对 dict 形状读取 `thread_fallback`；SDK `ReplyMessageResponse` 是对象。
- 官方基线：`f8489405600c9a7d9d2f307dace086f18d7173ba`；镜像构建包 `deploy/core-patches/cron-sdk-receipt.patch`。
- 修改范围：仅在 raw_response 为 dict 时读取可选降级字段；保持发送成功判定和错误策略。
- 验证：Core `TestDeliverResultLiveAdapterUnconfirmed` 3 项通过；真实云端发布前待验收。
- 删除条件：官方 SendResult/raw_response 契约统一为 dict 后删除补丁并重跑回归。
