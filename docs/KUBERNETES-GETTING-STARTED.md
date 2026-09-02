# From an empty cluster to a working fleet

This is the walkthrough this repo exists for: an empty Kubernetes cluster on one
end, a running Larkspur workspace with agents executing in ephemeral sandbox
pods on the other. Every command here is meant to be run, in order, and every
Helm value and env var has been checked against fleet's chart and code rather
than remembered.

Budget about an hour the first time, most of it waiting on two container
builds.

> **Reference, not repetition.** The engine-side story — why the control plane
> is one replica, how the sandbox backend works, the provider-by-provider notes
> — lives in fleet's
> [`docs/DEPLOYMENT-KUBERNETES.md`](https://github.com/ElcanoTek/fleet/blob/main/docs/DEPLOYMENT-KUBERNETES.md)
> and [ADR-0049](https://github.com/ElcanoTek/fleet/blob/main/docs/adr/0049-kubernetes-backend-split-control-plane.md).
> This page is the **bundle** half: what a deployment of *this* bundle has to
> do, and in what order.
>
> Want to try it on a laptop first? [`LOCAL-CLUSTER-KIND.md`](LOCAL-CLUSTER-KIND.md)
> is the honest answer to "can I do this on kind" (yes, with four named
> caveats).

---

## 0. The shape you are building

```
        browser ──TLS──▶ Ingress ──▶ (optional web) ──▶ larkspur Service
                                                          │ chat        :8080
                                                          │ orchestrator:8000
   ┌──────────────────────────────────────────────────────┴────────────┐
   │  control-plane pod — ONE replica, strategy Recreate               │
   │  agent loop · scheduler · MCP broker                              │
   │  this bundle's Python MCP servers run HERE, as subprocesses       │
   │  every credential lives HERE and nowhere else                     │
   └────────┬──────────────────────────────────┬───────────────────────┘
            │ pods/exec (WebSocket)            │ Postgres: chat + sched
            ▼                                  ▼  (managed, or the chart's
   fleet-sandbox-<hex> pods (ephemeral)           evaluation StatefulSet)
   read-only rootfs · non-root uid 1000
   all caps dropped · no service-account token
   no env · no secrets · egress by pod label
            │
            └── workspace PVC (RWX) — mounted at the SAME absolute path
                as the control plane sees it
```

Two things to internalize before you start, because they explain most of the
steps below:

1. **The MCP servers run in the control-plane pod, not in a sandbox.** That is
   where brokered credentials live. It is why the control-plane image needs
   `python3` and this bundle's requirements, and why sandbox pods can be given
   nothing at all.
2. **A sandbox pod mounts exactly one thing: the workspace claim.** No host
   filesystem, so none of the bundle's `protocols/`, `personas/` or
   `system_prompts/` reach it — unless the *sandbox image* carries them. That
   is the entire reason `deploy/kubernetes/Containerfile.sandbox` exists.
   Skills are different: fleet stages the skills tree (built-in pack, plugin
   skills, `skills/`) *into* the claim at boot and every pod mounts it
   read-only (fleet ADR-0055), so they need no baking at all.

---

## 1. Prerequisites

Tick every box before you build anything. Each unticked one turns into a
failure two steps later that looks like something else.

| Need | Why | Check |
| --- | --- | --- |
| Kubernetes ≥ 1.29 | the chart's `kubeVersion` floor | `kubectl version --short` |
| A CNI that **enforces** NetworkPolicy | fleet verifies the deny-all policy *object* exists; only the CNI makes the lockdown seal real | Calico, Cilium, GKE Dataplane V2, the EKS VPC CNI policy agent, or Azure CNI with policy. Verified for real in step 8. |
| A **ReadWriteMany** storage class | the workspace claim is mounted by the control plane *and* every sandbox pod at once | `kubectl get storageclass` — EFS `efs-sc`, GKE `filestore-*`, AKS `azurefile-csi-nfs`, or your NFS/CephFS class |
| Managed PostgreSQL, **two separate databases** | fleet refuses to boot if chat and sched resolve to the same database — their `users` schemas and migration runners are incompatible | two DSNs in hand |
| A registry your nodes can pull from | a `localhost/` tag is not a registry; sandbox pods pull the sandbox image themselves | ECR / GAR / ACR / GHCR |
| `helm` ≥ 3.12, `kubectl`, and `podman` or `docker` | build + install | `helm version --short` |
| Go ≥ 1.27 and a checkout of **ElcanoTek/fleet** | you build the control-plane binary and you install *fleet's* chart; this repo vendors neither | `go version` |
| An OpenRouter API key | fleet routes models through it by default | — |

Set these once; the rest of the page uses them:

```sh
export EKC=$(pwd)                              # this repo
export FLEET=/path/to/fleet                    # the fleet checkout
export REGISTRY=123456789.dkr.ecr.us-east-1.amazonaws.com   # or ghcr.io/your-org
export TAG=$(git -C "$EKC" rev-parse --short HEAD)
export NS=larkspur
```

`TAG` is a commit of **this** repo on purpose. Both images carry a copy of this
bundle and they must be built from the same commit — see step 3.

---

## 2. Pick your fleet commit

fleet publishes no images and cuts no tagged releases yet (`fleet version`
reads the repo `VERSION` file, still `0.0.0`), so "which fleet" is a commit you
choose:

```sh
git -C "$FLEET" fetch --all
git -C "$FLEET" checkout <the commit you intend to run>
git -C "$FLEET" rev-parse --short HEAD    # write this down; it goes in your change log
```

Three things in this bundle need a recent enough fleet. The first two fail
*loudly*; the third fails quietly, which is why it is listed:

- `manifest.yaml`'s `sandbox.kubernetes` block, and `bundle_docs_in_image`
  inside it. fleet's manifest decoder is **strict** — on a fleet that predates
  those fields, an unknown key fails the entire bundle load at boot, not just
  that field. Check the binary you are about to build:
  ```sh
  grep -n 'yaml:"bundle_docs_in_image"' "$FLEET/internal/clientconfig/clientconfig.go"
  ```
  No output means comment the `kubernetes:` block out of `manifest.yaml` and
  accept that in-sandbox `view_file` on bundle paths will be refused.
- The chart's runner Role must grant **`get` on `pods/exec`**, not only
  `create`. fleet streams exec over a WebSocket upgrade, which is an HTTP
  `GET`, and the apiserver derives the RBAC verb from the method. A chart old
  enough to grant only `create` passes the boot preflight and then 403s on the
  first `bash` call — boot having reported the cluster fine. Check:
  ```sh
  grep -A3 'pods/exec' "$FLEET/deploy/helm/fleet/templates/rbac.yaml"
  ```
  If `verbs:` there does not include `get`, either update your fleet checkout
  or add the verb to your own RoleBinding before you go looking for a fleet
  bug.
- Skills in pods and the `plugins/` dir. This bundle inherits fleet's built-in
  skills pack and ships an Agent Plugin, both of which reach a sandbox only
  because fleet **stages the skills tree into the workspace claim** at boot
  (ADR-0055) and loads `plugins/` (ADR-0054). An older fleet ignores
  `plugins/` without a word and leaves every skill description-only inside a
  pod. Check:
  ```sh
  grep -n 'func (b \*Bundle) StageSkillsAt' "$FLEET/internal/clientconfig/builtin_skills.go"
  ```
  No output means follow the dated note in `manifest.yaml`'s Agent Skills
  section (set `skills_builtin: false`, restore `COPY skills/` in
  `Containerfile.sandbox`) and expect no plugin.

---

## 3. Build the two images — from one commit, together

Both images carry a copy of this bundle. The **control-plane image is
authoritative** (manifest, policy, prompts, MCP servers); the **sandbox image
carries a read-only snapshot** of the doc dirs. Build them from the same commit
of this repo and roll them as a pair, or an agent reads last release's
protocols inside a sandbox while the control plane runs this one's. Nothing
enforces that — which is exactly why there is a single `make` target that does
both.

```sh
cd "$EKC"
make images FLEET="$FLEET" REGISTRY="$REGISTRY" TAG="$TAG"
make push          REGISTRY="$REGISTRY" TAG="$TAG"
```

That is three builds behind one target, and it is worth knowing what each does:

```sh
# 3a. the fleet binary, staged into this repo's build context.
#     Static (CGO_ENABLED=0), so the runtime stage needs no Go toolchain.
cd "$FLEET" && CGO_ENABLED=0 go build \
  -ldflags "-X github.com/ElcanoTek/fleet/internal/version.version=$(cat VERSION)" \
  -o "$EKC/deploy/kubernetes/fleet" ./cmd/fleet

# 3b. control plane: fleet + python3 + mcp/requirements.txt + this bundle
#     baked at /opt/fleet/client. Build context is the REPO ROOT.
cd "$EKC" && podman build -t "$REGISTRY/larkspur-fleet:$TAG" \
  -f deploy/kubernetes/Containerfile.control-plane .

# 3c. sandbox, in two layers. The base's context is sandbox/ (the same context
#     fleet's canonical scripts/build-sandbox-image.sh uses on a single box);
#     the overlay's context is the repo root, so it can reach protocols/ etc.
podman build --pull=newer -t "localhost/larkspur-sandbox-base:$TAG" \
  -f sandbox/Containerfile sandbox/
podman build -t "$REGISTRY/larkspur-sandbox:$TAG" \
  --build-arg BASE="localhost/larkspur-sandbox-base:$TAG" \
  -f deploy/kubernetes/Containerfile.sandbox .
```

`deploy/kubernetes/fleet` is gitignored: the build borrows an engine binary,
this repo never carries one.

**Why the control-plane image is not fleet's generic example.** That one
installs `git`, `ca-certificates` and `tzdata`. An image built from it boots
fleet, serves chat, and reports both of this bundle's MCP servers dead — they
are `python3 mcp/<server>.py` stdio subprocesses. It is a per-server error in
the log, not a boot failure, so it is easy to ship and not notice.

**Why the sandbox image is two layers.** The base is the bundle's real sandbox
definition and is what the single-box podman install builds, unchanged. The
overlay adds the doc dirs at `/opt/fleet/client/...` — byte-identical to the
control-plane image's `FLEET_CLIENT_CONFIG_DIR`, because the workspace symlinks
fleet drops are **absolute** and a sandbox pod resolves them against its own
filesystem. `mcp/` and `manifest.yaml` are deliberately *not* in the sandbox
image: the connectors run host-side and must not be readable from a sandbox.
Neither is `skills/`: fleet stages the skills tree into the workspace claim
itself (step 8c proves it), so a baked copy would never be read.

---

## 4. Namespace and secrets

```sh
kubectl create namespace "$NS"
```

The application secret. Sandbox pods receive **no env and no service-account
token**, so nothing here can reach one — it lives in the control-plane pod
only.

```sh
kubectl -n "$NS" create secret generic larkspur-secrets \
  --from-literal=OPENROUTER_API_KEY='sk-or-...' \
  --from-literal=CHAT_SERVER_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=FLEET_CHAT_DATABASE_URL='postgres://fleet:...@db.internal:5432/chat?sslmode=require' \
  --from-literal=FLEET_SCHED_DATABASE_URL='postgres://fleet:...@db.internal:5432/sched?sslmode=require' \
  --from-literal=DEPLOY_API_TOKEN='...'          # optional: gates the release_tracker server
```

- **The shared chat token has two spellings and only one of them works
  everywhere.** The fleet binary accepts `FLEET_SERVER_TOKEN` *or*
  `CHAT_SERVER_TOKEN` (it resolves the `FLEET_` prefix first, then the legacy
  `CHAT_` one); the web tier reads **only** `CHAT_SERVER_TOKEN`. Use
  `CHAT_SERVER_TOKEN` in both places and the question never comes up. fleet
  refuses to boot without it even when you run no web tier.
- The two DSNs must be **different databases**. fleet compares them at boot and
  aborts with "chat and sched must use SEPARATE databases" if they collide.
  Schema migrations run automatically at startup against both.
- Keep `postgres.enabled: false` when you use managed Postgres. With it true
  the chart sets both DSNs as container `env`, and Kubernetes `env` beats
  `envFrom` — so your Secret's DSNs are silently ignored and you stay on the
  in-cluster database.
- `DEPLOY_API_TOKEN` is this bundle's one credential-gated connector. Omit it
  and `release_tracker` simply stays dark — a fresh install runs clean with no
  secrets beyond the three above.

The registry pull secret, if your registry needs one. **Note it is referenced
twice** in the values file — once as `imagePullSecrets` for the control plane,
once as `sandbox.kubernetes.imagePullSecret` for the pods fleet creates. The
control plane's pull secrets do not apply to pods it creates.

```sh
kubectl -n "$NS" create secret docker-registry regcred \
  --docker-server=ghcr.io --docker-username=<user> --docker-password=<PAT>
```

On EKS with ECR, node-role pull covers both — delete `imagePullSecrets` and
blank `sandbox.kubernetes.imagePullSecret` in your values instead.

---

## 5. Review the values overlay

Open [`deploy/kubernetes/values-example.yaml`](../deploy/kubernetes/values-example.yaml)
and change the four things that are yours:

| Field | What to set |
| --- | --- |
| `workspace.storageClassName` | your RWX class (leave empty only if the *default* class is RWX) |
| `config.existingSecret` | the Secret name from step 4, if you renamed it |
| `imagePullSecrets` / `sandbox.kubernetes.imagePullSecret` | your pull secret, or remove both |
| `config.env.FLEET_DEFAULT_NETWORK_MODE` | `lockdown` (sealed sandboxes) or `open` (sandboxes may reach the internet). **`allowlisted` is refused at boot** under this backend — the host-side egress proxy is unreachable from a pod. |

Leave `sandbox.kubernetes.bundleDocsInImage: true` alone unless you stopped
using `Containerfile.sandbox`. Step 8 tests it.

Then render before you install. A fleet predating ElcanoTek/fleet#1257 ships
**no values schema**, so Helm accepts a misspelled key silently and renders
nothing for it; current fleet ships one and rejects unknown keys in the objects
it covers. Either way the schema belongs to the fleet commit you picked in
step 2, not to this bundle — so reading the rendered output is still the
check:

```sh
make helm-template FLEET="$FLEET" REGISTRY="$REGISTRY" TAG="$TAG"
```

You should see, among others:

```
- name: FLEET_SANDBOX_BACKEND            value: "kubernetes"
- name: FLEET_SANDBOX_IMAGE              value: "<REGISTRY>/larkspur-sandbox:<TAG>"
- name: FLEET_SANDBOX_K8S_WORKSPACE_CLAIM value: "fleet-workspace"
- name: FLEET_SANDBOX_K8S_NETWORK_POLICY  value: "fleet-sandbox-deny-all"
- name: FLEET_SANDBOX_K8S_BUNDLE_DOCS_IN_IMAGE value: "true"
```

A missing `FLEET_SANDBOX_K8S_BUNDLE_DOCS_IN_IMAGE` line is the single most
common thing to get wrong here, and it is silent: everything installs, chat
works, and `view_file protocols/…` is refused forever.

---

## 6. Install

```sh
cd "$EKC"
helm upgrade --install larkspur "$FLEET/deploy/helm/fleet" \
  --namespace "$NS" --create-namespace \
  -f deploy/kubernetes/values-example.yaml \
  --set image.repository="$REGISTRY/larkspur-fleet" --set image.tag="$TAG" \
  --set sandbox.image="$REGISTRY/larkspur-sandbox:$TAG"
```

(`make install FLEET=... REGISTRY=... TAG=...` is the same line.)

Image refs stay on the command line rather than in the values file: they carry
the build tag, and a values file that pins one is stale the moment anything
else in it changes.

The chart creates: the control-plane Deployment (1 replica, `Recreate`), the
`larkspur-runner` Role + RoleBinding, the control-plane and sandbox
ServiceAccounts, the two PVCs, the `fleet-sandbox-deny-all` NetworkPolicy, and
a ClusterIP Service on 8080/8000.

---

## 7. Watch the preflight

Selecting the kubernetes backend makes fleet run a **fail-closed cluster
preflight** before it serves anything: apiserver reachable with valid
credentials, every RBAC verb present, the workspace claim readable, the
sealed-egress NetworkPolicy object present, and the RuntimeClass present when
one is configured. Any failure aborts boot — there is no degrade to podman and
none to host execution.

```sh
kubectl -n "$NS" rollout status deploy/larkspur --timeout=5m
kubectl -n "$NS" logs deploy/larkspur | grep -Ei 'sandbox|preflight'
```

What a healthy boot says:

```
sandbox: kubernetes backend preflight OK — apiserver v1.30.4, sandbox namespace "larkspur"
sandbox: kubernetes backend — bundle_docs_in_image declared: keeping fileop read anchors for 4 bundle doc root(s) [...]
sandbox: kubernetes backend — image=..., pool=..., workspace=/var/lib/fleet/workspace, namespace=larkspur, runtime_class=cluster default
sandbox: network mode=lockdown — every sandbox pod is labeled fleet.elcanotek.com/egress=none ...
```

If that second line is missing, or says supporting-doc mounts "do not apply",
go back to step 5 — the declaration did not reach the pod.

Re-run the same checks any time, plus everything else the verb covers:

```sh
kubectl -n "$NS" exec deploy/larkspur -- fleet validate-config
```

---

## 8. Prove it actually works

Four checks, in increasing order of "this is really running".

### 8a. The API answers

```sh
kubectl -n "$NS" port-forward svc/larkspur 8080:8080 &
curl -s localhost:8080/healthz
```

### 8b. The MCP servers spawned

This is the check the control-plane image exists for. It spawns each server
with the boot loader's exact env and gates, handshakes, and lists tools;
`--deep` additionally runs the manifest-declared `probe:`.

```sh
kubectl -n "$NS" exec deploy/larkspur -- fleet mcp test --all --deep
```

`runbook_library` must be connected with `rb_search` / `rb_get_runbook` /
`rb_list_categories` and a passing probe. `plugin_notes` — the example Agent
Plugin's server, loaded from `plugins/example-plugin` — must be connected with
`plugin_info` / `note_add` / `note_list` / `note_clear` and a passing probe;
if it is missing entirely, the control-plane image was built without the
`COPY plugins/` line or from a fleet that predates ADR-0054. `release_tracker`
is connected only if you supplied `DEPLOY_API_TOKEN`; "disabled — gate not
met" is the correct answer otherwise, not a failure.

### 8c. A sandbox pod is created, used, and deleted

Watch in one terminal:

```sh
kubectl -n "$NS" get pods -l app.kubernetes.io/name=fleet-sandbox -w
```

Drive a real turn in another. `fleet task run` is fleet's one-shot local
harness — no server, no database, but the **same governed loop and the same
sandbox pool**, so it exercises the whole chain in one command. It costs one
real model call.

```sh
kubectl -n "$NS" exec -i deploy/larkspur -- sh -c 'cat > /tmp/smoke.yaml' <<'YAML'
prompt: |
  Three things, in the sandbox:
  1. Run `python3 -c "print(6*7)"` and report the number it printed.
  2. Read protocols/ask-the-runbooks.md with the view_file tool and quote its
     first heading. If view_file errors, say so explicitly, then fall back to
     `cat` and quote it that way.
  3. Run `python3 skills/example-skill/scripts/greet.py "Rowan"` and report
     its output, then read skills/plugin-quickstart/SKILL.md with view_file
     and quote its first heading.
model: "anthropic/claude-sonnet-4.5"
max_iterations: 8
YAML

kubectl -n "$NS" exec -it deploy/larkspur -- \
  fleet task run --workspace /var/lib/fleet/workspace/smoke /tmp/smoke.yaml
```

A `fleet-sandbox-<hex>` pod should appear in the watch, go `Running`, and be
deleted when the run ends. The answer should contain `42` **and** the protocol
heading read with `view_file` — no fallback. A fallback to `cat` means the
image bakes the docs but the declaration did not land (step 5); a "not found"
from both means the declaration landed but the image does not carry them (step
3c). (On a fleet older than ElcanoTek/fleet#1296 the `view_file` leg failed
under `fleet task run` on every backend — the one-shot harness never
registered the workspace root — while the same read worked in a chat turn; if
you pinned such a fleet, prove the docs side with one `fleet chat --no-tui
--message "Read protocols/ask-the-runbooks.md with view_file …"` turn
instead.)

The third item proves the **staged skills tree**: the greeting comes from a
bundle skill's script and the heading from a skill that arrived inside the
example *plugin*, both read from `<workspace>/skills` — a read-only subPath
mount of the claim, not the image. If both fail not-found, the control plane
could not stage: `kubectl logs deploy/larkspur | grep 'stage skills'` names
the reason (usually a workspace root the control plane cannot write, or a
fleet predating ADR-0055 — step 2).

### 8d. The lockdown seal is real

Only if you set `FLEET_DEFAULT_NETWORK_MODE=lockdown`. This is the check almost
nobody runs, and it is the one that finds out your CNI is not enforcing
anything:

```sh
# http, not https: busybox wget has no TLS, so an https URL fails for TLS
# reasons even on an UNSEALED cluster and fakes a passing seal test.
kubectl -n "$NS" run seal-test --restart=Never --rm -it \
  --labels=app.kubernetes.io/name=fleet-sandbox,fleet.elcanotek.com/egress=none \
  --image=busybox -- wget -T 5 -q -O- http://example.com && echo "NOT SEALED"
```

Run the control too — the same command **without** the labels must print the
page, or the "sealed" result is telling you about a broken network, not an
enforced policy.

A CNI that enforces the policy times the labeled request out. If you see page
content and `NOT SEALED`, the deny-all NetworkPolicy object exists (fleet
checked) and means nothing (your CNI ignored it) — kind's bundled kindnetd
does exactly this; k3s's embedded policy controller enforces. Fix the CNI or
stop calling those turns sealed.

---

## 9. Expose it

Everything above runs through `kubectl port-forward`, which is fine for a
smoke test and wrong for people.

- **API only, in-cluster:** the ClusterIP Service `larkspur` on 8080 (chat) and
  8000 (orchestrator) is already there.
- **With the web UI:** build fleet's `web/` tree into an image yourself (this
  bundle does not build it), then set `web.enabled=true` and `web.image=...`.
  The chart wires `CHAT_SERVER_URL` and `ORCHESTRATOR_SERVER_URL` at the fleet
  Service for you, binds the app to all interfaces, and passes the port. Give
  it its own `web.existingSecret` carrying `CHAT_SERVER_TOKEN` (the same value
  the control plane has) and `APP_SESSION_SECRET` — the app throws at startup
  without either. Do **not** point it at `config.existingSecret`: that one
  holds `OPENROUTER_API_KEY` and the database URLs, which the web tier never
  reads, and it is the one pod behind the Ingress.

  Until you do this there is **no user interface at all** — the fleet binary
  serves an API. Every pod will be Ready and every check above will pass. Do
  not read that as "the deployment works" until a person can open it.
- **Ingress:** set `ingress.enabled=true`, `ingress.className`,
  `ingress.host`, and `ingress.tls`. The Ingress routes to the web Service when
  `web.enabled`, otherwise to the chat port directly.
- **Somebody has to be an administrator.** A fresh database has zero admins,
  and a chat user is never silently promoted — so without
  `FLEET_ORCHESTRATOR_BOOTSTRAP_ADMINS` (set it in `config.env`; the overlay
  ships a placeholder) the `/orchestrator` page tells everyone to ask an admin
  and there is nobody to ask. It is re-asserted idempotently on every boot, so
  fixing it later is a `helm upgrade` and a restart, not a database edit.
- **Two build-time web env vars stay outside this bundle** because they are
  properties of the *host*, not of the client whose branding it wears:
  `NEXT_PUBLIC_PUBLIC_ORIGIN` (your public origin — unset, share links unfurl
  against a placeholder host, silently) and `NEXT_PUBLIC_APP_NAME` (only the
  fallback name shown when the backend is unreachable, on a current fleet).

---

## 10. Day 2

| Single box | Here |
| --- | --- |
| `fleet update` | rebuild **both** images from the new commit, `helm upgrade`. Strategy `Recreate` means a brief full stop, not a rolling overlap — that is the single-owner scheduler invariant, not an oversight. |
| on-box sandbox rebuild | `make sandbox && make push`, then `helm upgrade` with the new tag. Nothing rebuilds an image on a cluster. |
| `fleet-backup.timer` | managed-database snapshots (recommended), or a CronJob running `fleet backup --db=all --prune` off the control-plane image |
| `fleet-maintenance.timer` | **nothing to schedule.** `fleet cleanup` prunes dangling *podman* image layers and Go build caches; a control-plane pod has neither, so the job would print two disk lines and exit. Node-local image GC is the kubelet's. fleet's own hourly maintenance loop — reclamation, disk backpressure, stuck-task backstops — runs *inside* the control plane on both deployment shapes |
| `.env` on the box | the `larkspur-secrets` Secret plus `config.env` in the values file |
| `journalctl -u fleet` | `kubectl -n larkspur logs deploy/larkspur` |
| `fleet timers install` | not applicable — systemd tooling |

**Rotating a credential** is a Secret update plus a control-plane restart
(`kubectl -n "$NS" rollout restart deploy/larkspur`); env is read at boot.

**Adding or changing an MCP server** is a `manifest.yaml` change, which means a
new control-plane image. The manifest is baked in, not mounted. If that cadence
hurts, mount the bundle from a ConfigMap or a volume at
`FLEET_CLIENT_CONFIG_DIR` instead — fleet supports it and the engine/bundle
split is satisfied either way — at the cost of the bundle no longer being
pinned to an image digest.

**Changing a protocol, persona or system prompt** means rebuilding **both**
images, because the sandbox image carries the snapshot the agent reads.
**Changing a prompt, a skill or a plugin** means rebuilding only the
control-plane image: skills are staged from it into the workspace claim at
boot, and plugins load from it.

**Scaling.** Never a second control-plane replica — the chart does not even
expose the knob. More work means raising `FLEET_MAX_CONCURRENT_AGENTS`
alongside the control plane's `resources`, and giving sandbox pods a dedicated
node pool via `sandbox.kubernetes.nodeSelector` + `.tolerations`.

---

## 11. Teardown

```sh
helm uninstall larkspur -n "$NS"
```

**The PVCs survive on purpose.** The chart stamps
`helm.sh/resource-policy: keep` on both, because the workspace holds user data
— conversation files, task outputs, connector receipts. Removing them is a
separate, deliberate act:

```sh
kubectl -n "$NS" delete pvc fleet-workspace fleet-data
kubectl delete namespace "$NS"
```

If you enabled the chart's evaluation Postgres, its StatefulSet PVC
(`pgdata-larkspur-postgres-0`) also survives the namespace delete only if the
namespace survives — deleting the namespace takes it, and your data, with it.

---

## 12. Honest scope — what a cluster deployment does NOT get

Measured, not guessed. fleet's own list plus the parts specific to this bundle.

**From the engine (fleet `docs/DEPLOYMENT-KUBERNETES.md`):**

- **Egress sealing is delegated.** Podman's `--network=none` is a kernel
  namespace with no interface. Here it is a pod label matched by a
  NetworkPolicy, and fleet can only verify the object exists. Step 8d is not
  optional.
- **`FLEET_DEFAULT_NETWORK_MODE=allowlisted` is refused at boot.** The
  host-side egress proxy is unreachable from a pod. Use `lockdown` or `open`
  plus NetworkPolicy shaping.
- **No per-pod pids limit.** `FLEET_SANDBOX_PIDS` has no Pod-spec equivalent;
  runaway process counts are bounded by pod memory/CPU and the kubelet's
  `podPidsLimit` if you set one.
- **No per-sandbox resource telemetry.** `podman stats` has no in-process
  counterpart, so task resource summaries are absent. Use your cluster metrics
  on the `fleet-sandbox` pods.
- **The bundled seccomp profile does not apply.** Pods run `RuntimeDefault`, or
  a Localhost profile you install on the nodes and name in
  `sandbox.kubernetes.seccompProfile`. Setting the podman
  `FLEET_SANDBOX_SECCOMP_PROFILE` here refuses to boot rather than being
  ignored — as does `FLEET_SANDBOX_RUNTIME`, whose replacement is
  `sandbox.kubernetes.runtimeClass`.
- **Warm-pool pods hold cluster resources while parked.** Requests equal
  limits. Size `sandbox.warmSize` as standing capacity, not as free speed.
- **Disk quota is per-pod ephemeral storage**, which is a *stronger* cap than
  podman's — but the workspace claim sits outside it, exactly as the bind mount
  does under podman. Many files still add up.
- **The kind path is a documented walkthrough, not a CI job.** fleet's CI lints
  and template-renders the chart and unit-tests the backend against a fake
  apiserver; it does not stand up a cluster.

**From this bundle:**

- **`write_file` / `edit_file` on bundle docs are refused**, by design. The
  declared roots are re-admitted **read-only**, so a turn cannot rewrite its own
  protocols. Reads work; writes do not; that asymmetry is the point.
- **Skills live twice, and the control plane must be able to write the
  claim.** Skills work in pods because fleet stages the merged tree into
  `<workspace root>/skills` at boot (ADR-0055) — so the skill bytes exist in
  the control-plane image *and* in the claim, and a storage class that hands
  the control plane a root it cannot write degrades skills to description-only
  in pods with a `stage skills` warning in the log rather than a boot failure.
  Small, and honest.
- **The sandbox docs snapshot can go stale.** Nothing enforces that the two
  images came from one commit. `make images` exists so the easy path is the
  correct one; the discipline is still yours.
- **The sandbox image base is unpinned** (`fedora-minimal:latest`) and nothing
  rebuilds it on a cluster. It is as old as its last build. Rebuild and roll on
  a cadence; the weekly `sandbox-canary` workflow only proves the Containerfile
  still *builds*.
- **`install.sh` is not here.** Registering these MCP servers into a local
  coding agent is a laptop workflow, not a cluster one; the
  [example-config](https://github.com/ElcanoTek/example-config) template owns
  that path and its installer works unchanged against this bundle's
  `manifest.yaml`.

---

## 13. Troubleshooting

Two more, observed on the fleet#1264 validation clusters:

- **`OpenRouter /models fetch failed … network is unreachable` in the logs,
  yet turns work.** The control-plane pod resolved an IPv6 address first on a
  cluster with no IPv6 egress; the fetch falls back to the cached catalog and
  completions go out over IPv4. Cosmetic.
- **`fleet task run` times out taking a sandbox on a small node.** The
  one-shot harness builds its own warm pool from `FLEET_SANDBOX_WARM_SIZE`,
  *beside* the server's — on a tight node that transient double-reservation
  can make the turn's own pod unschedulable. Prefix the harness with
  `FLEET_SANDBOX_WARM_SIZE=0`; its warm pods die with the process anyway.


| Symptom | Cause |
| --- | --- |
| Boot dies with `kubernetes sandbox preflight` | the message names the exact missing piece — an RBAC verb, the claim, the NetworkPolicy, the RuntimeClass. Diff your RBAC against `deploy/helm/fleet/templates/rbac.yaml`. |
| Boot fine, first `bash` call 403s | the runner Role grants `create` but not `get` on `pods/exec`. See step 2. |
| Boot dies with `FLEET_SANDBOX_RUNTIME … has no effect` | a podman knob leaked into the cluster env. Its replacement is `sandbox.kubernetes.runtimeClass`. Same for `FLEET_SANDBOX_SECCOMP_PROFILE`. |
| Boot dies with `allowlisted is not supported` | set `FLEET_DEFAULT_NETWORK_MODE` to `lockdown` or `open`. |
| Boot dies with `chat and sched must use SEPARATE databases` | both DSNs point at one database. |
| Bundle load fails with an unknown key | your fleet predates a manifest field. Step 2. |
| First turn: `ErrImagePull` / `ImagePullBackOff` on `fleet-sandbox-…` | the sandbox image ref is not pullable *from the nodes*. Check the ref and `sandbox.kubernetes.imagePullSecret` — the control plane's own pull secret does not cover pods it creates. |
| `sandbox pod … not ready before start timeout` | scheduling (no node fits the sandbox requests — `sandbox.memory` / `.cpus` are requests *and* limits) or a slow first pull. `kubectl describe pod fleet-sandbox-…`. |
| Sandbox pods stuck `Pending` on volume | the workspace claim is ReadWriteOnce on a multi-node cluster. It must be RWX. |
| Workspace files owned by the wrong uid | the storage class must honor `fsGroup` (1000), or be provisioned world-writable at the root. Both the control plane and sandbox pods run uid/gid 1000. |
| MCP servers all dead in the log | the control-plane image has no `python3` or no `mcp` module — you built from fleet's generic example Containerfile instead of this repo's. |
| `view_file protocols/…` refused | `bundleDocsInImage` did not reach the pod. `kubectl logs deploy/larkspur \| grep -i 'bundle_docs_in_image\|supporting-doc'` prints exactly which roots fleet kept and dropped, and why. |
| `view_file protocols/…` returns not-found | the declaration landed but the image does not carry the docs — you deployed the base sandbox image, not the derived one. |
| A skill's `SKILL.md` cannot be opened in-sandbox | `kubectl logs deploy/larkspur \| grep 'stage skills'`. A warning means the control plane could not create `<workspace>/skills` in the claim (uid 1000 / `fsGroup`); no line at all means your fleet predates ADR-0055 — see the dated note in `manifest.yaml`. |
| `plugin_notes` is missing from `fleet mcp test --all` | the control-plane image lacks the `COPY plugins/` line, or the fleet predates ADR-0054 (it ignores `plugins/` silently). |
| A "sealed" turn reaches the internet | your CNI does not enforce NetworkPolicy. Step 8d. |
