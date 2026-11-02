# 0.5 控制与交付

主对话分工、裁决、交付；实现者修复自己的问题。此版本把程序可判断的约束放到程序里，避免主对话逐步点接口或重复跑检查。

## 八个日常工具

| 工具 | 用法 |
|---|---|
| `submit_work` | `tasks` 数组；单项任务也用同一入口。可给 `contract`、`max_parallel`、`budget_pool`、`review_policy`。 |
| `observe_work` | 无 team_id 时列组和未纳管旧会话（不启动它们）；有 team_id 时观察决策事件和全组完成。复用原样返回的 cursor，等待不超过 55 秒。 |
| `send_message` | 纠偏给原 Worker；已停止任务可带 `expected_revision`。熔断后明确 `acknowledge_escalation` 并调整任务/limits。 |
| `reply_agent` | 答复 request_id；普通回复不能替代浏览器动作授权。 |
| `stop_work` | 暂停全组并收尾所属进程。 |
| `authorize_browser_action` | 对用户已授权的具体动作发放一次许可。 |
| `deliver_work` | 主对话带当前全部 revisions 与 summary 记录交付决定。 |
| `close_work` | 结束协作、保留记录。 |

旧 `spawn_agent/create_team/inspect_team/wait_team/resume_task` 等 API 仍可通过 JSON CLI 调用，旧 MCP 客户端也可继续调用已知名称。并发调整和完整诊断留在 CLI。旧 observe/wait 的返回语义不变，不作为新流程的默认等待入口。工具发现减少不代表每次必然减少固定数量的 token，宿主可能按需加载工具。

## 独立需求审阅

`submit_work` 默认 `review_policy: independent`：每个工作区追加一个只读 reviewer，前置为该工作区所有实现任务，使用新建 DSH 会话。它得到原始需求、context_files 需求附件、required_reads 必读规则、逐项验收标准、定稿契约和版本化证据；不继承实现者的聊天及成功叙述。reviewer 仍使用 Flash/max；独立性来自新会话与职责隔离，不宣称不同模型或一定判断正确。

`complete_task` 的 `review_json` 必须逐项包含 criterion、pass/fail/unverified 和具体依据。缺项、自审会话、非 pass、源码指纹/修订改变都会阻止交付。只绑定声明的文件范围，不能证明未声明文件、外部页面或线上状态。reviewer 对缺少的业务证据应报 unverified。发现问题回责任 Worker；主对话仲裁后让原任务修复，再续接过期 reviewer，不替 Worker 修代码。

低风险工作可用 `review_policy: light`，同时写清 `review_reason`；仍由主对话判断需求是否满足。旧 API 不指定策略时仅保留执行兼容，不自动获得“已验收”。新工作应使用 submit_work；历史工作缺独立审阅时不能直接补造通过。仅对 0.5 之前缺少策略字段的旧任务，主对话可依据现有需求证据，在 deliver_work 中填写 legacy_review_reason 选择有理由的轻量审阅迁移；不重跑原实现、不改变旧结果。新提交的未设策略工作不能借此绕过策略。

## 共享消费预算

预算只控制本适配器的 DSH 原生模型调用，不包括 Codex 主模型、其他聊天绕开此入口的工具或 Worker 自行运行的付费 shell/API。它是合作式成本控制，不是针对同一用户恶意代码的财务沙箱。

控制面在共同注册表创建一次池；Worker 不应配置池或更改金额。每次 `llm/stream` 前用 SQLite `BEGIN IMMEDIATE` 原子预留，包含模型层重试。可用额 = 总额 − 已结算保守金额 − 未决预留。额度不足停派新任务、拒绝新模型请求；已获预留的请求可以结束，Worker 的下一次请求仍须过门。不能把“在途任务”当成整段会话无限续费许可。

输入按配置的模型上下文上界预留，输出按配置上界；成功后根据 SDK usage 按价格上界结算。缓存按未命中上界计入。文本请求释放未用额度；媒体请求可能有适配器内部重试，额外一次的完整上界不自动释放。超时、崩溃、无 usage 保留预留；这不是实际发票，不能将保守账本数字当成真实消费。请求 ID 重放拒绝，重复同值结算幂等，冲突结算拒绝。

目前适配器审计固定到 DSH 0.2.0-rc.2 的两份核心模块指纹。升级导致指纹变化时拒绝付费调用，需重新检查请求边界。缺预算、价格过期、路由不匹配、上下文/输出上界不明时均不发请求。内置 `web_search/web_fetch` 不经过此模型拦截点，因此当前受预算管理的执行会拒绝这两个入口；可使用主对话提供的资料或已分配的花叔 Chrome。不得把它们误报成已计入预算。

配置入口（JSON 走 stdin，不把密钥放入 policy）：

```python
import json, subprocess, sys
request = {"policy": authorized_policy, "default": True}
subprocess.run([sys.executable, "/path/to/skill/scripts/budget.py",
                "--root", "/absolute/shared/registry", "configure"],
               input=json.dumps(request), text=True, check=True)
```

policy 必填：`pool_id`、`limit_micros`、`provider: deepseek-official`、`model: deepseek-flash`、`input_price_micros`、`output_price_micros`、`max_input_tokens`、`max_output_tokens`、`attempt_bound: 2`、`expires_at`。金额单位为百万分之一人民币，单价为每百万 tokens 的微元。金额必须来自用户授权，单价用有效期内的官方价格上界；不借用业务项目预算。`snapshot` 接收 `{"pool_id":"..."}`。重复 configure 同名池会拒绝，不能通过重置池清空累计支出。价格到期后主对话核对官方价格，可用 refresh_prices(pool_id, input_price_micros, output_price_micros, expires_at) 刷新；它不会改变授权总额或累计支出，已预留请求按各自旧价格快照结算。刷新本身不续派暂停任务。

## 静默事件与宿主边界

普通进度、心跳、同伴回执及单个阶段完成仍落账本，不使 observe_work 提前返回。提问、阻塞、争议、浏览器授权、失败/预算熔断和全组完成才返回；cursor 去重并推进到已过滤事件之后。终态在相同修订下只通知一次。

返回声明 `delivery_mode: long_poll`、`host_push_available: false`。程序内部等待不调用模型；宿主在 55 秒空超时后若再次运行主模型，依然有成本。此版本不能主动唤醒已空闲的 Codex 聊天，不能宣传为零轮询成本。主对话有其他工作时先做其他工作，不用“怎么样了”反复追问 Worker。

## 调度与恢复语义

全局上限是硬顶，组上限是池内占用天花板，不是“必须同时拿满几个名额”。一个事务检查依赖、组/全局额度和资源互斥后领取单个任务；拿不到就留队，没有先占一个名额再等另一个名额。运行任务不得占位等待排队同伴；question 只转发给运行同伴，阶段关系用 depends_on。ask_parent 的真实决策等待有 180 秒上限，但程序不能理解文本里所有隐含循环依赖。

SQLite 是当前状态表加事件日志，不是完整事件溯源。WAL、事务和修订检查保留；多个控制进程仍可串行写库，没有声称改成单写者服务。消息收据与稳定消息 ID 可去重重复处理；这不能延伸为所有外部动作 exactly-once。

独立 Watchdog 恢复死亡/失去心跳的调度器。它和调度器同时死亡、整机重启时，需要下次控制入口唤起；没有安装 OS 常驻服务。恢复从持久任务状态、active invocation、进程归属和结果记录续接监督。Worker 执行结果不明时停止残留进程并暂停，不自动重放。浏览器授权在转发前消费，断线不能退款重试；外部副作用必须通过目标服务的幂等 API 或先确认实际结果来恢复。没有通用“最后 checkpoint 后全部重算”的保证。
