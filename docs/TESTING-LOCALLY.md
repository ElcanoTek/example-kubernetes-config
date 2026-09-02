# Testing this bundle locally, without a cluster

Everything in this repo except the deployment is just a bundle, and a bundle can
be exercised on a laptop. This page is how: run the two MCP servers, put Rowan
and a system prompt in front of a local coding agent, and drive
`ask-the-runbooks` end to end — **no Kubernetes, no Helm, no images, no fleet
binary required**.

Do this before you build anything. A protocol that does not work in a coding
agent will not start working because you put it in a pod, and a broken
`manifest.yaml` is much cheaper to find here than in a control-plane image.

> **What this path cannot test — the whole point of stating it up front.** Local
> testing covers the bundle's *content*: servers, tools, personas, prompts,
> protocols, skills. It covers **none** of what makes the cluster path
> different, and there are exactly two things only a cluster tests:
>
> 1. **In-sandbox bundle reads — the bake-and-declare pair.** Whether
>    `view_file protocols/…` works inside a sandbox depends on
>    `deploy/kubernetes/Containerfile.sandbox` baking the doc dirs **and** the
>    deployment declaring `bundleDocsInImage`. Locally there is no sandbox and no
>    image, so both halves are unexercised: your agent reads `protocols/` off the
>    filesystem and always succeeds. Bake without declaring → `view_file`
>    refused, bash still works. Declare without baking → reads fail not-found.
>    Neither failure can appear here.
> 2. **The sealed-egress NetworkPolicy.** Under podman a sealed sandbox is a
>    kernel namespace with no interface; on a cluster it is a pod label matched
>    by a NetworkPolicy that only your CNI enforces. fleet can verify the policy
>    *object* exists and nothing more. A laptop tells you nothing about it.
>
> Both are tested on a real cluster in
> [`KUBERNETES-GETTING-STARTED.md`](KUBERNETES-GETTING-STARTED.md) — §8c for the
> in-sandbox read, §8d for the seal — and rehearsable on kind with the caveats in
> [`LOCAL-CLUSTER-KIND.md`](LOCAL-CLUSTER-KIND.md) (kindnet does not enforce
> NetworkPolicy, so §8d is the one thing kind cannot rehearse either).

Throughout, `<bundle>` is the absolute path to your checkout (e.g.
`/home/you/example-kubernetes-config`) — most agent config files do not expand
`~`.

---

## Contents

1. [One-time setup](#one-time-setup)
2. [Part 1 — the MCP servers](#part-1--the-mcp-servers)
3. [Part 2 — persona and system prompt](#part-2--persona-and-system-prompt)
4. [Part 3 — run a protocol end to end](#part-3--run-a-protocol-end-to-end)
5. [Part 4 — skills](#part-4--skills)
6. [Part 5 — the Agent Plugin](#part-5--the-agent-plugin)
7. [Optional — waking `release_tracker`](#optional--waking-release_tracker)
8. [If you do have a fleet binary](#if-you-do-have-a-fleet-binary)
9. [What you have and have not proved](#what-you-have-and-have-not-proved)

---

## One-time setup

```sh
cd <bundle>
make venv        # .venv + mcp/requirements.txt + mcp/requirements-dev.txt
make test        # the servers and the manifest/deployment contract tests
make lint
```

`make test` is worth running first: `mcp/tests/test_deployment_contract.py`
checks the manifest against the servers and against both Containerfiles without
needing a cluster, so it catches a renamed tool or a missing `COPY` line before
anything else you do here.

**Point fleet's bundle variable at the checkout.** Nothing on this page strictly
requires it — a coding agent reads the files by path — but set it anyway, in the
shell you run everything from, so that any `fleet` verb you reach for later
resolves this bundle and not a stale one:

```sh
export FLEET_CLIENT_CONFIG_DIR=<bundle>
export PERSONA_DEFAULT=assistant      # personas/assistant.yaml — "Rowan"
```

**There is no `install.sh` in this repo, on purpose.** Registering MCP servers
into a local coding agent is a laptop workflow, and the podman-shaped sibling
[example-config](https://github.com/ElcanoTek/example-config) owns it —
its installer reads `manifest.yaml`, so it works unchanged against this bundle
if you would rather not wire agents up by hand. The snippets below are the
manual path.

---

## Part 1 — the MCP servers

Two servers, and they behave differently on purpose:
[`runbook_library`](../mcp/runbook_library.py) is always on and needs no
credentials; [`release_tracker`](../mcp/release_tracker.py) stays dark until
`DEPLOY_API_TOKEN` is set. **Start with `runbook_library`** — every step below
works with no secrets at all.

### Prove the server starts, without any agent

Speak MCP to it over stdio. Initialize, send the `initialized` notification,
then ask for its tools:

```sh
cd <bundle>
{ printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'; sleep 1; } \
  | .venv/bin/python mcp/runbook_library.py 2>/dev/null | sed -n '2p' \
  | python3 -c "import sys,json; print([t['name'] for t in json.load(sys.stdin)['result']['tools']])"
```

```
['rb_list_categories', 'rb_search', 'rb_get_runbook']
```

The `sleep 1` before EOF is not decoration: without it the server can see stdin
close and exit before it has written its reply, and you get a truncated stream
that looks like a broken server. (`mcp/README.md` shows a shorter two-line form
for a quick eyeball; add the sleep whenever you want a parseable answer.)

You can call a tool over the same transport — no agent involved. This is the
fastest way to prove the runbook JSON actually loaded:

```sh
{ printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"rb_search","arguments":{"query":"rotate a service credential","limit":3}}}'; sleep 1; } \
  | .venv/bin/python mcp/runbook_library.py 2>/dev/null | sed -n '2p' \
  | python3 -c "import sys,json; [print(r['id'], r['score'], r['title']) for r in json.load(sys.stdin)['result']['structuredContent']['result']]"
```

```
rb-002 68 Rotating a service credential
rb-001 54 Rolling out a service change
rb-003 49 Declaring and running an incident
```

`rb-002` on top is the ranker working: a title hit is scored above a body hit.

The zero-argument `rb_list_categories` call is the same one `manifest.yaml`
declares as this server's `probe:` — the canary `fleet mcp test --deep` runs —
and its first text block is what the manifest's `contains: "category"` asserts
on.

### Register them in your agent

Use `<bundle>/.venv/bin/python` rather than bare `python3`. The servers need
their dependencies importable, and the interpreter your agent finds on `PATH`
is a property of how you launched it — naming the venv interpreter removes the
variable entirely.

Only `release_tracker` takes credentials, and every snippet passes them as
**variable references**, never values: your agent expands them from the shell it
was launched in.

#### Claude Code

```sh
cd <bundle>

# always-on, no credentials — the right first target
claude mcp add runbook_library -s user \
  -- <bundle>/.venv/bin/python <bundle>/mcp/runbook_library.py

# credential-gated: names, not values
claude mcp add release_tracker -s user \
  -e DEPLOY_API_TOKEN='${DEPLOY_API_TOKEN}' \
  -e DEPLOY_API_BASE_URL='${DEPLOY_API_BASE_URL}' \
  -e DEPLOY_API_TIMEOUT_SECONDS='${DEPLOY_API_TIMEOUT_SECONDS}' \
  -- <bundle>/.venv/bin/python <bundle>/mcp/release_tracker.py
```

Scopes: `-s local` (this project, private), `-s project` (writes a shared
`.mcp.json`), `-s user` (all your projects — convenient for testing). The
equivalent project `.mcp.json`, which supports `${VAR}` / `${VAR:-default}`
expansion in `command`, `args`, `env`, `url` and `headers`:

```json
{
  "mcpServers": {
    "runbook_library": {
      "type": "stdio",
      "command": "<bundle>/.venv/bin/python",
      "args": ["<bundle>/mcp/runbook_library.py"]
    },
    "release_tracker": {
      "type": "stdio",
      "command": "<bundle>/.venv/bin/python",
      "args": ["<bundle>/mcp/release_tracker.py"],
      "env": {
        "DEPLOY_API_TOKEN": "${DEPLOY_API_TOKEN}",
        "DEPLOY_API_BASE_URL": "${DEPLOY_API_BASE_URL}",
        "DEPLOY_API_TIMEOUT_SECONDS": "${DEPLOY_API_TIMEOUT_SECONDS}"
      }
    }
  }
}
```

Verify with `claude mcp list` (✔ Connected per server) and `claude mcp get
runbook_library`; inside a session, `/mcp` lists every connected server with its
tool count.

#### opencode

`opencode.json` in the project, or `~/.config/opencode/opencode.json`. `command`
is an **array**, the env block is `environment`, and substitution is
`{env:VAR}`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "runbook_library": {
      "type": "local",
      "command": ["<bundle>/.venv/bin/python", "<bundle>/mcp/runbook_library.py"],
      "enabled": true,
      "environment": {}
    },
    "release_tracker": {
      "type": "local",
      "command": ["<bundle>/.venv/bin/python", "<bundle>/mcp/release_tracker.py"],
      "enabled": true,
      "environment": {
        "DEPLOY_API_TOKEN": "{env:DEPLOY_API_TOKEN}",
        "DEPLOY_API_BASE_URL": "{env:DEPLOY_API_BASE_URL}",
        "DEPLOY_API_TIMEOUT_SECONDS": "{env:DEPLOY_API_TIMEOUT_SECONDS}"
      }
    }
  }
}
```

#### Codex CLI

`~/.codex/config.toml`. Credentials are forwarded by **name** through
`env_vars`, so their values stay in your environment. Codex's default tool
timeout is short — set a generous one:

```toml
[mcp_servers.runbook_library]
command = "<bundle>/.venv/bin/python"
args = ["<bundle>/mcp/runbook_library.py"]
startup_timeout_sec = 30
tool_timeout_sec = 1800

[mcp_servers.release_tracker]
command = "<bundle>/.venv/bin/python"
args = ["<bundle>/mcp/release_tracker.py"]
startup_timeout_sec = 30
tool_timeout_sec = 1800
env_vars = ["DEPLOY_API_TOKEN", "DEPLOY_API_BASE_URL", "DEPLOY_API_TIMEOUT_SECONDS"]
```

Verify with `codex mcp list`.

#### Goose

`~/.config/goose/config.yaml`, under `extensions:`. Literal values go in
`envs:`; credential variable **names** go in `env_keys:`, which Goose resolves
from its keyring or environment:

```yaml
extensions:
  runbook_library:
    name: runbook_library
    type: stdio
    cmd: <bundle>/.venv/bin/python
    args: ["<bundle>/mcp/runbook_library.py"]
    enabled: true
    envs: {}
    env_keys: []
    timeout: 300
  release_tracker:
    name: release_tracker
    type: stdio
    cmd: <bundle>/.venv/bin/python
    args: ["<bundle>/mcp/release_tracker.py"]
    enabled: true
    envs: {}
    env_keys: [DEPLOY_API_TOKEN, DEPLOY_API_BASE_URL, DEPLOY_API_TIMEOUT_SECONDS]
    timeout: 300
```

`goose configure` → *Toggle Extensions* shows whether it is enabled.

> **A local agent is not fleet, in one way that matters.** fleet spawns stdio
> servers with a **minimal** environment — PATH/HOME/locale plus exactly the
> manifest's `env:` map — so a key the manifest does not map never arrives no
> matter what you exported. Your coding agent is more generous: it will happily
> pass through an ambient variable the manifest never mentions, and the server
> will work locally and be unconfigured in production. If a server needs a
> variable, put it in `manifest.yaml` in the same PR.

---

## Part 2 — persona and system prompt

fleet layers two things you are reproducing by hand:

- **`system_prompts/`** — the base operating rules.
  [`chat.md`](../system_prompts/chat.md) is the interactive base;
  [`default.md`](../system_prompts/default.md) is the scheduled one. Use
  `chat.md` for everything on this page except a scheduled-protocol rehearsal.
- **`personas/`** — a YAML character layered on top.
  [`assistant.yaml`](../personas/assistant.yaml) is **Rowan**, the shipped
  default (`PERSONA_DEFAULT=assistant` selects it by **file basename**, not by
  the `name:` field). The bundle also ships **Juno** (`analyst.yaml`) and **Kip**
  (`platform-guide.yaml`).

The persona file is structured data, not a ready-made prompt string, so the
practical move is to hand the agent the file and tell it to adopt it.

**Claude Code, per invocation (closest to what fleet does):**

```sh
claude \
  --append-system-prompt-file <bundle>/system_prompts/chat.md \
  --append-system-prompt "Adopt this persona for all responses — its voice,
operating approach, principles, and formatting:
$(cat <bundle>/personas/assistant.yaml)"
```

`--system-prompt` / `--system-prompt-file` *replace* the default prompt rather
than appending, which usually costs you Claude Code's own tool scaffolding —
available for a clean-room run, too aggressive for most testing.

**Persistently, per project:** a `CLAUDE.md` at your test project root that tells
the agent to read `<bundle>/system_prompts/chat.md` and
`<bundle>/personas/assistant.yaml` and follow both. For a toggleable persona,
Claude Code's output styles (`.claude/output-styles/rowan.md`, selected with
`/output-style rowan`) hold the voice, principles and formatting sections.

Other agents have the same shape: Goose reads `.goosehints`, opencode and Codex
read `AGENTS.md`. Point whichever one at this bundle's `system_prompts/` and
`personas/`.

**One thing you cannot reproduce locally: `personas:` tool permissions.** The
manifest's per-persona gate — Kip's `deny: ["mcp:release_tracker/*"]` — is
enforced by fleet, not by the persona file. Adopt Kip in a coding agent and the
release tracker is still in the tool list. Test that gate with fleet, not here.

---

## Part 3 — run a protocol end to end

The credential-free combination: **Rowan + [`ask-the-runbooks`](../protocols/ask-the-runbooks.md)
+ `runbook_library`.** The protocol searches, reads the matching runbook *in
full*, answers, and cites — and refuses to invent policy when the library has
nothing.

**Step 1 — the one server it needs is registered** (Part 1). Confirm with `/mcp`
in-session.

**Step 2 — launch with Rowan and the chat base prompt applied** (Part 2).

**Step 3 — run it:**

```
Read <bundle>/protocols/ask-the-runbooks.md and follow it step by step.
My question: in what order do we rotate a service credential?
```

**How to grade the run.** A faithful one calls `rb_search` with the substantive
words rather than the whole sentence, takes the top hit (`rb-002`), calls
`rb_get_runbook` to read the **full** runbook rather than answering from the
180-character excerpt, leads with the answer, quotes the sentence carrying the
rule, and ends with `Source: Rotating a service credential (rb-002)`. Answering
from the search excerpt alone is the most common infidelity, and it is exactly
the failure step 3 of the protocol exists to prevent.

**Then test the refusal path**, which is the protocol's real contract:

```
Ask the runbooks: what is our policy on expensing a home office chair?
```

A correct run searches genuinely and then says the library has nothing on it —
optionally naming the nearest runbook — and does **not** invent a plausible
policy. That refusal is the thing you are verifying.

**The other protocols** run the same way; swap the file and the persona:

| Protocol | Persona | Notes |
| --- | --- | --- |
| [`incident-timeline.md`](../protocols/incident-timeline.md) | Juno (`analyst.yaml`) | Put a couple of fake log or export files in a scratch directory first; grade on whether inferred rows are marked `(inferred)` and whether an unidentifiable trigger is named as unidentified. |
| [`weekly-platform-report.md`](../protocols/weekly-platform-report.md) | Juno | A **scheduled** playbook — pair it with `system_prompts/default.md`, not `chat.md`, and give it an `inputs/` directory. Grade on blank cells: a run that estimates a number it could not compute has failed. |
| [`example.md`](../protocols/example.md) | any | The annotated template. Read it before writing your own. |

---

## Part 4 — skills

A skill is `skills/<name>/SKILL.md` plus optional reference files and a
`scripts/` directory. fleet puts each skill's `description` in the prompt roster
and loads the body only when the skill is used; the bundled scripts are meant to
be **run**, not read into context. Both of this bundle's scripts are plain
standard-library Python:

```sh
cd <bundle>
python3 skills/example-skill/scripts/greet.py "Rowan"
python3 skills/csv-profiler/scripts/profile_csv.py path/to/data.csv
```

Through an agent, point it at the folder and give it a matching task:

```
Read <bundle>/skills/csv-profiler/SKILL.md and follow it to profile the CSV I attached.
```

A faithful run *runs* `scripts/profile_csv.py` rather than reimplementing the
profiling in prose.

> `allowed-tools` in a skill's frontmatter is **advisory metadata**, surfaced for
> review and not enforced as an authorization boundary. Govern consequential
> tools through `agent_policy.critical_tools` in the manifest.

The local path and the cluster path agree here more than they do for
protocols: locally your agent reads `skills/` off the filesystem; on Kubernetes
fleet stages the merged skills tree (its built-in pack, the plugin's skills,
this `skills/`) into the workspace claim and every pod mounts it read-only
(fleet ADR-0055), so the same `skills/<name>/…` paths resolve inside a pod with
no image bake. What a laptop cannot prove is the staging itself — §8c of the
getting-started guide does.

---

## Part 5 — the Agent Plugin

`plugins/example-plugin/` is an [Agent Plugin](https://agent-plugins.org): a
`plugin.json`, one skill (`plugin-quickstart`) and an `mcp.json` declaring a
stdio server, `server/plugin_notes.py`, that keeps scratch notes under the
`PLUGIN_DATA` directory a conformant client provides. Its tests run with the
rest:

```sh
.venv/bin/python -m pytest mcp/tests/test_plugin_notes.py -q
```

To drive the server by hand, supply the two plugin variables the way a client
would and use the same stdio probe as Part 1 (the tools are `plugin_info`,
`note_add`, `note_list`, `note_clear`):

```sh
PLUGIN_ROOT="$PWD/plugins/example-plugin" PLUGIN_DATA=/tmp/plugin-data \
  python3 plugins/example-plugin/server/plugin_notes.py
```

A local coding agent that implements Agent Plugins loads the directory
directly; one that does not can still register the server from `mcp.json` by
hand. Under fleet the server runs host-side (the control-plane pod on a
cluster) and its tools appear as `mcp_plugin_notes_<tool>`; the skill joins the
roster like any other and reaches sandbox pods through the staged tree above.

---

## Optional — waking `release_tracker`

The gated connector stays dark with no token, which is why everything above runs
clean. To exercise it, export the variables **in the shell you launch the agent
from** and point the base URL at a JSON API you control:

```sh
export DEPLOY_API_TOKEN=...                                  # brings it online
export DEPLOY_API_BASE_URL=https://your-test-api.example/v1  # a test API, not production
export DEPLOY_API_TIMEOUT_SECONDS=30                         # optional
```

`/mcp` should then show `release_tracker` with `rt_list_releases`,
`rt_get_release` and `rt_open_change_request`.

> **`rt_open_change_request` writes.** The manifest lists it in
> `agent_policy.critical_tools`, so under fleet it stops for an audit
> confirmation with a 600-second approval window. **A local coding agent has no
> such gate** — it will simply call the tool. Point `DEPLOY_API_BASE_URL` at a
> throwaway endpoint before you let an agent anywhere near it.

Receipts go to `DEPLOY_API_OUTPUT_DIR`, which the manifest maps to
`${FLEET_WORKSPACE}/outputs` — a token only fleet substitutes. In a local agent
the variable is simply unset, and the tool answers `"receipt": "not written — no
workspace directory was offered for this run"`. That is the degrade-gracefully
path working, not a bug. Set the variable by hand to a scratch directory if you
want to see a receipt written.

---

## If you do have a fleet binary

None of this needs a cluster either — the manifest deliberately leaves
`sandbox.backend` unset precisely so this bundle still works on a laptop:

```sh
export FLEET_CLIENT_CONFIG_DIR=<bundle>

fleet validate-config              # parses the manifest, resolves personas, checks paths
fleet mcp test --all --deep        # spawns each server with the boot loader's exact
                                   #   env and gates, then runs the declared probe
```

`fleet mcp test` is strictly better than the stdio probe above, because it
applies the manifest's minimal-environment and enable-gate rules rather than
your shell's. `release_tracker` reporting "disabled — gate not met" with no
token is the correct answer, not a failure.

With rootless podman available, `fleet task run <task.yaml>` drives a real
governed turn through a real sandbox — the podman backend, not the Kubernetes
one. It costs one real model call, and it is the closest local approximation of
a scheduled run.

---

## What you have and have not proved

**Proved, if everything above passed:** both servers start and expose their
tools; `manifest.yaml` parses and agrees with the servers and both
Containerfiles; the ranker returns sensible results; Rowan and the base prompt
produce the intended behavior; `ask-the-runbooks` cites correctly and refuses
correctly; the skills' scripts run; the plugin's server round-trips notes
through `PLUGIN_DATA` and its `plugin.json` allowlist names real tools; the
gated connector stays dark without its token and degrades gracefully without a
workspace.

**Not proved, and not provable here:**

- **In-sandbox bundle reads.** The bake-and-declare pair
  (`Containerfile.sandbox` + `bundleDocsInImage`) has no local equivalent. Test
  it with §8c of the getting-started guide, or the `in-sandbox-bundle-read`
  case in [`evals/example.yaml`](../evals/example.yaml).
- **The sealed-egress NetworkPolicy.** Only a CNI that enforces policy makes a
  sealed sandbox sealed, and only §8d of the getting-started guide finds out
  whether yours does. kind does not — kindnet ignores NetworkPolicy, which is
  why `values-kind.yaml` refuses to claim `lockdown`.

Everything else on the cluster path — the images, the preflight, the RBAC verbs,
the workspace claim — fails loudly at boot or on the first tool call. These two
fail silently, which is why they get their own checks and their own paragraph
here.
