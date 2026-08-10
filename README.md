# Hermes Amazon Ads Full-Managed ACOS Autopilot

面向 Amazon Ads 的**广告归因 ACOS 全托管自运营系统**。它只使用广告侧报告、Marketing Stream、Recommendations、MCP 和 Ads API，不接入 Seller Central，不读取成本、库存、自然销量、订单、退款、价格或 Buy Box。

当前封存线为 **4.2.1 / control-policy v6.1**。Hermes（包括通过 Hermes Studio 发起的对话）是操作入口，Amazon Ads Control 是不可绕过的权限、预算、执行、验证与审计边界。

## 产品形态

- **Hermes / Hermes Studio 是操作入口**：目标、每日预算、探索比例、暂停、恢复、解释和临时约束都直接告诉 Hermes。
- **Web 是所有者控制与可视面板**：展示花费、广告销售额、当前/目标 ACOS、趋势、AI 最近操作、异常、预算安全状态和紧急开关。
- **正常 SP 操作不逐项审批**：硬边界内的竞价、预算、Placement、否词、Harvest、可逆暂停/恢复和结构维护自动执行。
- **允许有界试错**：历史数据决定动作幅度和扩量速度，而不是决定 AI 有没有资格做一个小额可逆实验。
- **异常才打扰**：OAuth、报告、Schema、数据新鲜度、限流、预算边界、写入不确定、验证不一致、磁盘和运行时异常才通知用户。

## 预算有界自主权

所有增加广告资金暴露的写入都受账户级每日硬上限约束。Web 可设置：

- 目标 ACOS；
- 每日总广告预算硬上限；
- AI 探索预算比例；
- 单 Campaign 日预算上限。

控制器要求在增加 exposure 前对精确 Profile 做一次**新鲜、完整的 Amazon Campaign 预算读取**，并自行计算当前暴露、尚未被新读取吸收的已保留增量和计划动作的新增暴露。

默认预算状态机：

- `< 80%`：允许正常优化和有界探索；
- `>= 80%`：停止新探索；
- `>= 90%`：停止增加资金暴露，只允许 exposure-neutral 或降风险动作；
- `100%`：硬拒绝任何进一步增加暴露的动作。

小额探索 Campaign 使用 `HERMES-SP-EXP-` 命名空间并以 `PAUSED` 创建。失败实验只要始终处在授权损失包络内，就是可接受的学习成本；任何扩量仍必须由后续证据支持。

## 全托管闭环

Amazon Ads 数据进入延迟归因和贝叶斯 ACOS 后验，生成竞价、否词、Harvest、预算、小时节奏、生命周期和结构决策。每次只执行一个实体，写前回读并 Compare-And-Set，写后由**不同 Hermes Session** 独立读回，最后写入 SQLite 审计、恢复和异常通知。

定时运行也不再拥有一条旁路：systemd 只触发 Hermes one-shot。它和 Hermes Studio / CLI 使用同一个启用 `amazon-ads-control` 插件的 Hermes Profile，因此仍经过同一套 pre/post-tool hooks、Executor/Verifier 绑定和控制面授权。

## 自主范围

默认只对 Sponsored Products 开放长期自主权限：Keyword、Product Target、Default Bid、Search Term 否定与 Exact Harvest、Placement、Campaign 预算、小时 Pacing、可逆暂停/恢复以及 Campaign/Ad Group/Product Ad/Target/Keyword 原子结构维护和有界探索。

调用方不需要传入“批准我”的标记。只要完整计划符合 SP 封存包络，控制面会自动释放执行；一旦越界则直接拒绝，而不是让模型自行解释或绕过。

## 不可绕过的硬边界

- 每日总预算硬上限不可关闭；
- Campaign 使用 `HERMES-SP-` 命名空间，探索使用 `HERMES-SP-EXP-`，结构创建均以 `PAUSED` 开始；
- 单 Campaign 日预算、创建数量、状态迁移和累计暴露受限；
- Product Ad ASIN 必须来自同 Profile 的可信广告证据；
- 状态只允许控制器认可的可逆迁移；
- Main 和 Verifier 永远不能写；只有当前绑定 Executor 可以执行控制器已保留的精确动作；
- Billing、Payment、账户、用户、角色和权限永久禁止；
- Delete、Archive、Remove、跨区域、未知语义、Schema 漂移和黑盒复合写入永久禁止；
- 写入结果不确定时停止，不盲目重试；后续激活阶段保持封锁直到独立 reconciliation。

封存级安全来自预算上限、权限边界、原子动作、幂等、CAS、独立验证、证据血缘和熔断，而不是让用户逐项审批。

## Hermes Studio

Hermes Studio 只是聊天/Web 表面，不获得额外 Amazon 权限。部署时 Hermes Studio、交互式 Hermes 和定时触发器必须选中同一个 `HERMES_HOME` / `HERMES_PROFILE`，并显式启用 `amazon-ads-control` 插件。

完整接入与验收见 `docs/HERMES_STUDIO_INTEGRATION.md`。仓库提供：

```bash
bash scripts/install.sh
bash scripts/validate_hermes_studio.sh
# 在真实 Studio/Hermes + 本地控制面已运行时：
bash scripts/validate_hermes_studio.sh --live
```

不要把 Amazon OAuth、`ADS_CONTROL_AGENT_TOKEN`、Hermes provider credentials 或 Studio/JWT secret 暴露给浏览器 JavaScript、公开 HTML、截图或日志。

## 部署与封存验证

```bash
bash scripts/install.sh
bash scripts/validate.sh
bash scripts/coverage.sh
bash scripts/validate_deploy.sh
bash scripts/run_full_sandbox.sh
```

CI 对 Python 3.11/3.12/3.13 的**生产 runtime branch coverage 门槛为 80%**，并单独验证隐私/凭证扫描、Amazon 官方契约、真实 Hermes PluginManager、多浏览器、恢复压力、package/systemd 和 full-managed sandbox。

## 生产验收边界

CI 或 sandbox 通过仍不等于真实账户生产验收。第一次生产启用必须使用所有者环境完成：OAuth/刷新、认证 MCP 与 live Schema、真实报告生命周期、真实 429、Marketing Stream、受限 SP Canary、不同 Session 的独立 Amazon 回读、完整归因窗口、目标 2C2G VPS 重启/备份恢复，以及部署后的 Hermes Studio 同 Profile `--live` 验收。

在这些外部证据完成之前，正式状态只能是 **PASS_WITH_EXTERNAL_ACCEPTANCE**，不能宣称 LIVE FULL-MANAGED PRODUCTION ACCEPTED。

## 隐私

仓库禁止提交真实 Amazon Profile ID、advertiser/account ID、凭证、邮箱和公网 IP 等账户/个人标识。`scripts/verify-no-secrets.py` 会在 CI 扫描当前提交与生成 artifacts。Git 历史中的旧泄露必须通过独立历史清理处置；仅删除 HEAD 中的字符串不代表历史已经消失。
