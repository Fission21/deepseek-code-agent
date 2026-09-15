# DeepSeek Code Agent for Codex

[English](./README.md) | 简体中文

**证据状态：** 这是可选的委派实验工具，尚未证明能稳定减少主控 Token。紧密关联的读取分析与诊断默认由 Codex 直接完成。一次读取型对照观察到 Queen Token 少 5.25%，但耗时增加 62.01%，质量和实验干扰因素仍有局限，详见[实测结果与限制](./docs/reading-delegation-retest-2026-09-15.md)。桥接回传变短是独立的传输优化，不等于 Token 节省。

支持把范围明确的编码工作交给 Codex 原生 GPT-5.6 Luna 子 Agent，也支持通过 OpenCode Go 使用 DeepSeek、GLM，或通过 OpenCode 执行器直连 DeepSeek 官方 API。Codex 始终保持主控、审核和最终决定权。

随插件提供的 Skill 会在 Codex 直接处理、原生 Luna 子 Agent 和插件的持久 OpenCode Worker 之间选择。插件为 OpenCode 通道提供持久会话、信箱式纠正、状态等待、上下文 fork、权限请求处理、隔离 Git worktree、diff 检查和定向仓库规则清单。

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
- DeepSeek/GLM 通道需要当前版本的 OpenCode（已用 1.18.30 验证）
- 使用该通道时，Go 模型需要 OpenCode Go 权限；官方模型需要单独的 DeepSeek API 凭据

Codex 原生 Luna 通道直接使用宿主提供的 `gpt-5.6-luna` 子 Agent，不需要 OpenCode、`DEEPSEEK_API_KEY` 或其他外部 Provider 凭据。是否可用以当前 Codex 宿主和账号为准。

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

## 选择执行通道和模型

日常可以直接在 Codex 里口述。例如：“这段范围明确的实现使用 Codex 原生 GPT-5.6 Luna 子 Agent。”这会选择原生通道；没有明确说推理档位时，Codex 应在 spawn 中传 `model="gpt-5.6-luna"` 和 `reasoning_effort="max"`。它不调用 `ds_check`、`ds_spawn_agent` 或 OpenCode，也不能把 Luna 填进插件的 `provider` 字段。Luna 可显式请求 `none`、`low`、`medium`、`high`、`xhigh` 或 `max`；如果明确要求“默认档位/自动”，才省略该字段。如果当前宿主不支持所选组合，Codex 应明确报告，不能静默换通道。

Luna 选择只作用于本次要求的工作。`ds_model_defaults` 只保存 OpenCode Worker 默认值，不能持久化 Codex 原生模型偏好。原生子 Agent 创建后固定使用当时选择的模型；换模型要新建子 Agent。

也可以直接给一组工蜂统一指定模型。例如“当前任务所有工蜂都用 5.6 Luna”，表示从这条指令之后，本任务新建的每个工蜂都走 Codex 原生 Luna；“本次全部工蜂使用 Go GLM 5.3 Flash”则让本任务新建的 OpenCode Worker 统一使用该模型，但不改本机默认值。已经创建的工蜂保持原模型，需要换模型时应新建替代工蜂。“所有工蜂”默认只作用于当前任务或当前 Codex 会话；只有明确说“以后 / 全局 / 所有新执行者”才表示持久偏好，而且 Luna 仍不能通过 `ds_model_defaults` 持久化。

普通纠错循环和隔离编码不需要为了 Luna 再经过 OpenCode。Codex 可以在当前任务内保留原生子 Agent，使用宿主原生协调能力排队或发送追加消息、等待或中断，并把 Luna 分配到宿主管理或 Codex 预先创建的 Git worktree。这些属于 Codex 编排能力，不是 Luna 模型能力。只有需要跨任务/重启发现控制器状态、OpenCode 上下文 fork、外部 Provider 用量记录或持久化 `task_spec` 验收时，才应因为生命周期能力优先 OpenCode Worker。

[OpenAI 官方将 GPT-5.6 Luna 定位为面向成本敏感、高吞吐工作负载的模型](https://developers.openai.com/api/docs/models/gpt-5.6-luna)。公开 API 价格不能直接证明 Codex 套餐如何计算原生子 Agent 用量，因此本项目不会在缺少同条件 Codex 实测时承诺固定节省比例。

对于 OpenCode Worker 通道，未保存本机偏好时，内置默认是 `opencode-go/deepseek-v4.1-flash`，推理档位 `max`。`ds_check` 和 `ds_spawn_agent` 可以传入：

| 通道 | `provider` | `model` |
|---|---|---|
| Go DeepSeek | `opencode-go` | `deepseek-v4.1-flash` |
| Go GLM | `opencode-go` | `glm-5.3-flash` |
| 官方 Flash | `deepseek` | `deepseek-flash` |
| 官方 Pro | `deepseek` | `deepseek-v4-pro` |

OpenCode 模型同样可以直接口述。单独提出该通道的模型和推理档位设置时，默认保存到本机全局；如果是临时使用，加上“这次”或“仅当前任务”：

- “以后都用 OpenCode Go 的 GLM 5.3 Flash，推理开到最大”——保存本机全局默认，之后新建的执行者和未来 Codex 任务都继承。
- “这次走 DeepSeek 官方 API，用 Flash，推理最高”——只覆盖当前任务的新执行者。
- “当前默认用什么模型？”——查询实际保存的配置。
- “恢复插件原来的默认模型”——清除本机偏好，恢复内置默认。

“最大 / 最高 / 拉满”都会映射到 `max`。OpenCode 口述由技能转换成明确的 provider、模型和档位，再通过 `ds_model_defaults` 保存到本机；偏好不写进插件包，重装后仍保留。全局只指本插件的新 OpenCode Worker，Codex 主控模型和原生 Luna 子 Agent 不受它影响。

选定的模型会随会话保存，追加消息、队列、重启和 fork 都保持该选择。更换模型需要新建执行者；出错时不会自动换通道。

`variant` 可以省略：沿用本机默认模型时继承保存的档位；显式切换到不同 provider/model 且未指定档位时，所有外部模型都默认 `max`，包括 Go GLM、官方 Flash 和 Pro。同一模型的已保存档位优先，已保存的 `null` 也要保留；显式传 `null` 可让本次请求使用运行时默认值。传字符串时必须是本机模型目录支持的档位。这两个 provider 中已配置到 OpenCode 目录的其他模型 ID 也可使用。

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

如需完全使用 Codex 原生低成本通道，可以直接说：

```text
这段范围明确的实现用 Codex 原生 GPT-5.6 Luna，推理开到最大。不要使用 OpenCode 或外部 Provider 凭据，结果由 Codex 审核并独立验证。
```

使用原生 Luna 时，Codex 通过内置委派传入边界明确的任务和必要仓库上下文；使用 OpenCode 通道时，Codex 传入明确的 `scope_paths`、任务相关 `required_reads` 和精简的 `critical_constraints`。两种通道都由 Codex 独立审核 diff 与测试。

## Queen Token 优化

### 按预期收益与任务复杂度路由

路由依据预期能省下的 Queen 工作、风险以及探索与验证负担，而不是行数或文件数。**小型**已知改动，以及紧密关联的读取分析、诊断，默认由 Codex 直接完成，尤其是 Queen 复核时仍需重做大部分调查的情况。**中型**重复实现或独立资料批次可以考虑委派，但预期省下的 Queen 工作应能抵消派发、审查和纠错成本，并符合时间约束；收益不明确就直接做。读取量大、模型便宜或有空闲并发位，都不是单独成立的委派理由，也不要求先跑基准才能路由。确定值得委派后，范围明确、风险较低的原生子任务优先使用 `gpt-5.6-luna`，当前任务内的连续纠错、消息队列和由 Codex 管理的独立 worktree 也可以继续走 Luna。用户明确选择 DeepSeek/GLM，或任务确实需要跨任务/重启发现、控制器状态落盘、OpenCode 上下文 fork、外部 Provider 用量记录或持久化 `task_spec` 验收时，再优先 OpenCode Worker。**大型或高风险**任务（状态机、并发、权限、事务、迁移等）只能把边界明确的劳动交给任一通道。用户需求的解释、关键决定和最终验收由 Codex 保留；涉及改代码的委派前，Codex 先核查约束范围内的关键契约和设计不变量。已确定值得委派的只读探索，不要求 Queen 派发前先重复读完整条调用链。绝不按 Worker 自报或自测通过来接受；用户对本次或全部工蜂的明确模型选择优先于自动路由，任何失败都不能触发静默跨通道降级。

2026-09-15 的 pilot 在同一基线 commit 上为每个规模各做了一次 Codex 直接执行与委派 DeepSeek 执行：[任务分级 pilot](./docs/task-routing-pilot-2026-09-15.md)。它没有测试 Luna。每格仅一次、双臂并行、顺序未交替，因此它不是 queen 协议要求的三次重复正式基准，结果不能推广，也不承诺固定节省。该 pilot 中，worker 非缓存输入加输出合计比三次 direct 低 13.78%，controller 自身区间的 proxy 比 direct proxy 合计低 55.41%；controller 区间也包含管理 Codex 直接执行对照组和审查两组结果，不能当作纯委派账单。计入缓存后 worker total 高于 direct total，总 Token 和耗时仍可能增加；medium 和 large 的 worker 用量标记为不完整。

### 确定值得委派后：Codex 设计与审核，工蜂执行

Codex 保留用户需求解释、关键设计取舍和最终验收。路由判断支持委派时，工蜂可以先做有范围的只读探索、索引或资料整理。例如：“读取相关 service 和测试，只返回带范围/版本的证据包：结论、源码/测试定位、关键短摘录，以及未核查或不确定项；不要修改文件。”涉及改代码前，Codex 核查关键契约和设计不变量，再给出边界明确的目标与验收案例。原生 Luna 子 Agent 通过 Codex 自身的委派通道接收任务；OpenCode Worker 还可以接收持久化 `task_spec`、worktree 和精确检查命令。普通读取和实现不需要逐步汇报，只在有意义的阻塞时升级，并把普通问题合并反馈。Codex 按风险关键处和缺口独立核查实际补丁，不重做全部探索，也不只凭摘要签收；OpenCode 原有纠错生命周期保持不变。

新增 `ds_verify_agent`：只在 worker 空闲时运行预先保存的检查，保留退出码和完整日志，并将结果绑定到源码、Git 暂存区、任务规范和指令身份。代码变化会使旧证据失效；重复查询只读取同一异步任务，显式设置 `rerun=true` 才重新执行。`ds_wait_agent(return_on="actionable")` 在程序内处理短暂重试，连续重试 120 秒后升级为需处理事件。使用宿主支持的有界等待：55 秒等待和 65 秒宿主超时保持不变，超时可能需要再次进行有界等待；不承诺零轮询、重启后仍自动完成或后台唤醒，旧调用兼容。

详见[任务与验收契约](plugins/deepseek-code-agent/skills/deepseek-coding-delegation/references/control-contract.md)。原历史更新当时完成了本地功能验证，但没有附带模型对比实测；之后有日期的 pilot 或复测应单独记录，不能把这句变成对当前版本永远成立的结论。

默认流程让 **queen（Codex）专注于最影响质量的判断**：理解需求、关键设计与取舍、疑难诊断、风险审查和最终验收。值得委派时，Luna 或 OpenCode Worker 承担范围内的探索、实现、测试、常规修复、文档和证据整理。选择性读取、批量工具输出和精简汇报同样适用于 Queen 直接做，不需要为了降噪先开工蜂。优化目标是主控总 Token（`input + output`）；若报告的 `input` 已含缓存输入，不得再次相加。未缓存输入加输出只作为另列的 proxy，工蜂用量也单独记录；不要求合计 Token 或耗时更低。一次委派一个完整行为；OpenCode 纠错复用持久会话，原生 Luna 更换模型时新建子 Agent。不能仅因读取量大就委派；指定工蜂模型也不等于必须创建工蜂。

`ds_wait_agent` 和 `ds_inspect_agent` 默认只返回精简状态、待处理请求、执行者累计用量和有长度上限的最终交付摘要。普通等待不再带回任务回声、工具过程日志或重复指令清单。需要诊断或补全证据时使用 `detail="full"`，需要 diff 时显式设置 `include_diff=true`。保存首次 spawn 的清单，并持续复用返回的 cursor。

回传内容缩小可以测量，但**不能直接等同于整项任务节省了多少 Token**。桥接器无法读取 queen 用量；必须对同一任务做直接执行与委派的对照，统计实际 queen 用量，包含失败、审核和纠错，并单列 DS 用量。详见 [queen 验收方法](./docs/queen-token-efficiency.md)。

[原有 27 个最终结果的合成基准](./docs/token-routing-benchmark-2026-09-14.md)统计的是 Codex + DeepSeek 总量，在各档任务上委派都更高。保留这一结论；它不能证明新版 queen 单独节省了多少。总 Token 和耗时仍可能增加。

## 安全边界

- Codex 负责范围、授权、业务决定、审核、验证和目标完成状态。
- 原生 Luna 子 Agent 和 OpenCode Worker 都不得在没有用户授权时自行提交、推送、合并、部署、修改生产数据、暴露凭据或丢弃无关改动。
- Luna 使用 Codex 内置委派通道，不接收 OpenCode Provider 字段或外部凭据；任一通道失败都不能静默切换模型或 Provider。
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
