# 验收测试

后续补充真实行为测试：话题隔离、请求者绑定、权限、附件、进度生命周期、任务发布隔离和容器健康。当前只提供脚手架检查。
# Issue #29 审批回归

`test_lark_approval_adapter.py` 仅模拟传输和审批结算，保留真实卡片反馈生成；`test_execution_approval_access.py` 使用真实审批队列验证旧卡不会批准新请求。前者可用 `python3.13 -m unittest discover -s tests -p test_lark_approval_adapter.py -v`，后者需要 Core 依赖环境与指向 Core 的 `PYTHONPATH`。
