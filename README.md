# Hermes Amazon Ads Full Autopilot v3.2

面向单个 Amazon Ads 运营环境的 **Hermes 原生、确定性、审批门控、可恢复、可独立验证** 的广告全托管系统。

Amazon 官方 MCP 提供实时广告能力；本项目负责报告证据、策略、结构化运营计划、权限、原子执行、人工高风险审批、独立验证、恢复、审计和简明 Web 主控。项目不接管 Seller Central、收款、税务、申诉、Listing 或订单。

## 完整闭环

```text
Amazon Ads MCP / Reports / Recommendations / Marketing Stream
                              |
                              v
Hermes Main（读取、诊断、同步实时 Tool Schema）
             |                                |
             | 日常成熟决策                    | 高风险结构计划
             v                                v
确定性 Optimization Task             Payload-bound Approval Request
             |                                |
             |                         登录 Web 人工批准精确 Hash
             |                                |
             +--------------+-----------------+
                            v
Hermes Executor（当前任务、单实体、一次性预约、精确参数）
                            |
                            v
Amazon 返回真实对象 ID → 控制面确定性绑定 → 渲染后续已审批参数
                            |
                            v
不同 Hermes Session 的只读 Verifier 重新查询 Amazon
                            |
                            v
SQLite 审计 + Web + 告警 + 恢复 + 存储熔断
```

## 权限模型

### 日常全自动

数据成熟且满足确定性规则时，无需逐次询问：

- Bid 小幅升降；
- Budget 小幅升降；
- Placement 调整；
- 否定精准；
- 精准关键词收割；
- 经过明确规则限定的状态变化；
- 已有 Campaign 的日常优化。

### 人工批准后自动执行

Main 可以从实时 MCP Schema 构建完整方案并请求批准：

- Campaign 创建；
- Ad Group 创建；
- Keyword、Target、Product Ad 创建；
- Portfolio 和重要状态调整；
- 大幅或结构性预算变化；
- 可拆解、可逐项验证的市场扩展；
- 其他被实时 Catalog 判定为 high/critical、但影响范围明确的广告操作。

用户批准的是一份规范化计划 Hash，不是一句自然语言。批准绑定：Profile、工具名、每个参数、预期状态、依赖、预算暴露、有效期和决策集合。参数变化、过期、重复消费、依赖未成功或 Schema 漂移都会阻断。

### 永久禁止

- Billing、付款和发票；
- 用户、角色、权限、邀请和账户链接；
- Account/Profile 删除；
- 不可逆 Delete；
- 未知 MCP 语义；
- 未确认的实时 Schema 漂移；
- 无法拆解和独立验证的黑盒 Composite/Bulk Workflow。

Archive、重要暂停及其他可恢复的高风险广告操作可以进入人工审批，而不是一刀切永久禁止。

## Campaign 多步骤创建

一个 Campaign 计划可一次性人工批准，但始终按原子动作执行：

1. 创建 Campaign；
2. 从结构化成功结果提取唯一 Amazon Campaign ID；
3. 将该 ID 绑定到审批计划中的逻辑对象；
4. 确定性渲染已审批的 Ad Group 参数；
5. 创建并绑定 Ad Group ID；
6. 依次创建 Target、Keyword、Product Ad；
7. 任一前置动作不成功、结果不确定或无法提取唯一 ID，立即阻断后续动作；
8. Executor 完成后，由不同 Session 的 Verifier 对每个真实对象重新查询。

依赖模板使用：

```text
{{decision:<plan_key>.entity_id}}
```

模板、依赖和预期状态属于审批 Hash；运行时只能将已确认的真实 Amazon ID 填入该位置，不能更改其他参数。

## Hermes 原生集成

项目锁定并检查 `hermes-agent==0.18.2`（tag `v2026.7.7.2`）正式插件接口：

- `register_tool`：15 个 Amazon Ads 控制工具；
- `register_command`：`/ads-approvals`、`/ads-approve`、`/ads-reject`；
- `pre_llm_call` / `post_llm_call`；
- `pre_tool_call` / `post_tool_call`；
- `on_session_start` / `on_session_end` / `on_session_finalize` / `on_session_reset`；
- `subagent_start` / `subagent_stop`；
- namespaced Skill；
- `delegate_task` 的真实子 Session。

Main、Executor、Verifier 的权限不是 Prompt 约定：

- Main 没有写角色；
- Executor 必须由 `subagent_start` 绑定到精确任务，并带 `[ads-task:<id>] [ads-role:executor]`；
- Verifier 必须是不同 Session，并带 `[ads-role:verifier]`；
- Executor 进入验证阶段后不能重新绑定；
- 一个 Session 不能同时绑定不同任务或角色；
- Verifier 只能使用控制面记录的新 Amazon 只读 Action 作为证据。

Hermes 0.18.2 的全局 Delegation Model 不保证每个子代理使用不同模型，因此**不同 Session 是硬要求，不同模型是部署能力允许时的强化项**。Hermes 报告模型 fallback 时，控制面自动切换为 `OBSERVE` 并关闭写入。

## 人工审批交互

默认批准入口是登录后的控制 Web：

- Cookie Session；
- Origin 校验；
- CSRF；
- 精确确认短语；
- Payload Hash；
- 审批有效期；
- 每个决策一次性消费。

Hermes Slash Command 支持 CLI 和 Gateway，但命令批准默认关闭。只有在**没有 Terminal、File 和环境读取能力的受限 Gateway** 中，才应把独立的 `ADS_CONTROL_OPERATOR_TOKEN` 提供给 Hermes 并显式设置：

```bash
ADS_CONTROL_ENABLE_COMMAND_APPROVAL=true
```

一般部署中，Hermes 进程只获得 `ADS_CONTROL_AGENT_TOKEN`；`ADS_CONTROL_OPERATOR_TOKEN` 仅由控制面服务持有。AI 可以申请和解释审批，但不能调用批准接口。

## 实时 MCP 合约

插件直接从 Hermes 的 `mcp-amazon-ads` 注册表读取：

- 实际注册工具名；
- Native Name；
- JSON Schema；
- Enabled 状态。

控制面独立推导 read/job/write/unknown、Family 和 Risk。工具新增、移除、Schema、语义、Family 或 Risk 漂移都会记录；未知和未确认漂移 fail-closed。

CI 同时读取 Amazon 官方 Advanced Tools Postman Collection，规范化方法、路径、Header 和 Body 结构并固定语义指纹。Postman 是独立官方 REST 能力参考，运行时真实授权仍以 Hermes MCP Catalog 为准。

## 写入安全

- 每个 Amazon 写调用最多一个实体；
- 所有写入必须匹配唯一确定性 Decision；
- 日常可变字段写入前必须重新查询并执行 Compare-And-Set；
- 结构计划必须匹配审批后的规范化参数或确定性 ID 模板；
- SQLite `BEGIN IMMEDIATE` 原子预约；
- 任务、每日、Campaign 创建、预算暴露及冷却限制；
- 不确定 Amazon Mutation 永不盲目重放；
- 回调 Outbox 只重投结果信封，不重调 Amazon；
- 写后必须独立回读；
- Schema 漂移、控制面故障、Outbox 超限、存储硬压力和不可信模型 fallback 自动阻断写入。

## 运行资源

目标 2C2G Linux VPS 默认：

```yaml
delegation:
  max_concurrent_children: 1
  max_spawn_depth: 1
  orchestrator_enabled: false
```

Executor 与 Verifier 顺序运行。报告流式处理，单次只保留有限行和一个报告。资源压力可以推迟非紧急采集，但不能降低审批、CAS、限额、验证和恢复要求。

## 安装

```bash
sudo install -d -o amazonbot -g amazonbot /opt/hermes-amazon-ads-automation
sudo -u amazonbot git clone <repo> /opt/hermes-amazon-ads-automation
cd /opt/hermes-amazon-ads-automation
sudo -u amazonbot bash scripts/install.sh

python3 scripts/control_cli.py generate-token   # agent token
python3 scripts/control_cli.py generate-token   # optional operator token
python3 scripts/control_cli.py hash-password
```

根据 `deploy/control.env.example` 写入控制面环境，启用 systemd，然后合并 `config/hermes-amazon-chain.example.yaml`：

```bash
hermes plugins enable amazon-ads-control
hermes plugins list
hermes gateway restart
```

控制面只监听 `127.0.0.1:8790`。远程访问必须经过 HTTPS Nginx/Caddy，不得直接暴露 8790。

## 上线顺序

1. 保持 `OBSERVE`；
2. 完成 Amazon Ads MCP OAuth；
3. 验证 authenticated `initialize` / `tools/list`；
4. 同步真实 Catalog，确认必要工具没有 unknown/drifted；
5. 完成真实报告提交、轮询、下载、解压和归一化；
6. 使用真实历史数据运行至少一个完整归因窗口；
7. 在 Test Account 或低风险 Profile 创建一个小预算 Campaign 计划；
8. 在 Web 核对并批准精确 Hash；
9. 验证 Campaign → Ad Group → Target/Keyword → Ad 的真实 ID 绑定；
10. 由不同 Verifier Session 完成全部 Amazon 回读；
11. 完成 429、OAuth 刷新、重启、Outbox、备份恢复和存储压力演练；
12. 再逐步扩大 `AUTOPILOT` 范围。

## 验证

```bash
bash scripts/validate.sh
bash scripts/coverage.sh
bash scripts/validate_deploy.sh
PYTHONPATH="$PWD/control-plane:$PWD/hermes-plugin:$PWD/tests" python3 tests/stress_recovery.py
```

无凭据套件覆盖策略、权限、审批 Hash、AI 不能自批、过期、重复消费、多步骤真实 ID 绑定、参数篡改、独立验证、回调、Outbox、SQLite、浏览器、部署、真实 Hermes 插件加载和 Amazon 官方合同。

静态、Mock、历史回放和端点可达性不能证明广告增量效果，也不能替代所有者账户中的 OAuth、真实 MCP、Test Account/Canary 和归因窗口验收。详见 `docs/PRODUCTION_ACCEPTANCE.md`。
