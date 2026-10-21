Claude Code 运行时：
- 用 Agent 工具派发子代理。只读调研用 subagent_type 为 Explore，实现类工作用 general-purpose。
- 只有用户明确指定时才传 model（sonnet、opus、haiku、fable）和 effort（low、medium、high、xhigh、max）；否则两者都不传，让子代理继承默认值。
- 用 SendMessage 追加指示或要求返回结果，用 TaskStop 停止子代理。
- 用 Read 核实结果。模式开启时，主线程的 Edit、Write、Bash 和其他 MCP 工具会被 hook 拦截。
