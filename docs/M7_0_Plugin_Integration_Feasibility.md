# M7.0 Codex Native Plugin Integration Feasibility

## Decision

Use the portable Agent Plugins package as the canonical artifact:

```text
plugins/shutu-skill-workshop/
  plugin.json       portable identity + OpenAI extension metadata
  mcp.json          local stdio server mapping
  skills/           explicit review workflow
  hooks/            opt-in, trusted, summary-only observation
  bin/run_mcp.py    cache-safe launcher with a read-only fallback
```

The Workshop MCP layer is a thin read-only adapter over the existing Core
(`M1Database`, `TraceStore`, `DiagnosisService`, capability registry,
evaluation and release readers). It does not create a second ledger or start
another Codex process.

## Official format versus target host

The decision was checked against the current OpenAI documentation on [plugin
packaging](https://developers.openai.com/plugins/build/plugins), [Codex
Hooks](https://developers.openai.com/codex/hooks), [MCP servers](https://developers.openai.com/plugins/build/mcp-server), and
[desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only).
The current target reports `codex-cli 0.163.0`.

| Surface | Result | Evidence and boundary |
| --- | --- | --- |
| Portable `plugin.json` | `SUPPORTED` | Root manifest uses Agent Plugins schema and `extensions.com.openai`. Package verification is PASS. |
| Root `mcp.json` | `SUPPORTED` | `type=stdio`, `${PLUGIN_ROOT}` launcher, no example endpoint. |
| Repo Marketplace | `HOST_VERIFIED` | `codex plugin marketplace add <repo>` and isolated `CODEX_HOME` discovery succeeded. |
| CLI plugin install/list/remove | `HOST_VERIFIED` | Real `plugin add`, `plugin list --json`, `plugin remove` completed in `.test-m7`. |
| CLI `codex exec` plugin MCP | `BLOCKED_ENV` | With an authenticated isolated Home, the model reported `workshop_health` was not in configured tools. This is not a tool-call PASS. |
| CLI plugin Skill | `BLOCKED_ENV` | The probe did not observe a Skill invocation; one attempt to read the installed Skill was rejected by the host policy. |
| Plugin Hook trust/capture | `BLOCKED_ENV` | Package Hook unit tests pass, but no trusted normal plugin session produced a real Hook event. |
| Codex desktop install/use | `UNVERIFIED` | Desktop UI was not available for this run. The official desktop-only path remains the next host check. |
| Windows path handling | `UNIT_TESTED` | Package files, a Chinese/space-containing workspace, and a space-containing `PLUGIN_DATA` path passed local tests. |

Important feature boundary: the CLI has marketplace management commands, but
that alone does not prove the local plugin runtime is loaded into `codex exec`.
The official desktop-only guidance also says to install and test local stdio
plugins in the desktop app. M7 therefore stays PARTIAL until a normal trusted
Codex host session observes MCP, Skill, and Hook activity.

## Safety and compatibility decisions

- Only read tools are bundled. Proposal, evaluation, release, rollback, and
  configuration edits remain explicit Core workflows.
- Hook records contain bounded event summaries, hashes, argument-key names, and
  status. They do not persist prompts, tokens, credentials, complete tool
  arguments, or complete tool results.
- `PLUGIN_ROOT` is treated as read-only. Hook state is written under
  `PLUGIN_DATA`; the MCP server reads the selected workspace database.
- Hook failure, malformed input, retention overflow, and unavailable data are
  fail-open and visible as `HOOKS_UNTRUSTED`, `NOT_ACTIVE`, `dropped`, or
  `unknown`; they are never upgraded to success.
- The local fallback in `bin/run_mcp.py` is read-only SQLite access for a
  cache-installed package when the repository source is not beside the cache;
  it is not a new store or a write path.
