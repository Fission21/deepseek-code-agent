# 调用与浏览器说明

## 安装前提与平台状态

- macOS：已用真实 DSH 0.2.0-rc.2 + huashu-chrome 1.3.0 链路验收。
- Linux：Python/Node 离线测试与 CI 覆盖；**未做真实 DSH 模型调用或 Chrome 验收**，首次使用需自行做最小验证。
- Windows：当前**不支持运行**。`preflight` 会返回 `unsupported_platform`，不会假称跨平台通过；旧 OpenCode lane 仍可单独使用。
- 启动器要求 Python 3.9+、Node.js 18+（仅浏览器链路需要 Node）。升级 DSH 后先核对 `dsh --version`、最小离线预检和一次最小任务，不承诺版本永久不变。

路径全部基于安装位置、当前用户 home 或显式配置动态解析，不写死某个用户名：

| 变量/参数 | 用途 |
| --- | --- |
| `DSH_BIN` / `--dsh` | DSH CLI；否则查找 PATH、macOS `~/Applications` 与 `/Applications` |
| `DSH_PYTHON` | MCP 启动器使用的 Python 解释器 |
| `DSH_RUNTIME_ROOT` | DSH 安装内 `@deepseek-ai` 模块目录，供 `controller_link` 导入 |
| `NODE_BIN` | huashu MCP 使用的 Node |
| `HUASHU_CHROME_ENTRY` / `--huashu-entry` | huashu-chrome `src/cli.js` |
| `HUASHU_CHROME_VERSION` | 显式接受的其他 huashu 版本（默认 1.3.0；升级前先核对接口） |

离线预检不启动 DSH 模型会话、不调用模型；它可能执行 `dsh --version`：

```bash
node <plugin-root>/dsh-server.mjs --check
# 或在插件目录：npm run check:dsh
```

`run_dsh.py --check` 也可用于单次任务前诊断（`--cwd`、`--task-file`、`--result-file` 均需为已存在/新路径）：

```bash
python3 <plugin-root>/skills/dsh-delegate/scripts/run_dsh.py \
  --cwd /absolute/task-workspace \
  --task-file /absolute/task.txt \
  --result-file /absolute/new-result.json \
  --timeout 300 --check
```

## DSH 调用

默认模型配置位于 `config/max.yml`，不是命令行臆造的 `--model`/`--reasoning` 参数。`deepseek-flash` 是提供商别名；对应实际版本随 DSH 更新，须核实，不能承诺别名永久固定版本。

启动器以参数数组执行，不经过 shell；prompt 通过 stdin 传入，不放进进程参数。它加载 headless profile 与仅本次生效的 overlay，使用 `workspace-write` 和 approval `never`；这是本次运行配置，不是任意动作授权。任务文件仍不得含密钥或不必要的敏感原文。

`--check` 是可选的离线诊断，不是每次任务的前置流程；常规委派使用协作接口即可。它不启动 DSH 模型会话、不调用模型、不验证账号余额/扩展连接；可能仅执行 `dsh --version`。

超时或正常中断时终止本次创建的进程组，并删除自己生成的临时 MCP 配置。控制器会记录 DSH 子进程组；即使 launcher 被强杀，也会在可验证 PID/PGID 与 DSH 命令匹配时清理该组。以权限 600 新建 JSON 结果，不覆盖既有结果。stdout 仅显示进度元数据，不打印模型思考或原始工具正文；结果 final 由主助手审阅。

`status=completed` 加退出码 0 只是 transport 成功；失败时检查本次 session 的本地日志，按需脱敏，不输出全局配置或环境变量。底层启动器还支持 `--session-id` 恢复指定会话、`--events-file` 输出过滤后的实时 JSONL；通常由 `worker.py` 管理这些参数，避免会话与工作区错配。未知会话 ID 不会自动降级为新会话。

## 控制当前 Chrome

这条链路是 **DSH → browser_gate.py → huashu-chrome MCP → 本机共享 bridge → 用户 Chrome 扩展**。huashu-design 是设计技能，不提供该浏览器通道。

1. 主助手通过可用的 huashu 工具调用站点 `learnings`。首次验证优先使用无业务数据的本机页面。必要时在后台打开本任务独占标签页：`tabs(action="new", url=..., focus=false, label=...)`；保存并向用户报告返回的 tabId。
2. 核实 URL、账号与任务允许的动作。任务中写清按钮、输入值和期望结果，避免将别的标签页内容提供给 DSH。任务运行期间主助手不操作同一标签页。
3. 在调用中增加 `--tab-id 真实标签ID --url '已核实URL'`。启动器才会加载 MCP；新 DSH 会话无需继承主助手的工具连接。
4. DSH 先 learnings，再 snapshot 核实同一页；每次浏览器调用显式带 tabId。优先按页面已观察到的 role/name 定位，批量执行可预见步骤并读取效果行。
5. DSH 自行读取结果、处理普通定位问题并完成相关交互检查；主助手按通常使用子代理的方式汇总。无需因执行者是 DSH 再强制重做一次页面检查。本任务专属资源按分工收尾；用户原有标签页不因任务结束而关闭。

### 失败处理

| 观测 | 处理 |
| --- | --- |
| `NO_EXTENSION` | 停止浏览器操作，报告扩展未连接；用户确认重连后再做一次有界验证 |
| `STALE_SNAPSHOT`，确认动作未执行 | 在同一页面刷新快照一次，改用已观察到的语义定位；重复失败交还主助手 |
| 页面没有可归因变化 | 读取当前状态，分析目标/渲染情况；不要照样重复点击 |
| 写请求已发出但结果不明 | 先核对结果，不能重复提交 |
| 登录、验证码、页面漂移、权限拒绝 | 停止受影响操作并交还主助手，不绕过限制 |

共享同一标签页的浏览器操作串行执行。huashu 的稀疏后台页面可能内部拍第二次快照但返回第一次编号，所以简单表单也可能遇到过期；不要因此关闭快照保护。

`allowSensitive` 只能用于任务已经明确授权的具体动作；连接真实登录态不等于授权支付、发消息、删除或写业务数据。Skill 不授予这些权限。huashu 可能自动生成站点剧本草稿；临时测试草稿先按来源 URL、时间和 ID 核对归属，再清理本次条目；不采纳为通用经验、不覆盖其他条目。共享 bridge 和 Chrome 不属于一次 DSH 调用的清理对象。

当前入口增加了指定 tab/origin 的只读默认值和一次性动作授权。见[可靠协作中的浏览器授权](reliability.md#当前-chrome-的动作授权)；已有授权由主对话直接处理，普通消息不代替工具授权。
