---
name: "status"
description: "查看 Worker 模式当前是开还是关。用户输入 /worker:status 时使用。"
disable-model-invocation: true
---

用户想查看 Worker 模式的当前状态。

1. 不调用任何工具，不派发任何工作。
2. 如果本轮上下文包含 `【Worker 模式规则` 或 `Worker 模式当前：开`，状态为开；否则为关。
3. 用一两句话告诉用户当前状态。开启时说明主控只读、工作派给子代理；关闭时说明可以直接读写文件、运行命令。
