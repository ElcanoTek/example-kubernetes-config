# Can I run this on kind or minikube?

**Yes on kind, and it is a genuinely useful rehearsal** — the same chart, the
same backend, the same fail-closed preflight, real sandbox pods created and
deleted per turn. It is *not* a small-scale production deployment, and four
things it does are wrong on purpose. Read those first, then follow the
walkthrough.

**minikube:** the chart and backend work there too — nothing in either is
kind-specific — but the image-loading step differs (`minikube image load`, or
`eval $(minikube docker-env)` before building) and its default storage class is
likewise ReadWriteOnce. Everything below applies with those substitutions. We
verify the kind path; minikube is untested here and we will not pretend
otherwise.

**Docker Desktop / OrbStack / Rancher Desktop Kubernetes:** untested. Same
shape, same caveats, different image-loading story.

---

## The four things this path gets wrong on purpose

These are the caveats, stated up front because each one is invisible while it
is fine and expensive when you promote it. They are all encoded in
[`deploy/kubernetes/values-kind.yaml`](../deploy/kubernetes/values-kind.yaml),
with the same comments, so the file and this page cannot drift.

1. **The lockdown seal depends on your CNI, so test it — never assume it.**
   fleet's preflight only requires the deny-all NetworkPolicy *object* to
   exist; whether it is *enforced* is the CNI's business, and fleet cannot
   tell the difference. kind's bundled `kindnetd` did **not** enforce
   NetworkPolicy in this validation (kindnetd v20260528, kind v0.32.0 /
   K8s v1.36.1: a sealed-label pod fetched an external page with the deny-all
   object in place); the standalone `aojea/kindnet` ≥ v1.3.0, Calico and
   Cilium do enforce. Beware the measurement trap that produced a false
   "sealed" reading during this doc's own validation: busybox `wget` has no
   TLS, so an `https://` test URL fails on an *unsealed* cluster too — always
   test with `http://` and always run the unlabeled control. `values-kind.yaml`
   ships `FLEET_DEFAULT_NETWORK_MODE: open` so the overlay never *claims* a
   seal your CNI may not deliver — run the seal test in "Verifying the seal"
   below, and flip to `lockdown` only after it blocks.
2. **The workspace claim is ReadWriteOnce.** kind's `standard` class (the
   local-path provisioner) offers nothing else. It works only because a
   one-node cluster puts the control plane and every sandbox pod on the same
   node. Add a worker node and sandbox pods start hanging on an unmountable
   volume — which reads as "fleet is slow" and is actually "your storage class
   is wrong". Production needs ReadWriteMany.
3. **The database is the chart's evaluation Postgres.** One replica, one PVC,
   no backups, and a password Helm generates into the `<release>-postgres`
   Secret. Fine for a walkthrough.
4. **There is no warm pool.** Every turn pays a cold pod start. This needs a
   fleet that understands `warmSize: 0` (ElcanoTek/fleet#1288); older fleet
   treated 0 as "derive from the concurrency cap" and silently ran a two-pod
   Guaranteed-QoS warm pool this file claims not to have.

Everything else — the credential boundary, the RBAC, the read-only rootfs, the
per-turn pod lifecycle, `bundle_docs_in_image`, the MCP servers running
host-side in the control-plane pod — is exactly what production does.

---

## Prerequisites

`kind`, `kubectl`, `helm`, `podman` or `docker`, Go ≥ 1.27, `openssl` (step 4
generates a token with it — see the trap noted there), a checkout of
`ElcanoTek/fleet`, an OpenRouter API key, and roughly 8 GB of memory available
to your container runtime. The two image builds take most of the time (the
sandbox image's Python data stack is ~1.3 GB).

```sh
export EKC=$(pwd)
export FLEET=/path/to/fleet
export TAG=dev
export NS=larkspur
```

---

## 1. Cluster

```sh
make kind-up
# = kind create cluster --name larkspur --config deploy/kubernetes/kind-cluster.yaml
kubectl cluster-info --context kind-larkspur
```

One node, deliberately — see caveat 2. The config file explains why in place.

## 2. Build both images locally

Same two-image story as production, tagged `localhost/` because they are
side-loaded rather than pulled:

```sh
make images FLEET="$FLEET" REGISTRY=localhost TAG="$TAG"
```

Do **not** skip the derived sandbox image in favor of the base. The base has no
`protocols/`, `personas/`, `system_prompts/` or `skills/` in it, and the whole
point of rehearsing on kind is to catch that class of mistake here.

## 3. Side-load them

```sh
make kind-load TAG="$TAG"
# = podman save localhost/larkspur-fleet:dev -o /tmp/...  && kind load image-archive ...
#   podman save localhost/larkspur-sandbox:dev -o /tmp/... && kind load image-archive ...
```

`values-kind.yaml` sets `image.pullPolicy: Never` for the control plane. You do
not need to do anything for the sandbox pods: fleet hard-codes
`imagePullPolicy: IfNotPresent` on every pod it creates
(`internal/sandbox/k8s_backend.go`), precisely so a side-loaded image works.

Verify both landed:

```sh
podman exec larkspur-control-plane crictl images | grep larkspur
```

## 4. Namespace and secret

Two values. The database URLs come from the chart's in-cluster Postgres, so
they are not needed here; `CHAT_SERVER_TOKEN` is, because fleet refuses to boot
without the shared chat token even when no web tier is running.

```sh
kubectl create namespace "$NS"
kubectl -n "$NS" create secret generic larkspur-secrets \
  --from-literal=OPENROUTER_API_KEY='sk-or-...' \
  --from-literal=CHAT_SERVER_TOKEN="$(openssl rand -hex 32)"
```

Then verify what actually landed — both traps below have bitten:

```sh
kubectl -n "$NS" get secret larkspur-secrets -o json \
  | python3 -c 'import json,sys,base64; d=json.load(sys.stdin)["data"]; \
      [print(k, "len", len(base64.b64decode(v))) for k,v in sorted(d.items())]'
# OPENROUTER_API_KEY: a real key is `sk-or-v1-` + 64 hex = 73 chars.
# CHAT_SERVER_TOKEN: 64 chars.
```

Two traps. If `openssl` is missing, the command substitution prints an error
**but the secret is still created** — with an empty `CHAT_SERVER_TOKEN`, and
fleet's boot failure will not point here. And a wrong `OPENROUTER_API_KEY`
(wrong shell variable, stray quotes) is worse: fleet before
ElcanoTek/fleet#1289 probed a public endpoint for its `model_api` check, so
`fleet validate-config` *blessed* a junk key and the first real turn then
failed with `401 Missing Authentication header`.

## 5. Install

```sh
make kind-install FLEET="$FLEET" TAG="$TAG"
```

which is:

```sh
helm upgrade --install larkspur "$FLEET/deploy/helm/fleet" \
  --namespace "$NS" --create-namespace \
  -f deploy/kubernetes/values-kind.yaml \
  --set image.repository=localhost/larkspur-fleet --set image.tag="$TAG" \
  --set sandbox.image="localhost/larkspur-sandbox:$TAG"
```

## 6. Watch it boot

```sh
kubectl -n "$NS" rollout status deploy/larkspur --timeout=5m
kubectl -n "$NS" logs deploy/larkspur | grep -Ei 'sandbox|preflight'
```

The line you are looking for:

```
sandbox: kubernetes backend preflight OK — apiserver v1.31.x, sandbox namespace "larkspur"
```

Postgres starts as a StatefulSet in the same namespace; the control plane may
restart once or twice while it waits for it. That is normal here and is one of
the reasons the chart's Postgres is evaluation-only.

## 7. The same four proofs as production

```sh
# a. API up
kubectl -n "$NS" port-forward svc/larkspur 8080:8080 &
curl -s localhost:8080/healthz

# b. MCP servers spawned (this is what the control-plane image exists for)
kubectl -n "$NS" exec deploy/larkspur -- fleet mcp test --all --deep

# c. a real turn creates and deletes a sandbox pod (costs one model call)
kubectl -n "$NS" get pods -l app.kubernetes.io/name=fleet-sandbox -w   # in another terminal
kubectl -n "$NS" exec -i deploy/larkspur -- sh -c 'cat > /tmp/smoke.yaml' <<'YAML'
prompt: |
  Two things, in the sandbox:
  1. Run `python3 -c "print(6*7)"` and report the number it printed.
  2. Read protocols/ask-the-runbooks.md with the view_file tool and quote its
     first heading. If view_file errors, say so explicitly, then fall back to
     `cat` and quote it that way.
model: "anthropic/claude-sonnet-4.5"
max_iterations: 8
YAML
kubectl -n "$NS" exec -it deploy/larkspur -- \
  fleet task run --workspace /var/lib/fleet/workspace/smoke /tmp/smoke.yaml
```

Check (b) as the production guide describes. For (c), the part that proves
the sandbox is the python line printing `42` — pod created, exec'd, deleted.
The `view_file protocols/…` leg currently fails on **every** backend for
scheduled/one-shot runs — an engine path-policy gap, not a declaration or
image problem (ElcanoTek/fleet#1290 tracks it: the task-run harness never
registers the workspace root, and the scheduled-run file-tool scope has no
supporting-doc exception). Until that lands, verify the declaration side
directly instead: the boot log prints
`bundle_docs_in_image declared: keeping fileop read anchors for 4 bundle doc
root(s)`, and an absolute-path read from inside a sandbox pod
(`kubectl exec <sandbox-pod> -- cat /opt/fleet/client/protocols/ask-the-runbooks.md`)
proves the image carries the docs. Once fleet#1290 is fixed, the production
guide's original two-outcome diagnosis applies: `view_file` falling back to
`cat` means the declaration did not land; not-found from both means the image
does not carry the docs.

The fourth proof is the seal test. Which brings us to:

---

## Verifying the seal on kind

Worth doing once, before you trust `lockdown` anywhere. First find out what
your CNI does. Do not trust folklore about which versions enforce — this
doc's own validation first concluded the seal was real from an `https://`
test (busybox TLS failure mimicking a block) and was refuted by the exact
commands below on a fresh cluster:

```sh
# http, not https: busybox wget has no TLS, so an https URL fails for TLS
# reasons even on an UNSEALED cluster and fakes a passing seal test.
kubectl -n "$NS" run seal-test --restart=Never --rm -it \
  --labels=app.kubernetes.io/name=fleet-sandbox,fleet.elcanotek.com/egress=none \
  --image=busybox -- wget -T 5 -q -O- http://example.com && echo "NOT SEALED"
```

If that times out **and** the control run — the same command **without** the
labels — prints the page, your CNI enforces the deny-all policy: the seal is
real, and you can set `FLEET_DEFAULT_NETWORK_MODE: lockdown` in
`values-kind.yaml` and redo step 5. Both halves are required: the sealed run
alone cannot distinguish a policy block from a broken network or a TLS-less
client.

If the sealed run prints the page and `NOT SEALED` — which is what kind's
bundled kindnetd does as of v20260528 — recreate the cluster without kindnet
and install a policy-enforcing CNI:

```sh
make kind-down
```

Uncomment the `networking.disableDefaultCNI: true` lines in
`deploy/kubernetes/kind-cluster.yaml`, then:

```sh
make kind-up
# nodes stay NotReady until a CNI is installed — that is expected
kubectl apply -f https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/calico.yaml
kubectl -n kube-system rollout status daemonset/calico-node --timeout=5m
kubectl get nodes    # now Ready
```

Then set `FLEET_DEFAULT_NETWORK_MODE: lockdown` in `values-kind.yaml`, redo
steps 3–6, and re-run the seal test above — with Calico enforcing, it times
out.

Either way, seeing both outcomes — sealed pod blocked, control pod through —
on the same cluster is the fastest way to understand what fleet's preflight
does and does not promise: it checks the policy *object* exists; the CNI
decides whether the policy *acts*.

Pin the Calico version you install; `latest` manifests move.

---

## Teardown

```sh
make kind-down   # kind delete cluster --name larkspur
```

That takes the PVCs with it — unlike the production teardown, where the chart's
`helm.sh/resource-policy: keep` deliberately preserves them.

---

## Known rough edges on kind

| Symptom | Why |
| --- | --- |
| Control plane `CrashLoopBackOff` early on | it is waiting for the evaluation Postgres. Give it a few restarts; `kubectl -n larkspur logs deploy/larkspur --previous` confirms. |
| `ErrImagePull` on `fleet-sandbox-…` | the sandbox image was not side-loaded, or was loaded under a different tag than `sandbox.image`. `crictl images` on the node (step 3) is the check. |
| Sandbox pod `Pending`, "pod has unbound immediate PersistentVolumeClaims" | you added a worker node. Caveat 2. |
| Everything is slow | no warm pool (caveat 4) plus a cold Python import on every first `run_python`. Both are real on kind and both are configurable in production. |
| `kind load image-archive` fails on a 1.3 GB tar | disk pressure in the container runtime's VM. `podman system prune` / raise the VM disk. |
| The seal test passes traffic | kind's bundled kindnetd does not enforce NetworkPolicy (observed on v20260528 / kind v0.32.0) — install Calico or Cilium (see above). If it *times out* but you tested `https://`, that is the opposite trap: busybox wget cannot do TLS, so https fakes a **sealed** result on an unsealed cluster; test with `http://` and run the control. |
| `kubectl cp` to the control-plane pod fails | the image ships no `tar`, which `kubectl cp` requires. Stream instead: `kubectl -n larkspur exec -i deploy/larkspur -- sh -c 'cat > /tmp/file' < file`. |
| The whole cluster is gone after a host reboot | the kind "node" is just a container. `podman start larkspur-control-plane` (or `docker start`) brings it back; pods restart on their own. |
