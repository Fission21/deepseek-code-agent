---
name: dsh-delegate
description: Use ONLY when the user explicitly requests DSH delegation for the current task or invokes $dsh-delegate. Supports concurrent development, investigation, review, testing, and current-Chrome work. Never infer authorization from earlier DSH use or discussion.
---

# DSH 协作代理

**仅在用户明确指定本次任务使用 DSH，或显式调用 `$dsh-delegate` 时启用。** 日常任务使用正常 Codex 流程；讨论或曾经使用 DSH 不构成新任务的委派授权。已授权的同一任务可持续协作，无需反复确认。

按使用 Codex 子代理的方式使用本机 DSH。**主对话负责目标、分工、监督进度和纠偏；DSH 负责实现、测试、修复与完整交付。** 不为 DSH 增加专属的额外核验、重复确认或主对话重跑全部检查的流程。发现问题就把明确反馈交回原代理继续完成，不默认由主对话接手收尾。

默认模型为 `deepseek-official / deepseek-flash`，推理 `max`。启用本 Skill 后，用户明确选择 DSH 时，不要用 OpenCode、Luna 或其他同名 DeepSeek 工具替换。只有用户明确要求 OpenCode 或旧 `ds_*` 工具时才走 [legacy lane](https://github.com/Fission21/deepseek-code-agent/blob/main/plugins/deepseek-code-agent/skills/deepseek-coding-delegation/SKILL.md)。

## 常用协作

优先调用 `dsh_agents` MCP 的工具。若当前聊天尚未加载新工具，使用 [同一接口的 JSON 调用方式](references/collaboration.md)，无需用户代传消息或手工创建任务文件。

新任务统一使用 `submit_work`，单任务也是 tasks 数组。默认独立需求审阅；低风险任务可传 `review_policy: light` 和具体 `review_reason`。提前绑定用户已授权的共享 DSH 预算，不借用业务测试预算。完整约定见[0.5 控制与交付](references/control.md)。

1. `submit_work`：给原始目标、验收标准、工作区和归属；主对话按独立工作量设置 `max_parallel`，全局默认 2。
2. `observe_work`：无 team_id 可列组；带 team_id/cursor 只等决策和全组完成，普通进度静默。它是最多 55 秒的长轮询，不能主动唤醒空闲主对话；不要空转追问。
3. `send_message` / `reply_agent`：纠偏给原 Worker，回答实际阻塞。停止后的任务续接带 `expected_revision`；熔断后主对话明确调整 allocation 并带 `acknowledge_escalation`。
4. `stop_work`：暂停全组。浏览器确切写动作另用 `authorize_browser_action`，已有用户授权无需重复请示。
5. reviewer 从原始需求和产物逐项判断，问题回责任 Worker；`deliver_work` 记录主对话最终决定，`close_work` 结束协作。主对话不默认接手修复、不重复有效测试。

旧 18 项 API 保留兼容，完整诊断和并发配置通过 JSON CLI；新 MCP 发现仅呈现以上八个工具。

同一代理的后续消息、纠偏和中断续接沿用 `session_id`，不会静默新建会话；cursor 只排除已经交付的消息/结果。超时、中断或控制器错误会保留会话并暂停，不自动重放不确定的写入。待答问题最多等候 180 秒，超时不是同意。

## 上下文与职责

- DSH 自身会读取适用的 `AGENTS.md`；主对话提供当前目标、已定决定、相关上下文及必要的 Skill/专项规则路径。不要导出整个 Codex 私有会话或内部状态。
- `scope_paths` 指定可写路径，省略表示只读任务。它是任务边界，不是权限白名单或 OS 沙箱；保留已有数据访问和动作授权。`context_files` 与 `required_reads` 只传必要文本材料，单份上限 64 KiB、整包 160 KiB；不传凭据、私钥或私有会话记录。
- 让 DSH 自行选择适当检查、解决失败、清理它创建的专属临时资源，最后说明结果、修改文件、实际检查与剩余阻塞。项目本身适用的要求照常遵守，不因更换执行模型另加一套门槛。
- 超时、中断或错误会保留会话并暂停，避免自动重复不确定的写入。主对话作方向判断，交回 DSH 处理。

## 可靠性与交付

派发时给出验收标准和必要测试；程序记录证据，按请求预留消费预算并限制重试。独立 reviewer 的新会话不继承实现者聊天；低风险豁免须说明理由。主对话依据结果决定交付，关键改动按需审阅；不默认重跑检查或接手修复。集成问题由集成负责人定位，再交回责任 Worker。详见[验收、熔断、Watchdog、消息约束和浏览器授权](references/reliability.md)。

## 当前 Chrome

`submit_work` 的对应 task 传入已分配的 `tab_id` 和预期 `url`，即可启用花叔 MCP，使用当前 Chrome 的登录会话。将目标操作及授权范围交给 DSH；它负责交互、结果检查和普通定位问题。当前共享 Chrome 的浏览器任务串行执行，即使分配不同标签页也共用登录状态。工具入口默认只读；确切写操作由主对话根据已有用户授权发放一次许可，不重复向用户请示。真实登录态不等于授权支付、发消息、删除或写业务数据。详见 [浏览器与底层调用](references/execution.md)。

## 安装与平台

安装后先运行离线预检（不启动模型会话、不调用模型；可能执行 `dsh --version`）：

```bash
node <plugin-root>/dsh-server.mjs --check
# 或在插件目录：npm run check:dsh
```

可通过 `DSH_BIN`、`DSH_PYTHON`、`NODE_BIN`、`HUASHU_CHROME_ENTRY` 和 `DSH_RUNTIME_ROOT` 配置非标准安装路径。macOS 已用真实 DSH 链路验收；Linux 仅做离线/CI 测试，未做真实 DSH 或 Chrome 验收；Windows 暂不支持运行，预检会明确失败。不要声称未验证的平台已通过。

## 能力边界

这是本地 DSH 的协作接口，不是把 DSH 注册为 Codex 内部模型。它拥有自己的工具和持久会话，接收选择后的上下文；不会自动得到 Codex 全部私有工具与聊天历史。正在执行的单个工具不会因普通消息自动撤销，需要停止时用 `stop_work`。主对话不应承诺固定省 token；历史 benchmark 只是历史证据，不是当前 DSH 的实测结果。

文件归属与 `tab_id` 是任务边界，不替代用户授权。运行不复制凭据、不更改 DSH 全局模型配置。注册表与结果默认保存在 `~/.local/share/dsh-delegate/agents`，空闲代理不运行后台工作进程。
