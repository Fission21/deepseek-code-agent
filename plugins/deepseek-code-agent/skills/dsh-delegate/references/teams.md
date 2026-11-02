# 并发任务组

0.5 日常入口为 submit_work / observe_work；[控制与交付](control.md)说明新会话 reviewer、共享消费预算及按需事件。本文的 create_team / inspect_team / wait_team 是兼容和诊断 API。

仅在用户明确指定 DSH 的范围内使用。主对话决定目标、分工和取舍；程序处理排队、资源占用、阶段交接和消息转发；DSH 自行完成实现、检查、修复和交付，不另加主对话重复复验流程。

## 创建与观察

`create_team` 接收 `title`、可选的 `max_parallel` 和 `tasks` 数组。`max_parallel` 为本组并发上限（1–8），省略则沿用全局上限；实际执行始终受全局额度限制。每个任务的 `id` 在组内唯一，其他常规参数沿用 `spawn_agent`，新增：

- `depends_on`：前置任务 id。仅接受同组、无环依赖。
- `outputs`：任务应交付的工作区相对文件路径，必须位于 `scope_paths` 内。完成时记录文件 SHA-256，交给下游；缺失输出不会释放下游。
- `resources`：需独占的共享资源名称，如 `qat:tenant:sample`。不共享同一数据的任务可并行。

```json
{
  "title": "模块协作",
  "max_parallel": 2,
  "tasks": [
    {"id":"api","workspace":"/absolute/project","task":"实现已定接口，自行完成相关测试","scope_paths":["app/api/example.py"],"outputs":["app/api/example.py"]},
    {"id":"ui","workspace":"/absolute/project","task":"按已定接口实现界面，自行完成相关测试","scope_paths":["app/ui/example.tsx"],"outputs":["app/ui/example.tsx"]},
    {"id":"integrate","workspace":"/absolute/project","task":"整合两个模块，完成联调并自行解决问题","depends_on":["api","ui"],"scope_paths":["app/api/example.py","app/ui/example.tsx"]}
  ]
}
```

`wait_team(team_id, cursor, timeout_seconds)` 等待组内任一新事件；每次使用返回的新 cursor，最多等 55 秒。结果包含任务状态、agent_id、结果和事件；`inspect_team` 立即返回。问题事件含 request_id，仍用 `reply_agent` 回答。

状态中的 `capacity` 给出全局/本组上限、有效上限、正在占用的名额和空余名额；`team_limit: null` 表示继承全局。`dependency_ready` 是前置已完成的待执行任务数，`resource_ready` 是其中没有与运行任务争用资源的数量。`cancelling` 在收尾完成前仍占用名额。

每项任务的 `waiting_reasons` 会指出 `dependencies`（含组内未完成前置）、`resources`（冲突任务数量）、`team_capacity`、`global_capacity`、`team_paused` / `team_closed` 或 `stale` / `blocked_version` 等状态。不会透露其他组的任务内容。`eligible_now` 只表示该任务在当前快照中满足这些启动条件，不是名额预留；多项任务仍可能争用同一个空位或彼此冲突。产物版本检查仍在实际领取任务时执行，不在每次读状态时重复读取文件。

一个任务只对应一个持久 DSH 会话。`send_message(agent_id, message)` 可在执行中纠偏；已结束任务会排队继续原会话。`resume_task(team_id, task_id, message)` 用于明确返工和中断续接。

## 任务交接

团队 DSH 会自动得到两个工具：

- `send_peer(target, message)`：向同组已指定任务发送有用发现。队列和收件回执由程序处理；不能借此新建任务、扩大权限或向其他组发消息。对已完成任务可留存 blocked/artifact_ready 供返工读取；question 仅发给运行同伴，不能占位等待排队同伴。
- `complete_task(outcome, summary, checks)`：提交 `done`、`blocked` 或 `failed`，同时说明结果和自己实际完成的检查。它是交付结果，不是请求另一轮人工复验。只有当前执行结束且 outcome 为 done，才释放下游。

下游收到前置任务的摘要、修订号、输出路径和哈希。输出在交接前发生变化，会进入 `blocked_version`，避免静默使用过期结果。返工上游会将已经执行的下游标记为 `stale`；正在运行的下游会先停止。主对话给受影响任务明确续接指令即可，不重跑无关任务。

## 并发与资源

- 默认同时执行 2 个 DSH，所有使用同一注册表的 Codex 聊天共享额度。只有需要调整时才调用 `set_concurrency(limit)`，范围 1–8；上限是配置边界，不是承诺服务端支持的容量。
- 优先用 `set_team_concurrency(team_id, limit)` 调整本组，传 `null` 恢复继承；不会改其他组或全局配置。调低后已运行任务继续完成，新增任务等待；调高后符合条件的排队任务自动启动。暂停的组不会因此恢复。关闭的组不接受调整。
- 数量由主对话根据可独立推进的工作决定，调度器只落实额度、依赖和互斥。例如两项独立实现可并发 2，后续集成因依赖自然只运行 1 项；共享 Chrome 操作仍串行。复杂但高度耦合的任务也可能只适合 1 项，不设置难度评分模型或自动提升全局额度。
- 调度记录位于默认注册表中的 `scheduler.sqlite3`。修改、抢占名额使用 SQLite 事务；调度进程按需运行，空闲退出。`spawn_agent` 也经过调度，不会绕过额度。
- 重叠文件/目录的写入互斥；下游声明产物的读取也与写入互斥。不同任务尽量分配清楚的文件归属，需要同时改同一部分时由主对话分配独立工作区及整合负责人。
- 这是调度层的协作锁，不是操作系统沙箱。无法阻止模型越过任务约束；直接运行底层 worker/runner 调试命令也不会加入调度。
- 当前花叔接入共享一个真实 Chrome 环境，浏览器任务默认串行。多标签仍共享账号状态，不能把 tabId 当作完全隔离。
- 等待请求和控制请求使用不同线程池；大量等待不会把回答问题、发消息和停止操作堵在同一队列后面。

## 停止与恢复

`cancel_team` 暂停全组并停止所属执行；`resume_task` 明确继续指定任务。`close_team` 结束协作，保留结果但不再接受续接。独占名额与资源在 worker 完成进程收尾后释放。

调度器退出后，Watchdog 会恢复监督；下次调用也会接回仍在运行的 worker，避免再次启动同一模型任务。worker 本身异常退出时保留实际结果并停止不确定的残留执行，不自动重放可能写过的操作。一个任务失败只阻塞它的依赖链，独立任务继续。

任务取消、失败或阶段交接本身不代表浏览器、业务数据或工作区已清理。DSH 应按任务分工收尾自己创建的专属资源；用户文件、登录态及共享资源继续保留。

验收、结构化消息字段、跨组轮转和预算的完整用法见[可靠协作](reliability.md)。`resume_task` 使用观察到的 `expected_revision`，超限任务需要主对话明确重新分配。
