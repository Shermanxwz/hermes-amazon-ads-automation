# Hermes Amazon Ads Autopilot v2

面向单个 Amazon Ads 运营环境的 **Hermes 原生、确定性、可观察、可独立验证** 的广告自动运营系统。

它只解决广告运营，不接管 Seller Central 账户、收款、税务、申诉、Listing 或订单。Amazon 官方 MCP 是广告能力层；本项目负责广告策略、权限、原子执行、验证、审计和简明 Web 主控。

## 核心闭环

```text
Amazon Ads 官方 MCP / Reports / Recommendations / Marketing Stream
                              |
                              v
Hermes Main（采集、解释、同步真实 Tool Schema）
                              |
                              v
确定性策略引擎（成熟度、阈值、目标 ACOS、冷却期）
                              |
                              v
原子决策任务 ──> Hermes Executor（仅计划内单实体写入）
                              |
                              v
Hermes Verifier（不同子代理、只读、重新查询 Amazon）
                              |
                              v
SQLite 审计 + 简明 Web + 异常与熔断
```

## 为什么不是黑盒

每个可执行变化都保存：

- 使用的数据窗口、数据成熟度和 KPI；
- 命中的确定性规则、证据、原因和计划值；
- Amazon MCP 的真实注册工具名、JSON Schema 与 Schema 哈希；
- Executor 会话、原子预约令牌、工具参数、结构化结果；
- 独立 Verifier 的重新查询结果、预期值、实际值和差异；
- 所有允许、阻断、失败、漂移、预算和资格异常。

LLM 可以解释和编排，但不能凭自然语言直接产生广告写入。

## 广告运营能力

当前确定性策略覆盖：

- 成熟窗口内零订单浪费目标的竞价下降；
- 超目标 ACOS 目标的受控竞价下降；
- 稳定低 ACOS 目标的小幅放量；
- 搜索词否定精准；
- 有订单且 ACOS 达标的搜索词精准收割；
- 预算接近耗尽且表现达标的 Campaign 预算提升；
- Top of Search 表现优秀时的 Placement 调整；
- Amazon 官方 Recommendations 的受控接收（默认不自动应用）；
- Marketing Stream 去重、预算接近耗尽和广告失去资格告警；
- Sponsored Products、Sponsored Brands、Sponsored Display、Sponsored TV 数据按 `ad_product` 保持区分。

策略输入必须来自 Amazon 实际数据。归因尚未成熟、数据过旧、窗口过短或关键字段缺失时只观察，不写入。

## Hermes 原生集成

插件使用 Hermes `v2026.7.7.2` / `hermes-agent==0.18.2` 已验证的接口：

- `register_tool`；
- `pre_llm_call`；
- `pre_tool_call`；
- `post_tool_call`；
- `subagent_start` / `subagent_stop`；
- namespaced Skill；
- `delegate_task` 创建真实 Executor 和 Verifier 子代理。

插件从 Hermes 实时工具注册表读取 `mcp_amazon_ads_*` 工具及 Schema，不维护猜测性的静态工具名白名单。工具新增、移除或 Schema 漂移会进入告警；未确认的漂移写工具 fail-closed。

## 官方能力对齐

项目的无凭据 CI 会读取 Amazon 官方 Advanced Tools Postman Collection，并检查以下能力仍存在：

- OAuth / Profiles / Manager Accounts；
- Sponsored Products v3；
- Sponsored Brands v4；
- Sponsored Display；
- Reporting；
- Marketing Stream；
- Recommendations；
- Budget；
- Test Accounts；
- Exports。

参考：

- https://github.com/amzn/ads-advanced-tools-docs/tree/main/postman
- https://advertising-ai.amazon.com/mcp
- https://advertising.amazon.com/about-api
- https://advertising.amazon.com/library/guides/amazon-marketing-stream

## 执行安全边界

以下规则无法从 Web 或普通设置关闭：

- 必须使用实时 MCP Catalog；
- Schema 漂移阻断写入；
- 所有广告写入必须来自确定性计划；
- Main 不能写，Verifier 不能写；
- Executor 只能执行绑定任务；
- 每次只允许一个广告实体；
- 决策必须原子预约，防止并发重复；
- 跨周期等价动作有冷却锁；
- Delete/Archive 与账户管理始终禁止；
- 任务完成前必须由不同 Verifier 独立读回。

其他默认限制：单次竞价 20%、预算 25%、每任务 50 次、每日 250 次、每日新 Campaign 2 个。Campaign 创建与官方 Recommendation 自动应用默认关闭。

## Web 主控

页面保持简单，只有五个区域：

1. **总览**：最新广告 KPI、周期、任务、Executor/Verifier；
2. **决策**：规则、实体、证据、计划和状态；
3. **变更与验证**：工具调用、结构化结果、独立读回差异；
4. **异常**：Schema 漂移、工具消失、预算、资格、执行和验证异常；
5. **Profiles / MCP**：Profile 策略、MCP 工具目录、模式和核心阈值。

运行模式：

- `OBSERVE`：默认；读取、规划和展示，不写；
- `AUTOPILOT`：Executor 在全部硬规则内自动执行；
- `PAUSED`：阻断所有 Amazon Ads MCP 读取、数据任务和写入。

## 安装

```bash
sudo install -d -o amazonbot -g amazonbot /opt/hermes-amazon-ads-automation
sudo -u amazonbot git clone <repo> /opt/hermes-amazon-ads-automation
cd /opt/hermes-amazon-ads-automation
sudo -u amazonbot bash scripts/install.sh

python3 scripts/control_cli.py generate-token
python3 scripts/control_cli.py hash-password
```

将结果写入 `/etc/hermes-amazon-ads-control.env`，参考 `deploy/control.env.example`：

```bash
sudo install -d -o amazonbot -g amazonbot /var/lib/hermes-amazon-ads-control
sudo cp deploy/amazon-ads-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now amazon-ads-control
```

合并 `config/hermes-autopilot.example.yaml` 到 Hermes 配置，启用插件并重启：

```bash
hermes plugins enable amazon-ads-control
hermes plugins list
hermes gateway restart
```

控制面只监听 `127.0.0.1:8790`。公网访问必须经过 HTTPS Nginx/Caddy；不要直接暴露 8790。

## 上线顺序

1. 保持 `OBSERVE`；
2. 完成 Amazon Ads MCP OAuth；
3. `hermes mcp test amazon-ads`；
4. 运行 Skill，同步真实 MCP Catalog；
5. 读取 Test Account 或真实账户，只做报告和规划；
6. 查看 Web 中工具、Schema、KPI、决策和告警；
7. 优先在 Amazon Ads Test Account 做写入/Verifier 验收；
8. 再切换 `AUTOPILOT`。

## 测试

```bash
bash scripts/validate.sh
```

本地验证包括单元、HTTP、并发、迁移、独立进程 E2E、策略、Schema、结果解析、Hermes 插件契约、Web 安全、Marketing Stream、Secret Scan。GitHub CI 额外安装固定 Hermes 正式包，真实加载插件，并在线检查 Amazon 官方 Postman Collection 和 MCP 端点的认证保护。

无法在无凭据沙箱中伪造的唯一部分是：你的 Amazon OAuth、Profile 可见性和真实 Test Account/生产广告写入。项目不会把 Mock 测试描述成真实 Amazon 成功。

## 目录

```text
control-plane/amazon_ads_control/   策略、事务、SQLite、HTTP 与 Web
hermes-plugin/amazon_ads_control/   Hermes 插件和 Skill
integrations/                       Marketing Stream 轻量接入
scripts/                            安装、验证、官方契约检查
config/ cron/ deploy/               Hermes、Cron、systemd、Nginx 示例
tests/                              单元、集成、进程和真实 Hermes smoke
```
