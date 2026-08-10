# Hermes Studio Integration Contract

Hermes Studio is the owner's chat/Web surface. It does **not** replace the Amazon Ads Control trust boundary. Studio chat, scheduled runs and CLI one-shots must select the same Hermes Profile that has `amazon-ads-control` enabled.

## Required runtime identity

Use one server-side Hermes home/profile pair for all three entry points:

- Hermes Studio agent bridge;
- interactive/CLI Hermes sessions;
- `hermes-amazon-ads-us-orchestrator.service` scheduled one-shot.

Recommended deployment variables are in `deploy/control.env.example`:

```text
HERMES_HOME=/var/lib/hermes-studio/.hermes
HERMES_PROFILE=default
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes
```

The exact Profile name may differ. What matters is that Studio and the scheduled service select the **same** Profile home/configuration.

## Plugin installation

Run:

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=default \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
  bash scripts/install.sh
```

This links the plugin below the selected Hermes home and explicitly enables `amazon-ads-control` with Hermes' plugin manager. Plugin discovery alone is not sufficient because user plugins are opt-in.

After installation restart the Hermes Studio/Hermes runtime so long-lived sessions reload the plugin.

## Credential boundary

Keep these values server-side only:

- `ADS_CONTROL_AGENT_TOKEN`;
- Amazon OAuth client secret and refresh/access tokens;
- Hermes provider credentials;
- Hermes Studio auth/JWT secrets.

Never inject them into browser JavaScript, public HTML, repository files, screenshots or public logs. The Amazon Ads Control service remains loopback/private behind the authenticated reverse proxy.

## Studio-facing behavior

The owner may use normal Chinese requests in Studio, for example:

- 调整目标 ACOS；
- 修改每日总预算硬上限；
- 修改 AI 探索预算比例；
- 解释今天为什么扩量或否定；
- 暂停/恢复全托管；
- 总结今天的实验和独立验证结果。

Routine Sponsored Products work should not ask the owner to understand Executor/Verifier sessions, CAS tokens, payload hashes or internal task states. Those remain controller/runtime implementation details.

## Budget-bounded exploration

The Web owner surface stores:

- target ACOS;
- account daily budget exposure hard cap;
- exploration share;
- per-Campaign daily budget limit.

Before an exposure-increasing action Hermes must perform a fresh Amazon Campaign read for the exact enabled Profile. The controller, not the model, decides whether the projected Campaign-budget exposure fits the hard cap and exploration pool.

Weak historical evidence may justify a small `HERMES-SP-EXP-*` experiment. It does not authorize bypassing current-state reads, schema validation, atomic execution, independent verification, reversible creation/activation sequencing or the hard budget ceiling.

## Acceptance

Static/profile check:

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=default \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
  bash scripts/validate_hermes_studio.sh
```

Credentialed end-to-end acceptance (uses one real Hermes model turn and the live local control plane):

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=default \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
  bash scripts/validate_hermes_studio.sh --live
```

The repository CI separately loads the plugin through real Hermes PluginManager versions. Production acceptance additionally requires the deployed Hermes Studio profile, local control service, live Amazon MCP/OAuth and target VPS to pass together.

## File ownership

`hermes-amazon-ads-us-orchestrator.service` runs as the dedicated `amazonbot` user, not root. Ensure the selected Hermes home and plugin path are readable by that user and that only the minimum runtime directories are writable. Do not solve permission problems by returning the service to root.
