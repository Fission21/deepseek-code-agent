# DSH 控制与交付边界实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** 落实用户认可的资金准入、独立需求审阅、按需通知、精简工具及明确恢复语义。
**Architecture:** 沿用现有调度器和原子领取；新预算账本按模型请求预留与结算，原始需求驱动新会话 reviewer；默认 MCP 只呈现八个职责明确的入口，兼容 API 保留。普通事件不触发 attention 返回，宿主不支持主动唤醒时明确声明长轮询模式。
**Tech Stack:** Python 3.9+ SQLite，Node 18+，DSH llm/stream middleware。
**Spec:** 本文的已认可需求和边界，来自本轮评估及用户“认可”。

## Global Constraints
- DSH 仅明确指定时调用；本次实现不调用真实模型、不操作真实 Chrome。
- 主对话领导、仲裁与交付，原 Worker 负责修复；不强制重复全量验证。
- 不改用户业务工作区；不打断活跃 DSH；不自动增加实际消费授权。
- 真正宿主推送能力未开放，不能将长轮询/事件过滤宣传为后台唤醒。
- 金额按调用者声明并有有效期的价格上界、模型上下文和输出上界保守预留；未知用量保留预留，不凭超时退款。范围为当前注册表中的 DSH 请求，不含 Codex 主模型账单。
- 旧请求/诊断 API 兼容，新增执行必须绑定已配置预算池；既有进程不迁移途中状态。
- 独立审阅按任务风险配置，轻量任务须写明理由；独立指新会话且非实现者，不能证明语义判断必然正确。
- 事件可去重重复处理；外部副作用结果不明时暂停，不统一重放。SQLite 为状态表+事件日志，非完整事件溯源。

## Review Focus
- 8 并发抢最后预算、进程在扣款后死亡、重试重复结算，不能超额放行或错误退款。
- 缺失/过期价格、未知 provider/model、输出或上下文超过已声明上界，必须请求前拒绝。
- reviewer 自审、漏需求、源码/版本变动后沿用旧报告，不能交付。
- 心跳/普通进度/同伴回执不能变成需要主对话决定的事件；超时不冒充完成或主动推送。
- 调度器/Worker 异常后保留不确定结果，停机前创建的副作用不能自动执行第二遍。

## Tasks
### Task 1: 每次模型请求的资金控制
Files: scripts/budget.py, scripts/scheduler.py, scripts/run_dsh.py, runtime/budget_gate.mjs, runtime/controller_link.mjs, tests/dsh/test_budget.py, tests/budget-gate.test.mjs.
Interfaces: BudgetLedger.configure(policy, default=False), binding(pool_id=None), reserve(pool_id, request_id, owner), settle(request_id, usage); middleware installBudgetGate(ctx, config).
- [ ] RED: 原子预留、同键重放拒绝、结算幂等、未知用量不释放、过期/不匹配路由拒绝和 downstream 未调用。
- [ ] GREEN: SQLite BEGIN IMMEDIATE 预留；llm/stream 前预留，上界校验，正常 usage 保守结算；错误保留预算；控制面配置池。
- [ ] 执行定向 Python/Node 测试，记录实际结果。
### Task 2: 独立需求审阅
Files: scripts/review_policy.py, scripts/scheduler.py, runtime/controller_link.mjs, tests/dsh/test_review_policy.py.
Interfaces: expand_review_tasks(params), validate_review(task, result, dependencies); review_team enforces policy.
- [ ] RED: 独立新会话、逐需求判定、拒绝自审/漏项/过时指纹、轻量豁免需理由。
- [ ] GREEN: 按 workspace 生成只读 reviewer，原始任务与验收标准传入；源码与修订绑定；问题返回责任 Worker。
- [ ] 定向测试通过。
### Task 3: 按需观察与八个日常接口
Files: scripts/attention.py, scripts/agent_api.py, scripts/mcp_server.py, tests/dsh/test_attention.py, tests/dsh/test_mcp.py.
Interfaces: submit_work, observe_work, send_message, reply_agent, stop_work, authorize_browser_action, deliver_work, close_work. observe_work supports wait and compact delta; no hidden push claim.
- [ ] RED: 普通进度不提前唤醒、阻塞/授权/升级/整体完成返回、游标去重、八工具发现与旧请求兼容。
- [ ] GREEN: 程序过滤、增量状态、终态去重，保留显式诊断查询；高级操作保留 CLI。
- [ ] 定向测试通过。
### Task 4: 恢复语义、等待约束与交付
Files: scripts/watchdog.py, scripts/scheduler.py, references/*, SKILL.md, README*.md, tests/dsh/test_reliability_runtime.py.
- [ ] 回归调度器死亡恢复、Worker 不确定状态暂停、预算预留跨重启保留及原子名额领取。
- [ ] 阻止运行中对排队同伴发起会占位等待的问答；依赖预声明，超时不等于同意。
- [ ] 文档明确账本事实源、幂等范围、故障边界、价格上界及宿主限制，版本 0.5.0。
- [ ] 全套离线测试；一次全新上下文独立审阅，必要修复后同步 GitHub/本机并收尾。

## Progress
- Baseline: 02b8a9f67cbca0abb313f3a053292ddd681bf5b2.
- Ruling: 复用既有 docs/plans 路径，按当前明确认可继续实施，不重复审批。
- Ruling: 预算未配置时拒绝新执行；不会为用户擅自设置金额或价格。成本是首次使用需配置一次明确授权预算。
- Ruling: 只实现本机可提供的 attention long-poll，后台主对话唤醒仍需宿主支持。宿主若每次空超时都回模型，仍有等待成本。
