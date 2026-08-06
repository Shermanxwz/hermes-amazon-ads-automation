# Hermes Amazon Ads Full-Managed ACOS Autopilot

面向 Amazon Ads 的**广告归因 ACOS 全托管自运营系统**。它只使用广告侧报告、Marketing Stream、Recommendations、MCP 和 Ads API，不接入 Seller Central，不读取成本、库存、自然销量、订单、退款、价格或 Buy Box。

## 产品形态

- **Hermes 是操作入口**：目标、暂停、恢复、解释和临时约束都直接告诉 Hermes。
- **Web 是单页可视面板**：只展示花费、广告销售额、当前/目标 ACOS、趋势、AI 最近操作、异常和紧急开关。
- **正常操作不审批**：Sponsored Products 硬边界内的竞价、预算、Placement、否词、Harvest、可逆暂停/恢复和结构维护自动执行。
- **异常才打扰**：OAuth、报告、Schema、数据新鲜度、限流、写入不确定、验证不一致、磁盘和运行时异常才通知用户。

## 全托管闭环

Amazon Ads 数据进入延迟归因和贝叶斯 ACOS 后验，生成竞价、否词、Harvest、预算、小时节奏、生命周期和结构决策。每次只执行一个实体，写前回读并 Compare-And-Set，写后由不同 Hermes Session 独立读回，最后写入 SQLite 审计、恢复和异常通知。

## 自主范围

默认只对 Sponsored Products 开放长期自主权限：Keyword、Product Target、Default Bid、Search Term 否定与 Exact Harvest、Placement、Campaign 预算、小时 Pacing、可逆暂停/恢复以及 Campaign/Ad Group/Product Ad/Target/Keyword 原子结构维护。

调用方不再需要传入审批标记。只要完整计划符合 SP 封存包络，控制面会自动绑定长期授权并释放执行。

## 不可绕过的硬边界

- Campaign 使用 `HERMES-SP-` 命名空间并以 `PAUSED` 创建；
- 单 Campaign 日预算、每日新增预算和创建数量受限；
- Product Ad ASIN 必须来自可信广告证据；
- 状态只允许 `PAUSED ↔ ENABLED`；
- Billing、Payment、账户、用户、角色和权限永久禁止；
- Delete、Archive、Remove、跨区域、未知语义、Schema 漂移和黑盒复合写入永久禁止；
- 写入结果不确定时停止，不盲目重试。

封存级安全来自不可绕过的边界、幂等、独立验证和熔断，而不是让用户逐项审批。

## 使用方式

直接告诉 Hermes：“把目标 ACOS 调整到 25%”、“为什么今天 ACOS 上升？”、“暂停表现最差的两个 Target”、“总结今天所有调整”或“恢复全托管运行”。

Web 只提供全托管/观察/暂停、目标 ACOS、单 Campaign 日预算上限、核心 KPI、ACOS 趋势、AI 操作和异常。

## 部署与封存验证

```bash
bash scripts/install.sh
bash scripts/validate.sh
bash scripts/coverage.sh
bash scripts/validate_deploy.sh
bash scripts/run_full_sandbox.sh
```

生产第一次启用仍须使用所有者凭据完成 OAuth、真实报告、Profile 能力证明、低风险 SP Canary、独立 Amazon 回读、完整归因窗口和 VPS 重启/备份恢复演练。仓库不会伪造这些外部证据。
