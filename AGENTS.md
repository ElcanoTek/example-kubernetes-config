# AGENTS.md

Operating guide for AI coding agents working in **example-kubernetes-config**.
It follows the [agents.md](https://agents.md) convention; `CLAUDE.md` is a
symlink to this file so Claude Code and any `AGENTS.md`-aware tool read the
same instructions.

Humans, start with [`README.md`](README.md), then
[`docs/KUBERNETES-GETTING-STARTED.md`](docs/KUBERNETES-GETTING-STARTED.md).

## What this repo is

The public **fork-this-to-start** client bundle for
[`fleet`](https://github.com/ElcanoTek/fleet) **on Kubernetes**. fleet is a
client-agnostic engine that ships no customer content and loads a bundle at
boot (`FLEET_CLIENT_CONFIG_DIR`). This repo is that bundle, branded for a
fictional company — **Larkspur** — with a default persona named **Rowan**:
branding, model defaults, the MCP catalog (`manifest.yaml`), system prompts,
personas, protocols, prompts, skills, two example Python MCP servers under
`mcp/`, and — the part that makes this repo different — the cluster deployment
tooling under `deploy/kubernetes/`.

Nothing here is industry-specific and nothing here is secret. Larkspur is a
placeholder. The "platform ops" flavor of the runbooks and personas is a
generic team type, like support or research — not a vertical.

**The sibling to read first is
[example-config](https://github.com/ElcanoTek/example-config).** That is the
same idea aimed at the single-box podman install, and it owns things this repo
deliberately does not: `install.sh` and `INSTALL.md` (registering these servers
into a local coding agent), and the long tour of every manifest section. This
repo is the *cluster* answer. The two are **peers, not fork parent and child**
— a fix that matters to both is hand-ported, in its own PR, by someone who read
that repo's tests.

**Bundles are data; fleet is engine.** If something a forking team needs cannot
be expressed here, the fix is usually to extend fleet's bundle schema — not to
special-case a customer in fleet, and not to special-case Larkspur either.

## This bundle owns `mcp/`

**Edit `mcp/` here. It is the source of truth. There is no upstream.**

The two servers (`runbook_library.py`, `release_tracker.py`) were written here.
Keep it that way.

- **MUST** make MCP server changes here, as normal reviewed PRs with tests.
- **MUST NOT** introduce an automated sync between this bundle and any other
  repo — not a sibling client bundle, not a "generate the examples from
  production" mirror. Those mirrors revert reviewed fixes. See elcano-config
  #48 / #75: a sync silently undid an email fix and every `send_email` on the
  box answered `202 duplicate_suppressed` for a day. The org's last such mirror
  was deleted on 2026-08-13 and is not coming back.
- **MUST NOT** stamp `Synced-From:` commits or tell a reader to "fix it
  upstream and re-sync."

## Build · test · lint

```sh
make venv     # python3 -m venv .venv + requirements.txt + requirements-dev.txt
make test     # pytest mcp/ -m 'not expensive' -q
make lint     # ruff check mcp/ && ruff format --check mcp/
```

`pytest.ini` sets `testpaths = mcp mcp/tests`; tests import servers by bare name
(`import runbook_library`). The `expensive` marker gates tests that spend real
API money — run those by hand with `-m expensive`, never in a batch.

**The requirements split is load-bearing.** `mcp/requirements.txt` is RUNTIME
ONLY and `mcp/requirements-dev.txt` holds pytest/ruff/respx, because
`deploy/kubernetes/Containerfile.control-plane` installs the former into the
production image. Putting a test dependency in the runtime file ships a test
framework to production; putting a runtime dependency in the dev file produces
a control-plane image where that server dies at import, in the log, without
failing boot.

The MCP SDK pin is `mcp>=1.28.1,<2`. The `<2` ceiling is load-bearing: 2.0 drops
`mcp.server.fastmcp`, which both servers import, so an uncapped pin resolves to
a release on which neither server starts.

When you change a server's env vars or tool names, **update `manifest.yaml` in
the same PR**. Nothing checks this for you. A tool renamed in code but still
allowlisted in the manifest is dropped silently by fleet at boot, and an env key
the code reads but the manifest never provides means the subprocess never
receives it — fleet spawns stdio servers with a MINIMAL environment, so an
operator `export` never reaches a server on its own.

### The Helm checks CI cannot run

```sh
make helm-lint     FLEET=/path/to/fleet
make helm-template FLEET=/path/to/fleet    # renders and prints the env block
```

**CI does not run these and cannot**: rendering needs fleet's chart, and this
repo does not vendor it (that would put an engine version into bundle data).
So `make helm-template` before opening any PR that touches
`deploy/kubernetes/values-*.yaml` is the gate, and the PR template asks you to
paste what you saw.

Read the output; do not just check the exit code. **On any fleet predating
ElcanoTek/fleet#1257 the chart ships no values schema**, and Helm then silently
accepts a misspelled value key and renders nothing for it — a typo in
`bundleDocsInImage` is a green `helm lint`, a clean install, working chat, and
`view_file protocols/…` refused forever. Current
fleet ships `deploy/helm/fleet/values.schema.json`, which turns that class of
typo into a failed install. Do not rely on it: it is a property of the fleet
commit you pinned in step 2, not of this bundle, and it only closes the keys
the schema names. Render and read the output anyway.

## Two deployment shapes, and this repo is aimed at the second

1. **Single-box podman** — `fleet bootstrap` / `fleet update` on one VM; the
   sandbox image is built on the box from `sandbox/Containerfile`;
   `protocols/`, `personas/`, `system_prompts/` and the skills dir are
   bind-mounted read-only into every sandbox. This bundle still works there
   unchanged, and `sandbox/Containerfile` is kept honest for it.
2. **Kubernetes** (fleet #989 / ADR-0049) — a single-replica control-plane
   Deployment and one ephemeral pod per sandbox. This repo owns that
   deployment's tooling in `deploy/kubernetes/`.

What that means when you edit this repo:

- **A sandbox pod mounts only the workspace claim.** Anything you write that
  tells the agent to read or run a file under `protocols/`, `skills/`,
  `personas/` or `system_prompts/` from inside a sandbox works on path 1, and
  works on path 2 **only** because `deploy/kubernetes/Containerfile.sandbox`
  bakes those dirs into the sandbox image. Add a directory the agent reads
  in-sandbox → add it there too, and say so in the PR.
- **Add a directory fleet reads at all → add it to
  `Containerfile.control-plane` too.** That file enumerates its `COPY` lines
  rather than `COPY . .`, on purpose (no `.git`, no `.venv`, no second copy of
  the staged binary). The cost of that choice is that a new directory is
  silently absent until someone adds the line.
- **On path 2 the file tools reach bundle paths only because the deployment
  says so.** fleet clears the supporting-doc mounts there, and the fileop
  anchor refuses any root outside the workspace claim, unless
  `bundle_docs_in_image` (manifest) / `bundleDocsInImage` (chart values)
  declares the sandbox image carries them. **Bake without declaring →
  `view_file` refused, bash still works. Declare without baking → reads fail
  not-found.** Writes beneath those roots are refused either way. Keep the bash
  fallback both `system_prompts/` files carry — it is what makes a mis-declared
  or older deployment degrade instead of stall.
- **`mcp/` runs in the CONTROL-PLANE pod on path 2**, not in a sandbox — so the
  control-plane image needs `python3` plus `mcp/requirements.txt`. A new
  runtime dependency for a server is a dependency of that image; keeping
  `requirements.txt` honest is enough, because the Containerfile installs from
  it.
- **Nothing about path 2 changes the credential boundary.** Sandbox pods get no
  env, no secret mounts, and no service-account token. Connector calls stay
  host-side.
- **A connector that writes to `~/something` produces artifacts nobody can
  read.** The control-plane pod's `$HOME` is ephemeral and invisible to sandbox
  pods. Point connector output at `${FLEET_WORKSPACE}/…` — fleet resolves that
  token at spawn time and drops the key entirely when there is no workspace, so
  the server must treat it as optional.

## Invariants

- **`manifest.yaml` is the complete contract** for every server: env keys, tool
  allowlist, `critical_tools`, `identity_env`. A server reads plain env vars and
  stays customer-agnostic; per-customer identity arrives only through manifest
  env and suffixed account vars.
- **Credentials are never values in this repo.** Manifests name *variables*.
  fleet brokers the secrets host-side; they never enter a server's source, a
  test fixture, a Containerfile, a values file, or a log line. The example
  connector stays dark until `DEPLOY_API_TOKEN` is set.
- **No image, tag, registry, cluster name, or account id that belongs to
  anyone real.** `REGISTRY` is a variable everywhere; the values files carry
  `ghcr.io`-shaped placeholders and nothing else. This is a public template.
- **Keep Larkspur generic.** Do not drop a real customer's name, seat, account
  id, mailbox, or internal hostname into fixtures, runbooks, docs, or example
  prompts. Forks that become real client bundles scrub that themselves; the
  template must not be the place it is introduced.
- **Every write tool is audit-gated** through fleet's approval flow via
  `agent_policy.critical_tools`. Adding a write tool means adding it there in
  the same PR.
- **The two images ship as a pair.** The control-plane image is authoritative;
  the sandbox image carries a read-only snapshot of the doc dirs. Any change to
  `protocols/`, `personas/`, `system_prompts/` or `skills/` invalidates both.
  `make images` builds both from one commit so the easy path is the correct
  one; nothing else enforces it.
- **`skills_builtin: false` is a Kubernetes decision, not a taste one.** With
  fleet's built-in skills pack inherited, the skills dir is a merged tree under
  the control plane's data PVC, which no sandbox image can carry, and every
  skill becomes description-only inside a sandbox. There is no setting that
  gives you both the built-in pack and working in-sandbox skill files on this
  backend. Do not flip it back without changing the guide's honest-scope
  section in the same PR — and note it changes the podman path too, where both
  work fine.
- **`sandbox.backend` stays UNSET in `manifest.yaml`.** The backend is a
  property of the deployment, not the bundle; the chart sets
  `FLEET_SANDBOX_BACKEND=kubernetes` and env wins. Pinning it here would make
  this bundle refuse to boot on any single box and break `fleet mcp test` on a
  laptop.
- **`PERSONA_DEFAULT` is a file basename**, not the `name:` field inside the
  YAML. The shipped default is `assistant` (display name Rowan). `PERSONA` is
  the sibling knob and takes a bundle-relative *path*.
- **Honest docs.** If you change behavior, change the doc in the same PR — and
  if something degrades on Kubernetes relative to podman, it belongs in the
  getting-started guide's "Honest scope" section, not in a footnote.

## Things that are true and easy to get wrong

Collected here because each one has a failure mode that looks like something
else:

- fleet's manifest decoder is **strict**. An unknown key fails the entire
  bundle load at boot — so a manifest field newer than the fleet binary you run
  takes down the whole bundle, not just that field. New fields ship commented
  with a dated note explaining which fleet commit introduced them.
- The runner Role must grant **`get` on `pods/exec`**, not only `create`.
  fleet streams exec over a WebSocket upgrade, which is an HTTP `GET`, and the
  apiserver derives the verb from the method. A Role with only `create` passes
  the boot preflight and 403s on the first `bash` call.
- `sandbox.memory` / `sandbox.cpus` become pod **requests as well as limits**.
  Raising them is reserving capacity, not permitting a burst.
- The sandbox image must define a **uid-1000 user with `$HOME=/home/sandbox`**
  and must contain `python3` and `sleep`. fleet's backend hard-codes emptyDir
  mounts at `/home/sandbox/.cache`, `.ipython` and `.config`, runs the pod as
  1000, uses `sleep infinity` as PID 1, and uploads its bridge and fileops
  scripts to run with python3 — so a sandbox image without python3 breaks the
  *file tools*, not just `run_python`.
- Lifecycle **hooks run in the sandbox**, so a hook's command must exist in the
  *sandbox* image, not the control-plane one.
- `FLEET_DEFAULT_NETWORK_MODE=allowlisted`, `FLEET_SANDBOX_RUNTIME` and
  `FLEET_SANDBOX_SECCOMP_PROFILE` are **refused at boot** under this backend
  rather than ignored. Their replacements are NetworkPolicy shaping,
  `sandbox.kubernetes.runtimeClass`, and `sandbox.kubernetes.seccompProfile`.

## Where to look

- **Bundle contract, "make it yours", and the tour:** [`README.md`](README.md)
- **Empty cluster → working fleet, the centrepiece:**
  [`docs/KUBERNETES-GETTING-STARTED.md`](docs/KUBERNETES-GETTING-STARTED.md)
- **Rehearsing on a laptop, and the four things kind gets wrong:**
  [`docs/LOCAL-CLUSTER-KIND.md`](docs/LOCAL-CLUSTER-KIND.md)
- **Exercising the bundle in a local coding agent:**
  [`docs/TESTING-LOCALLY.md`](docs/TESTING-LOCALLY.md)
- **Authoring and testing the Python MCP servers:** [`mcp/README.md`](mcp/README.md)
- **The deployment artifacts themselves:**
  [`deploy/kubernetes/README.md`](deploy/kubernetes/README.md)
- **Engine side:** fleet's
  [`docs/DEPLOYMENT-KUBERNETES.md`](https://github.com/ElcanoTek/fleet/blob/main/docs/DEPLOYMENT-KUBERNETES.md),
  [ADR-0049](https://github.com/ElcanoTek/fleet/blob/main/docs/adr/0049-kubernetes-backend-split-control-plane.md),
  and the authoritative bundle schema in
  [`internal/clientconfig/clientconfig.go`](https://github.com/ElcanoTek/fleet/blob/main/internal/clientconfig/clientconfig.go).
