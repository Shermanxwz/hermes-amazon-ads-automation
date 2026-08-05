# Hermes Amazon Ads Sealed ACOS Autopilot v4.0

面向 Amazon Ads 的 **广告归因 ACOS 封存级自主运营系统**。系统只使用广告侧数据，不接 Seller Central、成本、库存、自然销量、订单、退款、价格或 Buy Box。封存自主范围默认为 **Sponsored Products**；SB、SD、STV、DSP 保持 Observe 或逐项审批。

## 闭环

```text
Amazon Ads Reports / Marketing Stream / Recommendations
                         │
                         ▼
延迟归因 + 贝叶斯 CVR/AOV/ACOS 后验
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
竞价/否词/Harvest   全局预算/小时节奏   生命周期/结构维护
        └────────────────┼────────────────┘
                         ▼
原子 Decision → 写前回读/CAS → MCP 或 Direct Ads API
                         ▼
真实 Amazon ID 绑定 → 不同 Session Verifier 独立回读
                         ▼
SQLite 审计、Outbox、恢复、告警、存储熔断
```

## 决策内核

v4 不再把点击阈值当作“置信度”。每个动作计算：

- 点击年龄对应的广告归因成熟度；
- 分层收缩后的 CVR 与 AOV；
- 预计最终广告归因订单、销售额和 ACOS；
- `P(最终 ACOS > 最大 ACOS)`；
- `P(最终 ACOS <= 目标 ACOS)`；
- ACOS 区间和不确定性。

降价、否词和隔离需要超目标风险概率足够高；扩价、加预算和恢复需要达标概率足够高。小样本不会因为达到固定点击数而获得 100% 置信度。

## 自主能力

- Keyword、Product Target、Default Bid 调整；
- Search Term 否定与经过验证的 Exact Harvest；
- Placement 调整；
- Campaign 间等额预算转移，不增加账户总暴露；
- Marketing Stream/小时数据驱动的日内小幅 Pacing；
- 高风险实体可逆 `PAUSED` 隔离与验证后恢复；
- 在长期授权包络内原子创建 SP Campaign、Ad Group、Product Ad、Target/Keyword。

## SP 长期授权包络

封存自主不是开放所有写工具。默认包络要求：

- 仅指定且已启用的 Profile；
- 仅 `SPONSORED_PRODUCTS`；
- Campaign 名称以 `HERMES-SP-` 开头；
- 新 Campaign 必须以 `PAUSED` 创建；
- 单 Campaign 新预算默认不超过 50 个账户货币单位；
- 每日新增 Campaign 总预算默认不超过 100；
- 每日最多创建 2 个 Campaign；
- Product Ad 的 ASIN 必须来自可信 Amazon Ads 证据；
- 状态只允许 `PAUSED ↔ ENABLED`；
- `ENABLED` 必须有已验证创建或已验证恢复证据；
- 每次写入只允许一个实体，并绑定精确参数和包络 Hash；
- 写后必须由不同 Session 的 Verifier 独立回读。

Profile 策略一旦变化，待执行 Decision 的包络 Hash 失效并自动阻断。

## 永久禁止

任何设置、LLM 或长期授权都不能开放：

- Billing、Invoice、Payment；
- 用户、角色、权限、邀请和账户链接；
- Delete、Archive、Remove、Purge；
- 跨区域写入；
- 未知工具和未确认 Schema 漂移；
- 黑盒 Composite/Bulk/End-to-End MCP Workflow 直接执行。

复合工作流只能被编译成单实体原子步骤，再分别执行和验证。

## MCP 与 Direct API

认证后的 Amazon Advertising MCP 是首选原子接口。项目同时维护每个必要 SP 操作的确定性 Direct Ads API 回退路由。只有完成 Profile、地区、工具 Schema 和回退路由的 Capability Attestation 后，运行时才可视为能力闭合。

传统 Ads API 作为稳定生产适配器；Unified API GA 纳入合约兼容检查；Unified Reports、Events、Rules、RuleLinks、Labels 等 Beta 资源只允许 Observe，不能成为封存系统的唯一依赖。

## 安全执行

- 实时 MCP Catalog、语义、Family、Risk 和 Schema Hash；
- Profile 与 NA/EU/FE 工具强绑定；
- 所有写入必须匹配唯一 Decision；
- 写前回读、Compare-And-Set、单实体原子预约；
- 不确定 Mutation 不盲目重试；
- Result Outbox 只重投结果信封，不重调 Amazon；
- Runtime 心跳、数据库、Catalog 漂移、Callback、Outbox 和磁盘压力任一异常都会关闭写入；
- 包络外结构、高风险和产品操作仍使用 payload-bound 人工审批。

## 部署

目标环境：2C2G Linux VPS，Executor 与 Verifier 顺序运行。

```bash
bash scripts/install.sh
bash scripts/validate.sh
bash scripts/coverage.sh
bash scripts/validate_deploy.sh
bash scripts/run_full_sandbox.sh
```

默认仍是 `observe` 且执行关闭。生产启用顺序：OAuth → authenticated MCP `initialize/tools/list` → 真实报告闭环 → Profile 能力证明 → Test Account/低风险 SP Canary → 独立回读 → 完整归因窗口 → VPS 重启和备份恢复演练 → `autopilot`。

仓库沙盒不能伪造真实账户授权、真实广告落库或成熟归因效果；这些会在完整沙盒报告中明确列为 `EXTERNAL`，不会被虚假标记为 PASS。详细设计见 `docs/SEALED_ACOS_V4.md` 与 `docs/PRODUCTION_ACCEPTANCE.md`。
