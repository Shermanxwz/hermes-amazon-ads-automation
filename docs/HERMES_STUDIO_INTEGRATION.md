# Hermes Studio Integration Contract

Hermes Studio is the owner's primary chat/Web surface. It does **not** replace the Amazon Ads Control trust boundary. Studio chat, scheduled runs and CLI one-shots must select the same Hermes Profile that has `amazon-ads-control` enabled.

## Required runtime identity

Use one server-side Hermes **base home + Profile** pair for all entry points:

- Hermes Studio Agent Bridge;
- interactive/CLI Hermes sessions;
- `hermes-amazon-ads-us-orchestrator.service` scheduled one-shot.

Recommended deployment variables are in `deploy/control.env.example`:

```text
HERMES_HOME=/var/lib/hermes-studio/.hermes
HERMES_PROFILE=default
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes
```

`HERMES_HOME` should normally be the Hermes **base home**. Hermes Studio resolves Profiles as:

```text
default      -> <base>/
named profile -> <base>/profiles/<name>/
```

The installer and validator also accept an already-resolved named Profile home, but normalize CLI execution back to the base home so Studio, Hermes and the orchestrator cannot silently diverge.

## Plugin installation

Default Profile:

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=default \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
  bash scripts/install.sh
```

Named Profile:

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=amazon-ads \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
  bash scripts/install.sh
```

For the named example the plugin is linked under:

```text
/var/lib/hermes-studio/.hermes/profiles/amazon-ads/plugins/amazon-ads-control
```

The installer explicitly enables `amazon-ads-control` with Hermes' PluginManager. Plugin discovery alone is not sufficient because user plugins are opt-in. Restart the Hermes Studio/Hermes runtime after installation so long-lived Agent Bridge sessions reload the plugin.

## Studio integration depth

The integration is intentionally deeper than “the CLI can see a plugin”:

1. Hermes Studio resolves the selected Profile home.
2. Studio's PluginManager discovers `amazon-ads-control` from that Profile.
3. Studio chat runs through its Agent Bridge and the `/api/chat-run/runs` HTTP bridge.
4. Hermes executes the `amazon-ads-control` tools/hooks inside the selected Profile.
5. The plugin talks only to the local Amazon Ads Control service using the machine token.
6. The controller applies the sealed policy, budget reservations, CAS/write boundary and independent-verifier requirements.
7. Amazon MCP/Direct API transports remain below that controller boundary.

Studio therefore does not receive a parallel or privileged mutation path. A chat request and an unattended scheduled request converge on the same control plane and the same safety envelope.

## Upstream Hermes Studio contract gate

CI runs:

```bash
python scripts/check_hermes_studio_contract.py --check
```

The checker follows the current `EKKOLearnAI/hermes-studio` `main` branch and fails closed if the integration semantics used by this project disappear or materially move, including:

- default/named Profile directory resolution;
- selected-Profile `HERMES_HOME` propagation;
- user-plugin discovery through Hermes `PluginManager`;
- plugin list/enable routes;
- `/api/chat-run/runs`;
- Profile propagation into the chat socket;
- bearer-token socket auth;
- tool execution events.

This is a semantic compatibility gate rather than an exact upstream file-hash pin, so harmless source edits do not break the build while structural integration drift does.

## Credential boundary

Keep these values server-side only:

- `ADS_CONTROL_AGENT_TOKEN`;
- Amazon OAuth client secret and refresh/access tokens;
- Hermes provider credentials;
- Hermes Studio authentication/JWT secrets.

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

Before an exposure-increasing action Hermes must perform a fresh Amazon Campaign read for the exact enabled Profile. The controller, not the model, decides whether the projected exposure fits the hard cap and exploration pool.

Weak historical evidence may justify a small `HERMES-SP-EXP-*` experiment. It does not authorize bypassing current-state reads, schema validation, atomic execution, independent verification, reversible creation/activation sequencing or the hard budget ceiling.

## Acceptance

Static/Profile check:

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=default \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
  bash scripts/validate_hermes_studio.sh
```

Credentialed Hermes runtime acceptance, using one real model turn and the live local control plane:

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=default \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
  bash scripts/validate_hermes_studio.sh --live
```

Full deployed **Hermes Studio Web -> HTTP chat-run -> Agent Bridge -> plugin -> local controller** acceptance:

```bash
HERMES_HOME=/var/lib/hermes-studio/.hermes \
HERMES_PROFILE=default \
HERMES_BIN=/opt/hermes-agent/venv/bin/hermes \
HERMES_STUDIO_URL=https://YOUR_STUDIO_ORIGIN \
HERMES_STUDIO_AUTH_TOKEN='SERVER_SIDE_TOKEN' \
  bash scripts/validate_hermes_studio.sh --studio-live
```

`--studio-live` never prints the Studio token. It requires the HTTP bridge to complete a real `ads_control_status` tool call and return tool execution evidence.

The repository CI separately loads the plugin through real Hermes PluginManager versions, validates named-Profile layout, and checks the current Hermes Studio upstream contract. Production acceptance additionally requires the deployed Studio Profile, local control service, real model/provider credentials, live Amazon MCP/OAuth and target VPS to pass together.

## File ownership

`hermes-amazon-ads-us-orchestrator.service` runs as the dedicated `amazonbot` user, not root. Ensure the selected Hermes base/Profile home and plugin path are readable by that user and that only the minimum runtime directories are writable. Do not solve permission problems by returning the service to root.
