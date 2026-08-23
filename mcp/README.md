# MCP servers in this bundle

Two Python servers, chosen to show the two shapes you will build for real: an
always-on server with no credentials, and a credential-gated server that stays
dark until you supply a token.

| Server | Gate | Tools | Env it reads |
| --- | --- | --- | --- |
| `runbook_library` | `always: true` | `rb_search`, `rb_get_runbook`, `rb_list_categories` | `RUNBOOKS_PATH` (optional) |
| `release_tracker` | `enabled_env: [DEPLOY_API_TOKEN]` | `rt_list_releases`, `rt_get_release`, `rt_open_change_request` | `DEPLOY_API_TOKEN`, `DEPLOY_API_BASE_URL`, `DEPLOY_API_TIMEOUT_SECONDS`, `DEPLOY_API_OUTPUT_DIR` |

`manifest.yaml` is the source of truth for both rows. This table is a snapshot.

## Where these actually run

**In the fleet process — never in a sandbox.** They are stdio subprocesses the
fleet process spawns, because that is where brokered credentials live.

On the single-box podman install that is a process on the VM. **On the
Kubernetes path it is a process inside the control-plane pod**, and three
consequences follow:

1. **The control-plane image needs `python3` and this directory's runtime
   dependencies.** `deploy/kubernetes/Containerfile.control-plane` installs
   `requirements.txt` for exactly this reason. An image built from fleet's
   generic example Containerfile boots, serves chat, and reports both servers
   dead — a per-server error in the log, not a boot failure.
2. **A new runtime dependency goes in `requirements.txt` and nowhere else.**
   The Containerfile installs from that file, so no image edit is needed. Test
   and lint tooling goes in `requirements-dev.txt`, which the image
   deliberately does not install.
3. **Never write output to a home-relative path.** The control-plane pod's
   `$HOME` is ephemeral and invisible to sandbox pods, so a receipt written to
   `~/something` is an artifact no agent can read. Map an output directory to
   `${FLEET_WORKSPACE}/…` in the manifest — the workspace claim is the one
   filesystem the control plane and every sandbox pod share, at the same
   absolute path. `release_tracker` shows the pattern, including treating the
   variable as optional because fleet drops it on a spawn path with no
   workspace.

Nothing here reaches a sandbox pod: those get no env, no secret mounts, and no
service-account token.

## Develop and test

```sh
make venv     # from the repo root
make test
make lint
```

or directly:

```sh
.venv/bin/python -m pytest mcp/ -m 'not expensive' -q
.venv/bin/python -m ruff check mcp/ && .venv/bin/python -m ruff format --check mcp/
```

Run both from the **repo root** so `pytest.ini` and `ruff.toml` are discovered.
`mcp/tests/conftest.py` puts this directory on `sys.path`, so tests import
servers by bare name (`import runbook_library`).

The `expensive` marker gates tests that spend real API money. Run those by hand
with `-m expensive`, never in a batch.

`mcp/tests/test_deployment_contract.py` is not a server test — it is the
contract between `manifest.yaml`, these servers, and `deploy/kubernetes/`. It
is what catches a tool renamed in code but still allowlisted in the manifest,
a directory the agent reads that no Containerfile bakes, and
`bundle_docs_in_image` set in one of its two homes and not the other. Read its
docstring before you change any of those.

## Speaking MCP to a server directly

Initialize plus `tools/list` over stdio. A real response means it starts and
exposes tools:

```sh
printf '%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | .venv/bin/python mcp/runbook_library.py | head -c 2000
```

Once fleet is running, the better check is fleet's own — it spawns each server
with the boot loader's exact env and gates:

```sh
fleet mcp test --all --deep      # locally
kubectl -n larkspur exec deploy/larkspur -- fleet mcp test --all --deep   # on a cluster
```

`--deep` additionally runs each server's manifest-declared `probe:`.
`runbook_library` declares `rb_list_categories` as its canary: zero arguments,
zero side effects, and it fails only if the runbook file did not load.

## Writing a new server

1. Write `mcp/<name>.py` using `mcp.server.fastmcp.FastMCP`. Log to **stderr** —
   stdout is the transport.
2. Read plain env vars. Stay customer-agnostic: no company name, no account id,
   no hostname in the code.
3. Add an entry to `mcp_servers[]` in `manifest.yaml` with the right gate
   (`always: true`, or `enabled_env` for a credential-gated connector) and the
   env map. **The manifest env map is the whole contract:** fleet spawns stdio
   servers with a minimal environment (PATH/HOME/locale plus exactly that map),
   so a key you do not map never arrives, no matter what the operator exported.
4. List read-only tools in `agent_policy.parallel_safe_tools` and every write in
   `critical_tools` (by bare name suffix) so it requires an audit gate.
5. Add a `probe:` if the server has a zero-side-effect canary call.
6. Add runtime dependencies to `requirements.txt`; test-only ones to
   `requirements-dev.txt`.
7. Write tests. `respx` is available for mocking `httpx`.
8. Run `make test` — the contract tests will tell you what you forgot.

### Degrade gracefully, always

Every optional env key should behave sensibly when it is missing, because on a
cluster "missing" is a normal state rather than a misconfiguration: `optional_env`
drops empty values so the server sees a clean unset, and `${FLEET_WORKSPACE}`
is dropped entirely on spawn paths that have no workspace to offer. A server
that raises on an unset optional variable turns a normal condition into a dead
connector.

### Never print a credential

Not in a log line, not in an error message, not in a receipt. `release_tracker`
builds its error dictionaries by hand for exactly this reason — a
`repr(headers)` in an exception handler is how a token reaches a log.
