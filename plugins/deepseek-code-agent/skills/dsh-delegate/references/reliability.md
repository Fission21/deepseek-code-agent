# 可靠协作与职责边界

主对话领导，Worker 交付。`done` 表示该 Worker 的执行交付，不等于整个用户任务已验收。主对话根据目标、现有证据、关键改动和遗留问题决定交付；**不从头重跑、不默认接手修代码**。发现问题交回原 Worker，集成失败由集成负责人定位后交回责任 Worker。`review_team` 记录这一交付决定，不执行测试，也不增加用户审批。小任务不强制增加独立集成 Worker。

## 派发与证据

任务可传 `acceptance`：

```json
{
  "criteria": ["空输入被拒绝，原有成功路径保持可用"],
  "commands": [["python3", "-m", "unittest", "tests.test_example"]],
  "paths": ["app/example.py", "tests/test_example.py"]
}
```

派发时写清完成标准。缺省标准沿用任务文本；只有文字标准的调研任务不会凭空获得“自动验证通过”。代码任务由主对话指定必要命令及其覆盖的源码、测试、配置路径；集成任务按影响指定集成/全量命令。命令是 argv 数组，不经过隐式 shell；禁止夹带密钥或超出任务授权的真实副作用。

Worker 自行开发、调试和修复；声明的最终验收命令由 runner 在本轮结束后执行一次，避免 Worker 为了回执再执行一遍。记录命令、退出码及执行前后的路径指纹；失败或源码发生变化不会释放依赖。指纹只覆盖声明路径，不能代表遗漏文件、真实设备、线上状态或语义质量。修改验收标准需要主对话重新分配任务，Worker 不能通过修改测试标准宣布通过。

`complete_task` 带 `summary`、`checks`、`rationale`、`open_issues`。这些短字段和产物版本一起传给下游；不另写长交接文档。历史 Worker 缺少新增字段时保留兼容，但不补造证据。

`review_team(team_id, revisions, summary)` 在各任务完成后记录主对话判断；`revisions` 示例为 `{"api":1,"ui":1,"integrate":1}`。`resume_task` 带观察到的 `expected_revision`；旧版本写入被拒绝。修订任务使原交付判断失效。

## 熔断与监督

任务 `limits` 默认值：

| 参数 | 默认 | 含义 |
|---|---:|---|
| `max_failures` | 2 | 失败执行累计到此值后停止续派 |
| `max_elapsed_seconds` | 1800 | 同一任务跨续接的执行时间预算 |
| `progress_timeout_seconds` | 180 | 无模型/工具进度的等待期限 |
| `tool_timeout_seconds` | 300 | 单段工具执行期限 |
| `max_peer_messages` | 12 | 同一任务可成功转发的同伴消息数 |
| `max_peer_rounds` | 2 | 同一关联问题可发起的轮数 |

原有单轮 `timeout_seconds` 仍生效。Worker 心跳与执行进度分开；等父代理回答的 180 秒使用独立问题期限，但不取消总预算。进程退出、超时、卡住或不确定写入不会自动重放。Watchdog 只恢复调度监督，不重开模型任务。默认调度循环超过 45 秒无心跳时尝试终止并恢复；占用任务有结果后才释放，不凭心跳直接认定写入失败。

达到上限返回 `escalated`。主对话选择缩小任务、换执行者或有理由地续派；如果给原 Worker 新预算，用 `resume_task(..., expected_revision, acknowledge_escalation=true, limits=...)`。确认会重新分配执行时间/失败次数预算并留下修订事件；不是自动无限重试，也不默认打扰用户。这里限制的是时间和失败次数，不是人民币消费上限。

## 结构化通信和公平调度

`send_peer` 的 `event_type` 为 `question`、`answer`、`blocked`、`artifact_ready`。问答必须带 `correlation_id`；超额、过期契约版本和错误目标写入 `peer_rejected` 事件，交主对话处理，不用自动回复制造新一轮模型对话。

需要共同接口时，主对话在 `create_team` 的 `contract` 中给出定稿，各 Worker 接收同一版；接口争议回主对话，不允许同伴消息更改任务权限或契约。默认契约版本 1，本组内不可由 Worker 更新。

全局额度是硬顶，组额度是这个池内的占用上限。多个就绪组按最近派发顺序轮转；无竞争时借用全部空闲额度，不预留闲置名额，不抢占运行中的任务。资源冲突、依赖和组上限仍优先。

SQLite 使用 WAL 和 10 秒 busy timeout；事务仍由多个控制进程串行写入。没有宣称 WAL 消除锁竞争，也没有改成单一数据库服务。

## 当前 Chrome 的动作授权

DSH → `browser_gate.py` → 花叔 MCP → 当前 Chrome。默认开放指定 tab/origin 的 snapshot、read_text、query、screenshot、scroll、wait 和域名经验读取。截图落盘/抢焦点、任意 eval/fetch、其他标签操作和文件上传下载不开放。

写操作需要确切参数的一次授权：

1. 拦截后生成 `browser_requests`，也写入团队事件；DSH 用 `ask_parent` 等待。
2. 主对话检查该动作是否已在用户授权范围内，调用 `authorize_browser_action(agent_id, request_id, expected_revision, reason)`，随后 `reply_agent` 让原执行继续。已有授权不再问用户；普通“同意”文本不能代替动作授权。
3. 授权绑定本次执行、页面 URL 指纹、工具名和完整参数，60 秒内有效，转发前消费一次。超时/断连不退还授权，不能盲目重试可能成功的写入。

本入口支持花叔 1.3.0 的标签元数据格式，格式不明时拒绝转发。调用前后检查标签 origin，禁止显式跨站导航；页面在两次检查之间仍可能变化，批量动作也不构成浏览器原子事务。此代理是合作模型的工具边界，**不是针对同一系统用户下恶意 shell 的安全沙箱**。高风险环境需另行提供进程权限隔离和专用浏览器配置；单独 Chrome profile 也不能隔离同一账号的服务端写入。
