# M7.3 Third-Party MCP Operations Runbook

The manager is for explicit, credential-free, no-side-effect local stdio MCP
testing.  It does not scan or replace the global Codex configuration and does
not wrap Workshop itself.

To inspect existing Codex registrations without exposing environment values or
HTTP headers:

```powershell
$env:PYTHONPATH = "servers"
python -m shutu_workshop --workspace .test-m73\workspace mcp host-list
```

Choose one returned stdio command explicitly, then register that exact command
in an M7.3-owned project manifest with `mcp enable`.  Streamable HTTP entries
remain visible as metadata but are not wrapped by this path.

## List and inspect

```powershell
$env:PYTHONPATH = "servers"
python -m shutu_workshop --workspace .test-m73\workspace mcp list
python -m shutu_workshop --workspace .test-m73\workspace mcp inspect fixture
```

## Enable and verify

```powershell
python -m shutu_workshop --workspace .test-m73\workspace mcp enable fixture `
  --command python tests/fixtures/mcp_test_server.py --scope project
python -m shutu_workshop --workspace .test-m73\workspace mcp verify fixture
```

The manifest stores the original command, arguments, cwd and explicit safe
environment values plus an identity hash.  Secret-like environment keys and
command arguments are rejected rather than persisted.  A reserved Workshop
name, recursive Observer command, non-stdio transport, or non-identical
existing manifest is rejected.

## Real local probe

The probe below uses the existing transparent Observer and a local fixture
only.  It forwards initialize/tools-list/tools-call bytes, records a TraceStore
trace and reports failed observations without replaying the call.

```powershell
python -m shutu_workshop --workspace .test-m73\workspace mcp probe fixture `
  --messages .test-m73\messages.json --timeout 30
```

`mcp_test_server.py` has only echo/add/error fixture tools.  Do not point this
command at production messaging, SSH, database-write or deletion services.

## Disable and rollback

```powershell
python -m shutu_workshop --workspace .test-m73\workspace mcp disable fixture
python -m shutu_workshop --workspace .test-m73\workspace mcp verify fixture
```

Disable changes only the owned manifest to `disabled`; it does not remove
another server and it does not edit user `config.toml`.  For a real Codex
registration, use an isolated `CODEX_HOME` and the existing M7.1/M7.2 scripts;
keep the normal daily Compat registration unchanged.

Unsupported boundaries: Streamable HTTP, secret-bearing registrations,
recursive wrapping and host surfaces without transparent evidence remain
`UNSUPPORTED`, `BLOCKED_ENV` or `NOT_VERIFIED`.
