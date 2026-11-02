# DeepSeek Code Agent for Codex

[English](./README.md) | 简体中文

**v0.2.0 状态：** 本机 **DeepSeek Harness（DSH）** 是默认外部执行者。旧 OpenCode/Luna 通道仍保留，但只作为显式 legacy 选择；用户明确要求 DSH 时，安装本插件不会静默替换成 OpenCode 或 Luna。

插件提供 `dsh_agents` MCP、`dsh-delegate` Skill 和当前 DSH 控制器实现：八个协作操作、持久会话、显式上下文包、`deepseek-official / deepseek-flash` + `max` 推理、可选的当前 Chrome huashu 链路，以及保留的旧 `deepseek_code_agent` MCP 与 `ds_*` 工具。

本仓库**不声称 DSH 有固定省 token 收益**。DSH 是外部独立持久会话，有自己的工具和历史；Codex 只发送主对话选择的上下文，不会自动继承 Codex 的全部私有工具、会话或聊天历史。仓库里的历史 benchmark 只属于其原始通道和设置，不是当前 DSH 的实测。

## 合作方式

```mermaid
flowchart TD
  U[用户目标] --> P[Codex 主对话：范围、授权、验收]
  P --> S[spawn_agent：本机 DSH]
  S --> W[DSH：在持久会话中实施、测试、修复、交付]
  W --> Q{wait_agent：进度、提问或结果？}
  Q -->|进度| P
  Q -->|DSH 的普通阻塞问题| P
  P -->|reply_agent：回答对应 request_id| W
  P -->|send_message：纠偏或补充上下文| W
  Q -->|仅缺少用户决定| H[询问用户]
  H --> A[reply_agent：回答继续原执行]
  A --> W
  Q -->|结果或失败| V[主对话审阅实际改动与检查]
  V --> C[DSH 清理本任务创建的资源]
  C --> X[close_agent]
  X --> D[交付]
```

普通问题由主对话决定。DSH 通过 `ask_parent` 提出的问题要用 `reply_agent` 回答对应的 `request_id`；`send_message` 用于补充上下文或纠偏。只有任务中确实缺少的用户决定才问用户。DSH 负责实现、测试、修复和交付，主对话按项目正常标准审阅结果，不额外增加 DSH 专属的重复全套验证，也不要求主对话重跑 DSH 的每个测试。

## 包含内容

- **`dsh_agents`**：默认 MCP，提供 `spawn_agent`、`send_message`、`wait_agent`、`inspect_agent`、`reply_agent`、`interrupt_agent`、`list_agents`、`close_agent`。
- **`dsh-delegate`**：默认 Skill，覆盖 DSH 协作、当前 Chrome 和同一套八接口。
- **DSH 运行时**：`worker.py`、`run_dsh.py`、`agent_api.py`、`context_packet.py`、`mcp_server.py`、controller link 和 `config/max.yml`，位于 `plugins/deepseek-code-agent/skills/dsh-delegate/`。
- **Legacy 通道**：原 OpenCode 控制器（`server.mjs`、`ds_*`）与旧 Skill 仍可用；旧 Skill 已关闭隐式触发。
- **不打包凭据**：不含 API key、Codex/DSH 会话记录、npm 缓存、用户 Chrome 配置、cookie 或浏览数据。

## 依赖与平台状态

| 依赖 | 说明 |
|---|---|
| 支持插件的 Codex | 安装后新开任务，让 MCP 工具和 Skill 加载。 |
| Git | 仓库工作流及旧 worktree 通道需要。 |
| Node.js 18+ | 插件启动器和 MCP transport 使用。 |
| Python 3.9+ | 内置 DSH 控制器使用；可用 `DSH_PYTHON` 指定解释器。 |
| DeepSeek Harness | 需单独安装；默认 `deepseek-official / deepseek-flash`，推理 `max`。 |
| huashu-chrome 1.3.0（可选） | 仅当前 Chrome 浏览器任务需要，不自动安装。 |

平台状态明确如下：

- **macOS**：当前真实使用平台；已知配置为 DSH `0.2.0-rc.2` 和 huashu-chrome `1.3.0`。本次交付新增的自动化验收仅为离线 fake DSH，未新增真实 provider/模型或 Chrome live smoke 验收。
- **Linux**：仅做离线/CI 测试；**未做真实 DSH 模型调用或 Chrome 链路验收**，首次使用前请自行做最小验证。
- **Windows**：DSH 执行暂不支持；预检返回结构化 `unsupported_platform`，不冒充跨平台已通过。旧 OpenCode 通道可在该主机单独使用（若其自身可用）。

## 安装

```text
codex plugin marketplace add Fission21/deepseek-code-agent --ref main
codex plugin add deepseek-code-agent@deepseek-code-agent
```

安装后新开一个 Codex 任务，然后运行离线预检（不启动 DSH 模型会话、不调用模型；可能执行 `dsh --version`）：

```bash
node <plugin-root>/dsh-server.mjs --check
# 在本仓库中：
npm --prefix plugins/deepseek-code-agent run check
```

预检会解析 DSH、Python、Node、DSH runtime 模块目录，以及已配置的 huashu-chrome。它可能执行 `dsh --version`，但不会打开模型会话。显式配置具有权威性：无效的 `DSH_BIN`、`DSH_PYTHON`、`NODE_BIN` 或 `HUASHU_CHROME_ENTRY` 会直接报配置错误，不会静默回退到另一个安装。

DSH 安装位置特殊时，可在启动 Codex 前配置：

| 变量 | 用途 |
|---|---|
| `DSH_BIN` / `--dsh` | DSH CLI 可执行文件 |
| `DSH_PYTHON` | 控制器/MCP 使用的 Python 3.9+ |
| `NODE_BIN` | huashu-chrome MCP 使用的 Node |
| `DSH_RUNTIME_ROOT` | 已安装的 `@deepseek-ai` 模块目录，供 `controller_link` 导入 |
| `HUASHU_CHROME_ENTRY` / `--huashu-entry` | huashu-chrome 的 `src/cli.js` |
| `HUASHU_CHROME_VERSION` | 核对后显式接受其他 huashu 版本 |

不要把凭据写进 prompt、任务文件、仓库文件或插件目录。

## 使用 DSH

用自然语言提出，或显式调用 Skill：

```text
使用 $dsh-delegate 完成这个有界改动，运行相关测试、修复失败并汇报结果。
```

主对话通常按以下顺序调用：

1. `spawn_agent`：传入 `task`、`workspace`、`scope_paths`，以及经选择的 `context_files` / `required_reads`。
2. `wait_agent`：需要进度、待答问题或结果时，带上返回的 `cursor`。
3. `send_message`：纠偏并续用同一会话；`reply_agent`：回答真正的待答问题。
4. `inspect_agent`：紧凑查看状态；方向错误时 `interrupt_agent`。
5. `list_agents`：重启后恢复控制；协作结束时 `close_agent`。

### 八个操作

| 操作 | 作用 |
|---|---|
| `spawn_agent` | 创建持久 DSH 执行者，立即返回。 |
| `send_message` | 补充上下文或纠偏；运行中在下一模型边界进入，空闲时续用原会话。 |
| `wait_agent` | 最多等 55 秒，返回新消息、待答问题、完成、失败或暂停；复用 cursor。 |
| `inspect_agent` | 不等待，读取紧凑状态。 |
| `reply_agent` | 回答 `request_id`，原执行继续。 |
| `interrupt_agent` | 停止当前执行并清理本代理进程组，保留会话。 |
| `list_agents` | 列出本适配器记录的代理。 |
| `close_agent` | 结束协作，保留会话/结果记录。 |

`scope_paths` 是任务边界，不是 OS 权限白名单。`context_files` 和 `required_reads` 是显式文本材料，单份上限 64 KiB，整包 160 KiB；DSH 会原生读取适用的 `AGENTS.md`。不导出凭据、私有 Codex 会话或无关文件。

超时、中断和控制器错误会暂停会话，不会自动重放不确定的写入；后续 `send_message` 在同一会话中带明确纠正继续。待答问题最多等 180 秒，超时不是同意。

### 默认值与上下文

- 模型：`deepseek-official` / `deepseek-flash`，推理 `max`，来自 `skills/dsh-delegate/config/max.yml`。
- `deepseek-flash` 是提供商别名，实际底层版本可能随 DSH 更新；升级后请核实，而不是承诺固定版本。
- 上下文是任务加显式上下文包；不传输主对话的全部私有聊天记录。
- DSH 是独立持久会话，不是把 DSH 注册为 Codex 内部模型。
- 适配器默认把协作记录放在 `~/.local/share/dsh-delegate/agents`；空闲代理只保留文件，不保留后台守护进程。

### 当前 Chrome（可选）

链路是 **DSH → huashu-chrome MCP → 本机共享 bridge → 用户 Chrome 扩展**。huashu-design 是设计技能，不提供该浏览器通道。

`spawn_agent` 传入已分配的 `tab_id` 和预期 `url`。DSH 先 `learnings`，再 snapshot 该标签并核实身份，每次浏览器调用都带这个 tab ID。同一共享标签的操作串行执行；任务期间主对话不要操作该标签。

登录态不等于授权。DSH 只能在用户明确授权的动作范围内使用已登录页面，不能仅因页面已登录就支付、发消息、删除数据或写业务记录。`allowSensitive` 只适用于已经明确授权的具体动作。遇到 `NO_EXTENSION` 应停止并等主对话确认重连；写入结果不明时先核对，绝不重复可能已完成的写操作。

## Legacy OpenCode/Luna 通道

旧通道仍保留以兼容：

- MCP：`deepseek_code_agent`，提供 `ds_spawn_agent`、`ds_check`、`ds_verify_agent`、`ds_send_message`、`ds_wait_agent`、`ds_inspect_agent`、`ds_fork_agent`、`ds_interrupt_agent`、`ds_reply_agent`、`ds_list_agents`、`ds_close_agent`、`ds_model_defaults`。
- Skill：`deepseek-coding-delegation`，已标记 legacy，并配置 `policy.allow_implicit_invocation: false`。
- 只有用户明确要求 OpenCode、`ds_*` 或原生 Luna 时才使用。用户明确要求 DSH 时，绝不能用 OpenCode 或 Luna 替换。

旧前置检查需显式运行：

```bash
npm --prefix plugins/deepseek-code-agent run check:legacy
```

旧 OpenCode 付费 live smoke 同样需显式运行，不属于默认 DSH 流程：

```bash
npm --prefix plugins/deepseek-code-agent run test:legacy-live
```

除非你明确要验证旧 OpenCode provider 并接受配额消耗，否则不要运行。

## 安全边界

- Codex 保留用户需求解释、授权、关键决策和最终验收。
- DSH 负责有界实现、测试、普通修复和交付；它的自报不是验收。
- 委派本身不授权 commit、push、merge、部署、生产数据写入、凭据导出或修改无关文件。
- `scope_paths` 是任务边界，不是安全沙箱。
- 控制器只清理当前 turn 由它启动、且 PID/PGID 归属关系可核实的进程组；必要时再用 DSH 命令确认。不会杀共享服务或无关标签页。
- 默认状态目录属于当前用户，结果和消息使用受限权限。
- 仓库不包含 Chrome cookie、凭据、DSH 会话、npm 缓存或个人数据。

## 开发与测试

可安装插件位于 [`plugins/deepseek-code-agent`](./plugins/deepseek-code-agent)。仓库根同时是 Codex Git marketplace。

```bash
npm --prefix plugins/deepseek-code-agent test
npm --prefix plugins/deepseek-code-agent run check
```

`npm test` 运行：

- Python DSH 测试：八接口、MCP framing、生命周期与进程清理、队列/消息边界、待答问题、中断/超时续接、上下文包上限、越界 scope、安装相对路径/特殊字符路径、输出过滤；
- 现有 Node 测试：旧 OpenCode 控制器、验证、路由策略、MCP 和安装接线。

CI 在 Ubuntu、macOS、Windows 上运行。Linux/macOS 用假 DSH 覆盖 DSH Python 生命周期；Windows 覆盖明确的“不支持运行”预检，以及不依赖 POSIX 进程组的部分。测试不调用真实模型或 provider API；旧付费 live 测试不会进入 CI。

历史测量保留用于审计，不是当前 DSH 证据：

- [2026-09-14 token-routing benchmark](./docs/token-routing-benchmark-2026-09-14.md)
- [2026-09-15 task-routing pilot](./docs/task-routing-pilot-2026-09-15.md)
- [2026-09-15 reading-delegation retest](./docs/reading-delegation-retest-2026-09-15.md)
- [Queen token efficiency protocol](./docs/queen-token-efficiency.md)

这些文档描述其原始 OpenCode/Luna 时代设置、有限次数运行和既定局限，不是 DSH benchmark，也不承诺省 token。

## License

[MIT](./LICENSE)
