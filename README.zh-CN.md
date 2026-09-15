# DeepSeek Code Agent for Codex

[English](./README.md) | 简体中文

支持通过 OpenCode Go 使用 DeepSeek、GLM，也支持通过 OpenCode 执行器直连 DeepSeek 官方 API。把范围明确的编码实现交给执行者，由 Codex 保持主控、审核和最终决定权。

插件提供持久执行者会话、信箱式纠正、状态等待、上下文 fork、权限请求处理、隔离 Git worktree、diff 检查和定向仓库规则清单。它适用于“一个执行者编码、Codex 最终把关”的编码目标。

## 在 Codex 中一句话安装

把下面这句话粘贴到一个新的本地 Codex 任务中：

> 请访问 <https://github.com/Fission21/deepseek-code-agent>，阅读 README，按当前操作系统安装这个插件并运行前置检查；不要要求我把任何密钥粘贴到聊天中。

Codex 会根据下面的系统说明准备依赖并安装 Git marketplace。如果当前权限模式要求审批，请在执行前检查它准备运行的本地命令。

## 直接安装

以下 Codex 命令在 Windows、macOS 和 Linux 上相同：

```text
codex plugin marketplace add Fission21/deepseek-code-agent --ref main
codex plugin add deepseek-code-agent@deepseek-code-agent
```

安装完成后新建 Codex 任务，新的任务才会加载 MCP 工具和 Skill。

## 运行要求

- 支持插件的本地 Codex 桌面应用或 Codex CLI
- Git
- Node.js 18 或更高版本
- 当前版本的 OpenCode（已用 1.18.30 验证）
- Go 模型需要 OpenCode Go 权限；官方模型需要单独的 DeepSeek API 凭据

插件不保存 API Key。每台电脑都要在本机交互式认证：

```text
opencode auth login
opencode models opencode-go
```

确认模型列表中包含：

```text
opencode-go/deepseek-v4.1-flash
```

不要把 Provider Key 粘贴到 Codex 提示词、GitHub Issue、日志或仓库文件中。

## 选择模型

未保存本机偏好时，内置默认是 `opencode-go/deepseek-v4.1-flash`，推理档位 `max`。`ds_check` 和 `ds_spawn_agent` 现在都可以传入：

| 通道 | `provider` | `model` |
|---|---|---|
| Go DeepSeek | `opencode-go` | `deepseek-v4.1-flash` |
| Go GLM | `opencode-go` | `glm-5.3-flash` |
| 官方 Flash | `deepseek` | `deepseek-flash` |
| 官方 Pro | `deepseek` | `deepseek-v4-pro` |

日常建议直接口述。单独提出模型和推理档位设置时，默认保存到本机全局；如果是临时使用，加上“这次”或“仅当前任务”：

- “以后都用 OpenCode Go 的 GLM 5.3 Flash，推理开到最大”——保存本机全局默认，之后新建的执行者和未来 Codex 任务都继承。
- “这次走 DeepSeek 官方 API，用 Flash，推理最高”——只覆盖当前任务的新执行者。
- “当前默认用什么模型？”——查询实际保存的配置。
- “恢复插件原来的默认模型”——清除本机偏好，恢复内置默认。

“最大 / 最高 / 拉满”都会映射到 `max`。口述由技能转换成明确的 provider、模型和档位，再通过 `ds_model_defaults` 保存到本机；偏好不写进插件包，重装后仍保留。全局指本插件的新执行者，Codex 主控模型仍由应用设置决定。

选定的模型会随会话保存，追加消息、队列、重启和 fork 都保持该选择。更换模型需要新建执行者；出错时不会自动换通道。

`variant` 可以省略：沿用本机默认模型时继承保存的档位；显式切换到不同模型时，原有 Go DeepSeek 模型默认 `max`，其他模型使用运行时默认档位。显式传 `null` 可让任意模型使用运行时默认值；传字符串时必须是本机模型目录支持的档位。这两个 provider 中已配置到 OpenCode 目录的其他模型 ID 也可使用。

走官方 API 时，在 OpenCode 中使用 `/connect → DeepSeek`，也可以运行 `opencode auth login`。另一种方式是在启动 Codex 前给宿主进程设置 `DEEPSEEK_API_KEY`，插件会传给 OpenCode。官方内置 provider 通常请求 `https://api.deepseek.com`，使用独立的 DeepSeek API 账号；工具执行和多轮上下文仍由 OpenCode 管理。插件不会改写 endpoint 或保存密钥。

```bash
npm --prefix plugins/deepseek-code-agent run check -- --model glm-5.3-flash --variant max
npm --prefix plugins/deepseek-code-agent run check -- --provider deepseek
```

前置检查会区分“目录中有模型”和“本机 provider 已配置”，不会请求推理模型，也不会验证密钥、余额或额度。项目配置、缺失模型和更多命令见 [模型选择与排错](plugins/deepseek-code-agent/skills/deepseek-coding-delegation/references/model-selection.md)。模型 ID 依据 [OpenCode Go 文档](https://opencode.ai/docs/go/) 和 [DeepSeek 当前官方模型说明](https://api-docs.deepseek.com/quick_start/pricing/)。

## 分系统准备

### macOS

使用常用包管理器安装 Git 和 Node.js，然后安装 OpenCode：

```bash
npm install -g opencode-ai@latest
opencode --version
```

### Linux

使用发行版包管理器或 Node 安装器准备 Git 和 Node.js 18+，然后执行：

```bash
npm install -g opencode-ai@latest
opencode --version
```

### Windows

安装 Git for Windows 和 Node.js 18+，打开 PowerShell，然后执行：

```powershell
npm install -g opencode-ai@latest
opencode --version
```

桥接器会识别 Windows npm 生成的 `opencode.cmd`，并通过 `cmd.exe` 启动。如果 OpenCode 安装在特殊位置，请在启动 Codex 前把 `OPENCODE_BIN` 设置为对应 `.cmd`、`.bat` 或可执行文件的完整路径。

OpenCode 的安装命令可能变化；如果 npm 命令失效，请查阅 [OpenCode 官方文档](https://opencode.ai/docs/)。

## 验证安装

在新的 Codex 任务中发送：

> 运行 DeepSeek Code Agent 前置检查，并告诉我识别到的系统、Node、Git、OpenCode、Provider、模型和推理档位。

也可以在本仓库运行：

```bash
npm --prefix plugins/deepseek-code-agent test
npm --prefix plugins/deepseek-code-agent run check
```

真实冒烟会调用一次模型，可能消耗套餐额度：

```bash
npm --prefix plugins/deepseek-code-agent run test:live
```

## 使用方式

在 Codex 中创建编码目标，或者显式调用随插件提供的 Skill：

```text
/goal 完成这个功能。由 Codex 做主控，把范围明确的编码实现委派给 DeepSeek。
```

```text
使用 $deepseek-coding-delegation 委派实现，并由 Codex 审核结果。
```

Codex 会选择相关仓库规则，传入明确的 `scope_paths`、任务相关 `required_reads` 和精简的 `critical_constraints`，然后独立审核 diff 与测试。较大的规则文档可以只指定相关章节，不需要整份复制进提示词。

## Queen Token 优化

### Codex 设计与审核，GLM 实现

Codex 先阅读关键调用链，明确接口、必须保持的行为和验收案例，通过可选 `task_spec` 一次交付设计决定和检查命令。执行模型在同一会话中完成实现、自测和常规修复，Codex 集中审核并批量反馈；两轮修正仍未通过时，确认 worker 和验收进程停止后再接管。

新增 `ds_verify_agent`：只在 worker 空闲时运行预先保存的检查，保留退出码和完整日志，并将结果绑定到源码、Git 暂存区、任务规范和指令身份。代码变化会使旧证据失效；重复查询只读取同一异步任务，显式设置 `rerun=true` 才重新执行。`ds_wait_agent(return_on="actionable")` 在程序内处理短暂重试，连续重试 120 秒后升级为需处理事件。55 秒等待和 65 秒宿主超时保持不变，旧调用兼容。

详见[任务与验收契约](plugins/deepseek-code-agent/skills/deepseek-coding-delegation/references/control-contract.md)。本次完成本地功能验证，未进行新的模型对比实测，因此尚不声称达到具体的 Token 节省比例或耗时目标。

默认流程让 **queen（Codex）专注于最影响质量的判断**：理解需求、关键设计与取舍、疑难诊断、风险审查和最终验收。工蜂承担量大的代码探索、实现、测试、常规修复、文档和证据整理。省 queen Token 应该来自把这些工作交出去，同时保持质量。一次委派一个完整行为，纠错复用同一会话；已知的小改动仍可直接完成。

`ds_wait_agent` 和 `ds_inspect_agent` 默认只返回精简状态、待处理请求、执行者累计用量和有长度上限的最终交付摘要。普通等待不再带回任务回声、工具过程日志或重复指令清单。需要诊断或补全证据时使用 `detail="full"`，需要 diff 时显式设置 `include_diff=true`。保存首次 spawn 的清单，并持续复用返回的 cursor。

回传内容缩小可以测量，但**不能直接等同于整项任务节省了多少 Token**。桥接器无法读取 queen 用量；必须对同一任务做直接执行与委派的对照，统计实际 queen 用量，包含失败、审核和纠错，并单列 DS 用量。详见 [queen 验收方法](./docs/queen-token-efficiency.md)。

[原有 27 个最终结果的合成基准](./docs/token-routing-benchmark-2026-09-14.md)统计的是 Codex + DeepSeek 总量，在各档任务上委派都更高。保留这一结论；它不能证明新版 queen 单独节省了多少。总 Token 和耗时仍可能增加。

## 安全边界

- Codex 负责范围、授权、业务决定、审核、验证和目标完成状态。
- 执行者不得自行提交、推送、合并、部署、修改生产数据、暴露凭据或丢弃无关改动。
- 隔离 worktree 从已提交的 `HEAD` 创建，不包含未提交文件。
- 指令清单记录执行者收到的准确路径、哈希和读取范围，但不能证明语义上完全服从，因此 Codex 仍须审核补丁。
- 本地 OpenCode 服务只绑定 `127.0.0.1`，每次进程使用随机密码。

## 开发

可安装插件位于 [`plugins/deepseek-code-agent`](./plugins/deepseek-code-agent)，仓库根目录同时也是 Codex Git marketplace。

```bash
npm --prefix plugins/deepseek-code-agent test
python3 /path/to/plugin-creator/scripts/validate_plugin.py plugins/deepseek-code-agent
```

GitHub Actions 会在 Windows、macOS 和 Linux 上运行测试。公开 CI 不运行 Provider 真实调用，因为仓库不会保存凭据。

## 许可证

[MIT](./LICENSE)
