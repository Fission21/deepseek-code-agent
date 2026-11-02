# 协作接口

提供与子代理相近的八个单代理操作：`spawn_agent`、`send_message`、`wait_agent`、`inspect_agent`、`reply_agent`、`interrupt_agent`、`list_agents`、`close_agent`。MCP 注册名为 `dsh_agents`，与旧 OpenCode 的 `deepseek_code_agent` 独立。

并行任务另有 `create_team`、`inspect_team`、`wait_team`、`resume_task`、`cancel_team`、`close_team`、`set_concurrency` 和 `set_team_concurrency`，详见[并发任务组](teams.md)。

## 直接调用

Codex 已加载 `dsh_agents` MCP 时，直接用工具。当前聊天尚未加载时，用同一接口的命令行入口；请求通过 stdin 传 JSON，不要插入 shell 字符串，也不要要求用户代写文件。

```python
import json, subprocess, sys
request = {
    "method": "spawn_agent",
    "params": {
        "title": "修复目标问题",
        "workspace": "/absolute/project",
        "task": "目标、已有发现、约束和预期结果；自行完成实现、相关测试及修复。",
        "scope_paths": ["app/services/example.py", "tests/test_example.py"],
        "context_files": [],
        "required_reads": [],
        "timeout_seconds": 600
    }
}
entry = "/path/to/plugin/skills/dsh-delegate/scripts/agent_api.py"
p = subprocess.run([sys.executable, entry], input=json.dumps(request),
                   text=True, capture_output=True, check=True)
result = json.loads(p.stdout)
```

`entry` 换成插件实际安装位置；安装器可能给路径加随机后缀，因此不要依赖某个固定用户名下的绝对路径。也可设置 `DSH_PYTHON` 让 MCP 启动器选择解释器。

后续沿用返回的 `agent_id`：

| method | params（除 agent_id 外） | 行为 |
| --- | --- | --- |
| `send_message` | `message` | 运行中在下一模型边界进入；空闲/中断后续用原会话 |
| `wait_agent` | `cursor`、`timeout_seconds`（0–55） | 有新进度、提问或结果就返回 |
| `inspect_agent` | 可选 `cursor` | 立即读取紧凑状态 |
| `reply_agent` | `request_id`、`answer` | 回答 `pending_questions` 中的问题，原执行继续 |
| `interrupt_agent` | 可选 `cursor` | 暂停并处理所属进程组，保留会话 |
| `close_agent` | 可选 `cursor` | 结束协作，保留记录，不再接收工作 |
| `list_agents` | 无；也不需要 agent_id | 列出本接口管理的代理 |

每次观察后保存新 `cursor`，避免重复接收旧消息。`waiting_for_parent` 不是要求用户确认：由主对话回答；普通选择自主决定。问题最多等候 180 秒，超时会向 DSH 返回阻塞信息，不能当作默认同意。

`scope_paths` 相对工作区；没有写入范围表示只读。附加材料可用绝对路径或相对工作区路径，只选必要的授权内容。单份附加材料上限 64 KiB，整包 160 KiB；不要静默截断规则。DSH 原生加载全局及项目路径上的 AGENTS/CLAUDE 规则；专项规则和 Skill 可经 `required_reads` 提供。

## 执行中的通信

DSH 加载本 Skill 的 `runtime/controller_link.mjs`，获得：

- `message_parent`：发送有意义的进度、发现或结果，不要求每步汇报。
- `ask_parent`：缺少必要信息时提问并等候主对话回答。
- 主对话消息通过 DSH 原生 `agent/pre-step` 和 `agent/turn-stopping` 接入；无需 DSH 反复调用“检查信箱”。

最后一步之后才到达的消息由控制器转为同会话后续回合，不会静默丢弃。普通消息不终止正在执行的工具；需要停止时用 `interrupt_agent`。中断/故障后不自动重放不确定的写入，继续时给 DSH 清楚的纠正方向。工具调用名称和阶段可用于监督，不读取模型思考或原始工具正文。

## 存储与维护

默认代理目录：`~/.local/share/dsh-delegate/agents/dsh-<id>`。每个代理保留配置、会话身份、过滤事件、结果和双向信箱。目录权限 700，消息与结果权限 600。只保存必要协作资料，不复制模型思考或原始工具正文；DSH 自身仍管理其原生会话日志。

底层 `worker.py` 和 `run_dsh.py` 是实现及故障诊断入口，常规任务无需直接管理它们的队列、task 文件或 stop/wait 顺序。空闲时不保留 DSH 工作进程；MCP 服务仅负责工具通信。

本机通过 `dsh-server.mjs` 提供 stdio MCP；注册名称 `dsh_agents`。修改工具注册不会自动让已经开始的聊天重载工具表；当前聊天继续使用 JSON 入口即可。旧 `deepseek_code_agent` 服务仍提供 `ds_*` 接口，但只作为显式 legacy 入口，不应替代 DSH。
