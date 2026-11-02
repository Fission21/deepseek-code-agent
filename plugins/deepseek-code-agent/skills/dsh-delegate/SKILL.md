---
name: dsh-delegate
description: Use ONLY when the user explicitly requests DSH delegation for the current task or invokes $dsh-delegate. Supports concurrent development, investigation, review, testing, and current-Chrome work. Never infer authorization from earlier DSH use or discussion.
---

# DSH 协作代理

**仅在用户明确指定本次任务使用 DSH，或显式调用 `$dsh-delegate` 时启用。** 日常任务使用正常 Codex 流程；讨论或曾经使用 DSH 不构成新任务的委派授权。已授权的同一任务可持续协作，无需反复确认。

按使用 Codex 子代理的方式使用本机 DSH。**主对话负责目标、分工、监督进度和纠偏；DSH 负责实现、测试、修复与完整交付。** 不为 DSH 增加专属的额外核验、重复确认或主对话重跑全部检查的流程。发现问题就把明确反馈交回原代理继续完成，不默认由主对话接手收尾。

默认模型为 `deepseek-official / deepseek-flash`，推理 `max`。启用本 Skill 后，用户明确选择 DSH 时，不要用 OpenCode、Luna 或其他同名 DeepSeek 工具替换。只有用户明确要求 OpenCode 或旧 `ds_*` 工具时才走 [legacy lane](../deepseek-coding-delegation/SKILL.md)。

## 常用协作

优先调用 `dsh_agents` MCP 的工具。若当前聊天尚未加载新工具，使用 [同一接口的 JSON 调用方式](references/collaboration.md)，无需用户代传消息或手工创建任务文件。

多个可拆分任务使用 `create_team` / `wait_team`。主对话根据独立任务数量、依赖和共享资源设置本组 `max_parallel`，可用 `set_team_concurrency` 运行中调整；全局默认并发 2。状态直接显示容量和等待原因，不因任务复杂就自动提高全局额度。详见[并发、交接和团队消息](references/teams.md)。

1. `spawn_agent`：给目标、工作区、文件归属及必要背景。像给原生子代理派活一样说明预期结果；不必逐步指挥它怎么做。已有并行改动要保留，有冲突才隔离。
2. `wait_agent`：带上上次返回的 `cursor`，收到新进度、问题或结果再处理。DSH 原生工具调用的名称和阶段可用于监督，不需要读取思考过程。
3. `send_message`：随时补充上下文或纠偏。执行中的消息在下一模型步骤进入；空闲时自动续用同一会话。
4. `reply_agent`：回答 DSH 的实际阻塞问题。主对话能决定的直接决定；只有缺少用户决定时才问用户。不要把 DSH 的每次提问都转给用户。
5. `interrupt_agent`：需要立即停止方向错误的执行时使用；工具会处理本代理的进程组收尾。之后 `send_message` 继续同一会话。
6. 结果按通常使用子代理的方式汇总、审阅和整合。需要返工就发回原代理；结束协作用 `close_agent`。

同一代理的后续消息、纠偏和中断续接沿用 `session_id`，不会静默新建会话；cursor 只排除已经交付的消息/结果。超时、中断或控制器错误会保留会话并暂停，不自动重放不确定的写入。待答问题最多等候 180 秒，超时不是同意。

## 上下文与职责

- DSH 自身会读取适用的 `AGENTS.md`；主对话提供当前目标、已定决定、相关上下文及必要的 Skill/专项规则路径。不要导出整个 Codex 私有会话或内部状态。
- `scope_paths` 指定可写路径，省略表示只读任务。它是任务边界，不是权限白名单或 OS 沙箱；保留已有数据访问和动作授权。`context_files` 与 `required_reads` 只传必要文本材料，单份上限 64 KiB、整包 160 KiB；不传凭据、私钥或私有会话记录。
- 让 DSH 自行选择适当检查、解决失败、清理它创建的专属临时资源，最后说明结果、修改文件、实际检查与剩余阻塞。项目本身适用的要求照常遵守，不因更换执行模型另加一套门槛。
- 超时、中断或错误会保留会话并暂停，避免自动重复不确定的写入。主对话作方向判断，交回 DSH 处理。

## 当前 Chrome

`spawn_agent` 传入已分配的 `tab_id` 和预期 `url`，即可启用花叔 MCP，使用当前 Chrome 的登录会话。将目标操作及授权范围交给 DSH；它负责交互、结果检查和普通定位问题。当前共享 Chrome 的浏览器任务串行执行，即使分配不同标签页也共用登录状态。真实登录态不等于授权支付、发消息、删除或写业务数据。详见 [浏览器与底层调用](references/execution.md)。

## 安装与平台

安装后先运行离线预检（不启动模型会话、不调用模型；可能执行 `dsh --version`）：

```bash
node <plugin-root>/dsh-server.mjs --check
# 或在插件目录：npm run check:dsh
```

可通过 `DSH_BIN`、`DSH_PYTHON`、`NODE_BIN`、`HUASHU_CHROME_ENTRY` 和 `DSH_RUNTIME_ROOT` 配置非标准安装路径。macOS 已用真实 DSH 链路验收；Linux 仅做离线/CI 测试，未做真实 DSH 或 Chrome 验收；Windows 暂不支持运行，预检会明确失败。不要声称未验证的平台已通过。

## 能力边界

这是本地 DSH 的协作接口，不是把 DSH 注册为 Codex 内部模型。它拥有自己的工具和持久会话，接收选择后的上下文；不会自动得到 Codex 全部私有工具与聊天历史。正在执行的单个工具不会因普通消息自动撤销，需要停止时用 `interrupt_agent` 或 `cancel_team`。主对话不应承诺固定省 token；历史 benchmark 只是历史证据，不是当前 DSH 的实测结果。

文件归属与 `tab_id` 是任务边界，不替代用户授权。运行不复制凭据、不更改 DSH 全局模型配置。注册表与结果默认保存在 `~/.local/share/dsh-delegate/agents`，空闲代理不运行后台工作进程。
