# Hermes Amazon Ads Autopilot

一个为 **Hermes Agent** 原生设计的 Amazon Ads 主控/Worker 自动运营项目：主控负责认知、拆解、复核；Worker 执行被明确绑定的任务；所有工具调用、写入、阻断、子代理和结果进入可查看的只读审计 Web 端。

## 成品能力

- **Hermes 原生插件**：工具、`pre_llm_call`、`pre_tool_call`、`post_tool_call`、`subagent_start/stop` 全部通过官方插件接口接入。
- **硬性主控/Worker 分工**：主控可读、分析、建任务和委派；Amazon Ads 写工具仅允许绑定到任务的 Hermes Worker 调用。
- **无审批全托管**：`autopilot` 模式下，Worker 在硬性 guardrails 内直接执行，无需人工逐项批准。
- **非黑盒**：SQLite 保存任务、Worker、每次工具调用、允许/拦截原因、执行结果和事件；Web 端只读展示全部操作链。
- **简单管理**：Web 端仅保留 `AUTOPILOT / OBSERVE / PAUSED`、Worker 写入总开关和状态查看。
- **轻量部署**：控制面仅用 Python 标准库 + SQLite，适合 2C2G VPS；Hermes 和 Amazon Ads 官方 MCP 仍是实际智能与广告能力层。
- **故障安全**：控制面不可达、操作语义未知、主控直接写、任务未授权、超出竞价/预算变化或操作次数限制时全部 fail-closed。

## 工作流

```text
用户 / 每日 Hermes Cron
          |
          v
Hermes Main Controller
  - 读取 Amazon Ads MCP
  - 分析与计算
  - ads_control_create_task
  - delegate_task(goal 包含 [ads-task:<id>])
          |
          v
Hermes Worker / subagent
  - 插件 subagent_start 绑定任务
  - pre_tool_call 校验角色、任务、模式、guardrails
  - 调用 Amazon Ads MCP 写工具
  - 读回验证
          |
          v
SQLite 审计 + Web Dashboard
```

## 快速安装

```bash
sudo install -d -o amazonbot -g amazonbot /opt/hermes-amazon-ads-automation
sudo -u amazonbot git clone <this-repo> /opt/hermes-amazon-ads-automation
cd /opt/hermes-amazon-ads-automation
sudo -u amazonbot bash scripts/install.sh

python3 scripts/control_cli.py generate-token
python3 scripts/control_cli.py hash-password
```

把结果写入 `/etc/hermes-amazon-ads-control.env`，参考 `deploy/control.env.example`。数据库目录：

```bash
sudo install -d -o amazonbot -g amazonbot /var/lib/hermes-amazon-ads-control
sudo cp deploy/amazon-ads-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now amazon-ads-control
```

Hermes 插件由 `scripts/install.sh` 链接到：

```text
~/.hermes/plugins/amazon_ads_control
```

重启 Hermes：

```bash
hermes plugins enable amazon-ads-control
hermes plugins list
hermes gateway restart
```

然后合并 `config/hermes-autopilot.example.yaml`，确认 Amazon Ads MCP OAuth 已可用，并通过 `hermes tools` 为日任务启用：

```text
mcp-amazon-ads
amazon-ads-control
delegation
```

## 创建每日全托管任务

推荐让 Hermes 自己创建，以免依赖内部 JSON 版本：

```bash
hermes cron create "30 17 * * *" \
  "执行 Amazon Ads 每日全托管循环；控制面不可达或不是 autopilot 时只读。" \
  --skill amazon-ads-control:amazon-ads-autopilot
```

完整提示参考 `cron/amazon-ads-autopilot.example.json`。

## Web 端

控制面只监听 `127.0.0.1:8790`。建议使用独立 HTTPS 子域名经 Nginx/Caddy 反向代理，示例在 `deploy/nginx.conf`。不要把 8790 直接暴露公网。

页面提供：

- 当前运行模式和写入开关
- 今日任务、写入、拦截数量
- 主控创建的任务及状态
- 活跃与历史 Hermes Workers
- 每次工具调用和执行结果
- 系统事件和异常

日志/操作记录没有删除、修改或审批按钮。管理功能只有暂停/观察/自动执行和总开关。

## 默认 guardrails

| 项目 | 默认值 |
|---|---:|
| 单次竞价变化 | 15% |
| 单次预算变化 | 20% |
| 每任务允许写操作 | 50 |
| 每日允许写操作 | 250 |
| delete/archive | 禁止 |
| 未知 Amazon Ads 工具 | 拦截 |
| 主控直接写 | 拦截 |

可在 SQLite settings 或 Web/API 扩展这些限制；当前 Web 为避免误操作，只暴露模式与总开关。

## 验证

```bash
bash scripts/validate.sh
```

验证包括：Python 语法、密码/session、策略分类与脱敏、主控写拦截、Worker 任务绑定、observe 模式、任务完成、HTTP 登录/CSRF/agent token、独立进程端到端链路、Hermes 插件真实 handler 契约、静态 Dashboard 和 secret scan。

## 项目结构

```text
control-plane/amazon_ads_control/   Python/SQLite 控制面与 Web
hermes-plugin/amazon_ads_control/   Hermes 原生插件和 autopilot skill
config/                             Hermes 配置参考
cron/                               日循环参考
scripts/                            安装、验证、凭据生成
deploy/                             systemd、Nginx、环境文件
tests/                              单元与 HTTP 集成测试
```

## 范围与真实限制

本项目不复制 Amazon 的远程 MCP，也不伪造 Ads 数据。最终可执行能力取决于：Amazon Ads MCP 实际暴露的工具、OAuth 权限、账户/profile 可见性和 Hermes 当前版本。工具名称含义不明确时会被拦截，而不是猜测。

Seller Central 网页登录应继续使用同一 VPS 上固定浏览器 Profile 进行人工异常处理；本控制面不读取 Cookie、不自动处理验证码、账户健康、收款、税务或申诉。

详见 `SECURITY.md` 与 `docs/ARCHITECTURE.md`。
