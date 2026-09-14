# DeepSeek Code Agent for Codex

[English](./README.md) | 简体中文

通过 OpenCode Go 把范围明确的编码实现交给 DeepSeek V4.1 Flash，同时由 Codex 保持主控、审核和最终决定权。

插件提供持久 DeepSeek 会话、信箱式纠正、状态等待、上下文 fork、权限请求处理、隔离 Git worktree、diff 检查和定向仓库规则清单。它适用于“一个执行者编码、Codex 最终把关”的编码目标。

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
- OpenCode，并且账号可以访问 `opencode-go/deepseek-v4.1-flash`
- OpenCode Go 账号或兼容的模型使用权限

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

## 安全边界

- Codex 负责范围、授权、业务决定、审核、验证和目标完成状态。
- DeepSeek 不得自行提交、推送、合并、部署、修改生产数据、暴露凭据或丢弃无关改动。
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
