Codex 运行时：
- 用 spawn_agent 派发子代理。
- 只有用户明确指定、且该工具支持时才设置 model 和 reasoning_effort；否则不设置，让子代理使用宿主 [agents] 的默认配置。
- 用子代理工具等待结果；返工或后续指示用子代理消息工具（如 send_input）发回原子代理，保留其上下文；不再需要时关闭子代理。
- 允许的只读检查：cat、rg、git diff、git status。
- 不要使用 apply_patch，也不要运行会写入的命令（构建、安装、测试、格式化、提交都交给子代理）。
- 用户命令：`@worker 任务`（或 `$worker 任务`）开启，`@worker off` 关闭，`@worker status` 查看状态。
