# example-kubernetes-config

A complete, **fork-this-to-start** client-config bundle for
[**fleet**](https://github.com/ElcanoTek/fleet) — with the **Kubernetes**
deployment as the main event.

fleet ships **no** client content. At boot it loads a *bundle* from the
`FLEET_CLIENT_CONFIG_DIR` environment variable: a manifest plus prompts,
personas, playbooks, and MCP servers that turn the generic engine into *your*
team's agent workspace. This repo is a clean, generic example of that bundle —
branded for a fictional company, **Larkspur**, with a default persona named
**Rowan** — plus everything a cluster deployment needs that a single-box one
does not.

> Nothing here is industry-specific and nothing here is secret. "Larkspur" is a
> placeholder for your company; the MCP servers, personas, and protocols exist
> to show you the *shape*. Replace them and the bundle is yours.

**→ [docs/KUBERNETES-GETTING-STARTED.md](docs/KUBERNETES-GETTING-STARTED.md)
is the walkthrough this repo exists for: an empty cluster on one end, agents
executing in ephemeral sandbox pods on the other.**

---

## Which example bundle do I want?

| | [example-config](https://github.com/ElcanoTek/example-config) | **example-kubernetes-config** (this repo) |
| --- | --- | --- |
| Target | one VM, `fleet bootstrap`, rootless podman | a Kubernetes cluster, `helm install`, one ephemeral pod per sandbox |
| Sandbox image | built on the box | built in CI or on your workstation, pushed to a registry, **with the bundle docs baked in** |
| Control plane | a systemd unit | a single-replica Deployment you build an image for |
| Owns | `install.sh` / `INSTALL.md` — registering the servers into a local coding agent | `deploy/kubernetes/` — two Containerfiles, two values overlays, a Makefile |
| Read it for | the exhaustive tour of every manifest section | the cluster story, honestly scoped |

They are **peers**, not fork parent and child. Both are complete bundles; take
whichever matches your deployment and steal from the other freely. Everything
below assumes you picked this one.

## Why fleet

fleet runs AI agents — interactive **chat** and recurring **scheduled** tasks —
on infrastructure you control. One Go process boots a unified agent runtime, a
scheduler, and a worker pool; the execution sandbox is pluggable and mandatory.
The design principles that matter when you author a bundle:

- **Any model.** fleet runs its own native agent loop, and models route
  OpenRouter-style, so you choose the right model per task instead of
  hard-wiring one vendor.
- **Sandboxed by default.** Every tool call — bash, Python, file I/O — executes
  inside a sandbox over a per-conversation workspace. The *backend* is
  pluggable (rootless podman, or one ephemeral Kubernetes pod per sandbox); the
  *sandbox* is not. There is no fast path that skips it and no host-execution
  fallback.
- **Cost-controlled.** Each turn runs against configurable per-task cost and
  token **ceilings**, a per-turn timeout, and an iteration cap.
- **Connected to your data via brokered MCP credentials.** Credentials are
  brokered **host-side** — on a cluster, that means inside the control-plane
  pod — and injected only when fleet runs a delegated MCP call, so they never
  enter a sandbox or the model's context. The manifest names the *variables*,
  never the *values*.
- **Reusable personas and protocols.** Standardize your team's agent recipes
  once, in a bundle, and point any deployment at it.
- **Standards-based, MIT-licensed, observable.** The runtime emits structured
  events for every turn — tool calls, results, usage, cost.

## What Kubernetes changes

The agent loop, the governance, and the credential boundary are identical. Four
things are genuinely different, and they are why this repo has a
`deploy/kubernetes/` directory at all:

1. **You build and push two images.** fleet publishes none. A control-plane
   image (the `fleet` binary + this bundle + `python3` for the MCP servers) and
   a sandbox image (the execution environment + a read-only snapshot of the
   bundle's doc dirs). `make images` builds both from one commit.
2. **The MCP servers run in the control-plane pod.** They are stdio
   subprocesses of the fleet process, because that is where credentials live.
   Sandbox pods get no env, no secrets, and no service-account token.
3. **A sandbox pod mounts exactly one thing — the workspace claim.** There is
   no host filesystem, so `protocols/`, `personas/` and `system_prompts/`
   reach a sandbox only if the *sandbox image* carries them at the same
   absolute paths, and only if the deployment **declares** that it does
   (`bundle_docs_in_image`). Bake without declaring and the file tools stay
   refused; declare without baking and reads fail not-found. **Skills take the
   other route:** fleet stages the skills tree — its built-in pack, every
   plugin's skills, this bundle's `skills/` — into the workspace claim at boot
   and every pod mounts it read-only (fleet ADR-0055), so skills work in a pod
   with no bake and no declaration.
4. **Some things degrade, and the guide says which.** Egress sealing becomes a
   NetworkPolicy your CNI must enforce; `allowlisted` egress mode is refused at
   boot; there is no per-pod pids limit and no per-sandbox resource telemetry;
   the bundled seccomp profile does not apply. Full list:
   [Honest scope](docs/KUBERNETES-GETTING-STARTED.md#12-honest-scope--what-a-cluster-deployment-does-not-get).

## How fleet loads this bundle

fleet reads the bundle directory from `FLEET_CLIENT_CONFIG_DIR`. On a cluster,
the control-plane image bakes the bundle at `/opt/fleet/client` and sets that
variable for you. Locally:

```sh
export FLEET_CLIENT_CONFIG_DIR=/path/to/example-kubernetes-config
export PERSONA_DEFAULT=assistant     # personas/assistant.yaml — the persona named "Rowan"
```

At boot fleet parses `manifest.yaml`, resolves each MCP server's **enable gate**
and `${VAR}` env interpolation against the process environment, and reads the
`system_prompts/`, `personas/`, `protocols/`, `prompts/`, `skills/` and
`plugins/` directories. `PERSONA_DEFAULT` selects the default persona by **file basename**
(`assistant`, not the display name `Rowan`); users still switch persona per
conversation in the UI. The loader and the authoritative schema live in
[`internal/clientconfig/clientconfig.go`](https://github.com/ElcanoTek/fleet/blob/main/internal/clientconfig/clientconfig.go).

The decoder is **strict**: an unknown or duplicate key fails the whole load at
boot. That is a feature — a typo'd key can never silently drop a policy — but
it also means a manifest field newer than your fleet binary takes the entire
bundle down, which is why a few blocks in `manifest.yaml` ship commented with a
dated note.

## The bundle contract

```
example-kubernetes-config/
  manifest.yaml          # branding, model tiers, the sandbox block (incl.
                         #   sandbox.kubernetes.bundle_docs_in_image), the MCP
                         #   catalog, empty-state cards, agent policy, task
                         #   templates, per-persona tool permissions — plus
                         #   commented, copy-paste-ready examples of http_tools,
                         #   webhook_triggers, providers, pricing, and hooks
  system_prompts/
    default.md           # base prompt for SCHEDULED agents
    chat.md              # base prompt for INTERACTIVE chat
  personas/              # *.yaml — PERSONA_DEFAULT picks one by file basename
    assistant.yaml       #   Rowan — general-purpose workspace assistant (default)
    analyst.yaml         #   Juno  — data & research analyst
    platform-guide.yaml  #   Kip   — runbook-grounded "how do we do X here" guide
  protocols/             # *.md | *.yaml — reusable playbooks
    example.md           #   annotated template: what a protocol is, how to write one
    ask-the-runbooks.md  #   grounded Q&A over the runbook library, with citations
    incident-timeline.md #   reconstruct a cited timeline from workspace evidence
    weekly-platform-report.md  # a SCHEDULED playbook: gather → compute → write
  prompts/               # Git-backed prompt library, shown in Chat + Operations Center
    release-readiness-brief.yaml
  skills/                # <name>/SKILL.md folders — instructions + bundled code
    example-skill/       #   annotated template (+ REFERENCE.md + a demo script)
    csv-profiler/        #   profile a CSV with the stdlib only
  plugins/               # Agent Plugins (agent-plugins.org): portable skills + MCP packages
    example-plugin/      #   plugin.json + one skill + mcp.json (a stdio server using PLUGIN_ROOT/PLUGIN_DATA)
  mcp/                   # the bundle's Python MCP servers — they run in the
    runbook_library.py   #   CONTROL-PLANE pod, never in a sandbox
    release_tracker.py
    data/runbooks.json
    requirements.txt     #   RUNTIME deps — the control-plane image installs these
    requirements-dev.txt #   pytest / ruff / respx — deliberately NOT in the image
    tests/
  sandbox/
    Containerfile        # the BASE sandbox image (also the podman path's image)
  deploy/kubernetes/     # ← what makes this repo the Kubernetes one
    Containerfile.control-plane   # fleet binary + python3 + this bundle
    Containerfile.sandbox         # the base image + the baked read-only docs (not skills — staged by fleet)
    values-example.yaml           # documented production overlay for fleet's chart
    values-kind.yaml              # evaluation overlay, with its four caveats named
    kind-cluster.yaml             # one-node kind cluster, and why one node
    README.md
  evals/                 # golden regression sets replayed by `fleet eval run`
  assets/                # brand assets referenced from manifest.yaml
  Makefile               # venv/test/lint, both images, helm lint/template, kind
```

## A tour of what this bundle ships

### Branding (white-label)

`branding:` in `manifest.yaml` is the whole white-label surface: the strings
(`app_name`, `login_title`, `login_tagline`, `share_title`,
`share_description`), an optional bundle-relative `logo`, an optional
`share_image` for link unfurls, and a 19-token `colors.light` / `colors.dark`
palette applied as a render-blocking stylesheet so even the login page paints
in your colors.

Two things worth knowing before you tune the palette. fleet's defaults for the
structure, scrim, and rail tokens are hand-tinted from **fleet's** primary hue
rather than derived from yours, so overriding `primary` alone leaves
fleet-tinted emphasis borders next to your brand — set the whole block, as
`manifest.yaml` does. And the semantic status colors (success / danger /
warning) are deliberately **not** themable: they encode meaning, so a failed
tool call reads as failure in every deployment.

`logo` and `share_image` ship **commented**, with dated notes naming the fleet
PR that introduced each — the strict decoder means an uncommented new field on
an older fleet fails the whole bundle. Full reference: fleet's
[docs/BRANDING.md](https://github.com/ElcanoTek/fleet/blob/main/docs/BRANDING.md).

### MCP servers — and where they actually run

| Server | Gate | Credentials | What it shows |
| --- | --- | --- | --- |
| `runbook_library` | `always: true` | none | Point an agent at **your own docs.** Searches a bundled JSON runbook set. |
| `release_tracker` | `enabled_env: [DEPLOY_API_TOKEN]` | brokered host-side | The **gated connector** pattern — stays dark until the token is set. |

- **`runbook_library`** is the canonical "answer from our docs" pattern
  (`rb_search`, `rb_get_runbook`, `rb_list_categories`). It needs no
  credentials, so a fresh checkout runs clean. Swap `mcp/data/runbooks.json`
  for your content, or rewrite the server to read your wiki or vector store. It
  also declares a **`probe:`** — the bundle-vetted, read-only canary call that
  `fleet mcp test --deep` runs to prove the server works end to end.
- **`release_tracker`** is a generic REST connector that registers but stays
  **dark** until `DEPLOY_API_TOKEN` is present (`rt_list_releases`,
  `rt_get_release`, and the write `rt_open_change_request`). Its entry carries a
  live `${FLEET_WORKSPACE}` mapping — which matters far more on a cluster than
  on a box, because the control-plane pod's `$HOME` is ephemeral and invisible
  to sandbox pods. The workspace claim is the one filesystem the control plane
  and every sandbox share.

`rt_open_change_request` is listed under `agent_policy.critical_tools`, so fleet
stops and takes an **audit confirmation** before it runs, with a per-tool
approval window from `critical_tool_timeouts`. The read tools are in
`parallel_safe_tools`, so fleet may dispatch them concurrently within a turn.

**On a cluster both servers are subprocesses of the control-plane pod.** That
is why `deploy/kubernetes/Containerfile.control-plane` installs `python3` and
`mcp/requirements.txt` — and why an image built from fleet's generic example
Containerfile boots fine, serves chat, and reports both servers dead in the
log without failing to start.

### Personas

| File | Name | Remit |
| --- | --- | --- |
| `assistant.yaml` | **Rowan** | Everyday workspace assistant — the shipped default. |
| `analyst.yaml` | **Juno** | Data & research analyst — computes on real data, shows the work. |
| `platform-guide.yaml` | **Kip** | Runbook-grounded guide — answers "how do we do X here?", always cited. |

The manifest's `personas:` block adds **per-persona tool permissions**, a
least-privilege gate that can only *narrow* what a persona sees. Kip carries
`deny: ["mcp:release_tracker/*"]`: the guide never needs the connector, so it
never sees it.

### Protocols, prompts, and skills

Protocols under `protocols/` encode "the way we do this here" once so chat and
scheduled agents run it the same way. Files under `prompts/` appear in fleet's
prompt picker in both Chat and the Operations Center, read-only in the UI and
reviewable in Git. Skills under `skills/` follow the open
[Agent Skills](https://github.com/anthropics/skills) standard: a folder with a
`SKILL.md`, optional reference files, and a `scripts/` directory, using
progressive disclosure so only the description sits in the prompt roster until
the skill is used.

**Protocols and prompts reach a sandbox only via the sandbox image on the
cluster path** — see `deploy/kubernetes/Containerfile.sandbox`. Skills take a
different route; see the next section.

### Skills on Kubernetes — staged, not baked

fleet embeds a built-in Agent Skills pack that every bundle inherits alongside
its own `skills/`, and this bundle inherits it. The tree an agent reads is
assembled **at boot** from three sources — that pack (inside the fleet
binary), every Agent Plugin's skills, and `skills/` — so no sandbox image can
carry it: its path is hash-derived and two of its sources are not files in this
repo.

On the kubernetes backend fleet therefore **stages the complete tree into the
workspace claim** at boot (`<workspace root>/skills`) and every sandbox pod
mounts it read-only — the same mechanism as fleet's shared file library
([fleet ADR-0055](https://github.com/ElcanoTek/fleet/blob/main/docs/adr/0055-kubernetes-skills-staged-into-the-workspace-claim.md)).
`SKILL.md`, reference files and bundled scripts all resolve from inside a pod,
for the file tools and for `bash`/`run_python`, with no `COPY skills/` in the
sandbox image and no `bundle_docs_in_image` involvement. A skill edit is a
new control-plane image (the bundle is baked into it) and nothing else.

The cost is honest and small: the skill bytes exist twice on a cluster
(control-plane image + staged copy in the claim), and the control plane must be
able to create that directory — the same requirement the shared library
already imposes. If staging fails, boot continues and the log says
`warning: stage skills for the kubernetes sandbox backend`; skills are then
rostered but unreadable in pods. On a fleet predating ADR-0055 the old remedy
(`skills_builtin: false` + `COPY skills/`) applies — see the dated note in
`manifest.yaml`.

### Agent Plugins — the portable package for skills + MCP servers

`plugins/` holds **Agent Plugins**: the open, vendor-neutral
[Agent Plugins standard](https://agent-plugins.org) (v1.0.0) that packages Agent
Skills and MCP servers together in one directory with a `plugin.json` manifest.
The same directory loads in fleet **and** in Cursor, VS Code, GitHub Copilot,
ChatGPT/Codex, Kiro and the other compatible clients, so a plugin is written
once and shared across tools.

```
plugins/
  example-plugin/
    plugin.json                       # REQUIRED: "$schema" + "name" (+ metadata)
    skills/plugin-quickstart/SKILL.md # an ordinary Agent Skill
    mcp.json                          # one stdio server: python3 ${PLUGIN_ROOT}/server/plugin_notes.py
    server/plugin_notes.py            # a scratch-notes server that persists in ${PLUGIN_DATA}
```

fleet (from the release that implements [ADR-0054](https://github.com/ElcanoTek/fleet/blob/main/docs/adr/0054-agent-plugins.md))
merges a plugin's skills into the same roster as `skills/` — this bundle's own
skill wins a name collision, a plugin's wins over fleet's built-in pack — and
appends its `mcp.json` servers to the MCP catalog as always-on entries launched
in the plugin root with `PLUGIN_ROOT` / `PLUGIN_DATA` set, subject to every gate
a manifest server already has. Older fleet releases ignore the directory; a
plugin defect never blocks the bundle, and `fleet validate-config` lists the
problems as advisories. Details: fleet's
[`docs/AGENT-PLUGINS.md`](https://github.com/ElcanoTek/fleet/blob/main/docs/AGENT-PLUGINS.md).

What Kubernetes changes about a plugin is *where its parts run*, not the
format: the server runs in the **control-plane pod** (so `plugins/` is copied
into `Containerfile.control-plane`, its runtime deps come from
`mcp/requirements.txt`, and `PLUGIN_DATA` lands on the data PVC), and its
skills reach sandbox pods through the staged skills tree above.

| Part | What it shows |
| --- | --- |
| `plugin-quickstart` skill | How the plugin is laid out, what the cluster path changes, how to use its server, and how to author or port a plugin. |
| `plugin_notes` server | A stdio MCP server started as `python3 ${PLUGIN_ROOT}/server/plugin_notes.py` that keeps a scratch-notes file under `${PLUGIN_DATA}`. Tools: `plugin_info`, `note_add`, `note_list`, `note_clear`. Tested in `mcp/tests/test_plugin_notes.py`. |
| `com.elcanotek.fleet` extension | The spec's reverse-domain namespace for client-specific data, here carrying fleet's per-server `tools` allowlist and `fleet mcp test --deep` `probe`. Other clients ignore it; nothing credential-shaped can go there. |

### Sandbox

`sandbox/Containerfile` defines the base execution image: Fedora plus a Python
data stack (pandas/numpy/scipy/matplotlib/scikit-learn), document and image
tooling, and a few read-only CLI tools. It runs read-only-rootfs, all
capabilities dropped, non-root as uid 1000, sealed or shaped egress.

On a cluster it is the **base** for
`deploy/kubernetes/Containerfile.sandbox`, which layers in the bundle's doc
dirs at `/opt/fleet/client/...` — byte-identical to the control-plane image's
`FLEET_CLIENT_CONFIG_DIR`, because the workspace symlinks fleet drops are
absolute and a pod resolves them against its own filesystem.

The Containerfile documents the constraints fleet's kubernetes backend places
on it — uid 1000, `$HOME=/home/sandbox`, `python3` and `sleep` on PATH — which
are not stylistic: the backend hard-codes them in the pod spec, and an image
that violates one produces a pod that starts and then fails on the first tool
call.

### Evals

`evals/` holds golden regression sets: known-good prompts replayed through
fleet's governed run loop at a pinned model and scored against expectations.
This is how you gate a model swap, a persona edit, or any manifest change on
*"did my known-good tasks get worse?"*:

```sh
fleet eval run example --bundle-path "$PWD"   # exit 0 = pass, 1 = fail — CI-ready
```

Each run replays against live models (real spend, no cache). Grow real sets
from real runs with `fleet eval capture --task <uuid>`.

## Make it yours

1. **Rebrand.** Edit `branding:` in `manifest.yaml`; replace
   `assets/larkspur-mark.svg`; rename the persona files and their `name:`
   fields and set `PERSONA_DEFAULT` to your default persona's **file
   basename**. Run `fleet validate-config` to catch a bad `logo` path before
   you restart into it.
2. **Rename the images.** `larkspur-fleet` / `larkspur-sandbox` appear in the
   `Makefile`, both Containerfiles, and both values overlays. Grep for
   `larkspur` and you have found all of them.
3. **Point the runbook library at your docs.** Replace
   `mcp/data/runbooks.json`, or edit `mcp/runbook_library.py` to read your
   wiki, database, or vector store.
4. **Add your MCP servers.** Drop a Python server under `mcp/`, add an entry to
   `mcp_servers[]` with the right enable gate, and name its credential
   *variables* (never values). Then add its runtime dependency to
   `mcp/requirements.txt` — the control-plane image installs from that file, so
   that is all it takes.
5. **Govern new tools.** Read-only tools in `agent_policy.parallel_safe_tools`;
   writes and consequential actions in `critical_tools` so they require an
   audit gate.
6. **Write personas, prompts, protocols, skills and plugins.** Copy an existing
   one and adapt it. **Anything the agent reads from inside a sandbox must be
   in a directory `deploy/kubernetes/Containerfile.sandbox` bakes** — add the
   `COPY` line in the same PR — with one exception: `skills/` and a plugin's
   skills are staged into the workspace claim by fleet itself, so they need no
   `COPY` line (and must not get one). A plugin's *server* runs in the
   control-plane pod; its dependencies go in `mcp/requirements.txt`.
7. **Tune the sandbox image.** Add packages your agents need to
   `sandbox/Containerfile`, keeping the uid-1000 / `/home/sandbox` / `python3`
   constraints intact.
8. **Fill in your cluster's specifics** in
   `deploy/kubernetes/values-example.yaml`: the RWX storage class, the pull
   secret (or delete it), the network mode. Then
   `make helm-template FLEET=…` and read the rendered env block — fleet's chart
   only gained a values schema in ElcanoTek/fleet#1257 — before that a
   misspelled key rendered nothing and Helm never complained, and even now the
   schema is a property of the fleet commit you pinned, not of this bundle.
9. **Capture goldens and gate regressions.** `fleet eval capture` a good run
   into `evals/<set>.yaml`, then `fleet eval run <set>` in CI.

## Where to go next

- **[docs/KUBERNETES-GETTING-STARTED.md](docs/KUBERNETES-GETTING-STARTED.md)** —
  empty cluster to working fleet, with an honest-scope section.
- **[docs/LOCAL-CLUSTER-KIND.md](docs/LOCAL-CLUSTER-KIND.md)** — rehearse it on
  a laptop, and the four things kind gets wrong on purpose.
- **[docs/TESTING-LOCALLY.md](docs/TESTING-LOCALLY.md)** — exercise the bundle's
  servers, personas and protocols in a local coding agent, no cluster involved.
- **[deploy/kubernetes/README.md](deploy/kubernetes/README.md)** — what each
  deployment artifact is and when it changes.
- **[mcp/README.md](mcp/README.md)** — author and test the Python MCP servers.
- **fleet:** [README](https://github.com/ElcanoTek/fleet) ·
  [docs/DEPLOYMENT-KUBERNETES.md](https://github.com/ElcanoTek/fleet/blob/main/docs/DEPLOYMENT-KUBERNETES.md) ·
  [ADR-0049](https://github.com/ElcanoTek/fleet/blob/main/docs/adr/0049-kubernetes-backend-split-control-plane.md) ·
  [clientconfig.go](https://github.com/ElcanoTek/fleet/blob/main/internal/clientconfig/clientconfig.go)

## License

Released under the [MIT License](LICENSE).
