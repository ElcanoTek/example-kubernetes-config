# deploy/kubernetes

Everything a Kubernetes deployment of this bundle needs that the bundle itself
is not. The walkthrough that uses all of it is
[`../../docs/KUBERNETES-GETTING-STARTED.md`](../../docs/KUBERNETES-GETTING-STARTED.md);
this page is the reference for what each file is and when it has to change.

**What is deliberately NOT here:** fleet's Helm chart. It lives in the fleet
repo (`deploy/helm/fleet`) and you install it from a checkout. Vendoring a copy
would put an engine version into bundle data and guarantee it goes stale — the
same reason `Containerfile.control-plane` consumes a `fleet` binary you built
rather than pinning a fleet ref.

| File | What it is | Changes when |
| --- | --- | --- |
| `Containerfile.control-plane` | the `fleet` binary + `python3` + `mcp/requirements.txt` + this bundle baked at `/opt/fleet/client`. Build context: the **repo root**. | this bundle gains a directory fleet reads, or the runtime needs a new system package |
| `Containerfile.sandbox` | the base sandbox image (`../../sandbox/Containerfile`) plus a read-only snapshot of `protocols/`, `personas/`, `system_prompts/`, `skills/` at the same absolute paths. Build context: the **repo root**. | the bundle gains a directory the agent reads *from inside a sandbox* |
| `values-example.yaml` | the documented production overlay for fleet's chart | your cluster's storage class, pull secret, network mode, or sizing changes |
| `values-kind.yaml` | the evaluation overlay for a local kind cluster, with its four degradations named in place | rarely — it exists to be read, not tuned |
| `kind-cluster.yaml` | a one-node kind cluster (and why one node) | you want the policy-enforcing-CNI variant |
| `fleet` | the staged control-plane binary. **Gitignored.** `make fleet-bin FLEET=…` puts it here. | never — it is a build artifact |

## The two-image rule

Both images carry a copy of this bundle:

- the **control-plane image is authoritative** — manifest, agent policy,
  prompts, the MCP servers that actually run;
- the **sandbox image carries a read-only snapshot** of the doc dirs, so the
  agent can read its own protocols from inside a pod.

Build them from the **same commit of this repo** and roll them together, or an
agent reads one release's protocols while the control plane runs another's.
Nothing enforces that. `make images` builds both from one commit so the easy
path is the correct one; that is the whole mitigation.

## Bake AND declare — both, or neither works

Baking the docs into the sandbox image fixes `bash` and `run_python` (the
workspace symlinks fleet drops are absolute, and now they resolve inside the
pod). It does **not** fix the file tools. fleet's fileop path anchor refuses any
root that is not actually mounted, and a pod mounts only the workspace claim —
so `view_file protocols/foo.md` is refused *before the file is looked for*
unless the deployment declares that the image carries those roots:

- `sandbox.kubernetes.bundleDocsInImage: true` in `values-example.yaml`
  (rendered by the chart as `FLEET_SANDBOX_K8S_BUNDLE_DOCS_IN_IMAGE`), and
- `sandbox.kubernetes.bundle_docs_in_image: true` in `../../manifest.yaml`,
  which is what keeps a chart-less deployment correct. Env wins; the two must
  agree.

| | file tools (`view_file`) | `bash` / `run_python` |
| --- | --- | --- |
| neither bake nor declare | refused (anchor) | not found |
| bake, do not declare | **refused (anchor)** | works |
| declare, do not bake | attempted, **not found** | not found |
| bake **and** declare | works | works |

Writes beneath those roots are refused in every row: the anchors are re-admitted
**read-only**, so a turn cannot rewrite its own protocols. That asymmetry is
intentional.

It is a **declaration, not a probe** — fleet cannot inspect an image's
contents. It also cannot widen anything: it only re-admits read-only anchors for
paths the operator already configured, and the read still executes inside the
sandbox.

## Two values files, on purpose

`values-example.yaml` is production: ReadWriteMany workspace, managed Postgres,
`lockdown` egress, a registry pull secret, sizing for real traffic.

`values-kind.yaml` is not that file with the registry swapped out. It trades
away four things and says so in place: the seal is fake (kindnet does not
enforce NetworkPolicy), the workspace is ReadWriteOnce (single node only), the
database is the chart's evaluation Postgres, and there is no warm pool. Never
promote it.

## Checking a values change

fleet's chart ships **no values schema**. Helm accepts a misspelled key
silently and renders nothing for it, so `helm lint` passing proves very little.
Render and read:

```sh
make helm-template FLEET=/path/to/fleet REGISTRY=ghcr.io/your-org TAG=$(git rev-parse --short HEAD)
```

CI cannot do this — it has no fleet checkout — so it is a local gate, and the
PR template asks you to paste what you saw.

## Verbs the runner Role needs

The chart's `<release>-runner` Role must grant, in the sandbox namespace:

- `pods`: `create`, `get`, `list`, `delete`
- `pods/exec`: `create` **and `get`**
- `persistentvolumeclaims`: `get`
- `networkpolicies` (networking.k8s.io): `get`
- plus a cluster-scoped `runtimeclasses: get` when you set a `runtimeClass`

`get` on `pods/exec` is the one people write their own RBAC without. fleet
streams exec over a WebSocket upgrade, which is an HTTP `GET`, and the
apiserver derives the verb from the method — so a Role with only `create`
passes the boot preflight and 403s on the first `bash` call.
