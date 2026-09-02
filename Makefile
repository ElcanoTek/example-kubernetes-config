# example-kubernetes-config — one entry point for the things this bundle builds.
#
# Nothing here is magic: every target is the exact command the docs show, kept
# in one place so the docs and the build cannot drift. Run `make` for the list.
#
# The two variables you will actually set:
#   FLEET=/path/to/fleet     a checkout of ElcanoTek/fleet at the commit you
#                            intend to run. Needed for the control-plane binary
#                            and for anything touching the Helm chart — this
#                            repo deliberately vendors neither.
#   REGISTRY=ghcr.io/you     where the two images go. Sandbox NODES pull from
#                            it, so `localhost/...` only works on kind.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# ── knobs ───────────────────────────────────────────────────────────────────
FLEET       ?=
REGISTRY    ?= localhost
TAG         ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
RELEASE     ?= larkspur
NAMESPACE   ?= larkspur
KIND_CLUSTER?= larkspur
ENGINE      ?= podman
PY          ?= python3
VENV        := .venv

CP_IMAGE    := $(REGISTRY)/larkspur-fleet:$(TAG)
SB_BASE     := localhost/larkspur-sandbox-base:$(TAG)
SB_IMAGE    := $(REGISTRY)/larkspur-sandbox:$(TAG)
CHART       := $(FLEET)/deploy/helm/fleet

# Guard: a target that cannot work without a fleet checkout says so instead of
# failing three commands later with a confusing path error.
define need_fleet
	@test -n "$(FLEET)" || { echo "error: set FLEET=/path/to/fleet (a checkout of ElcanoTek/fleet)"; exit 2; }
	@test -d "$(FLEET)/deploy/helm/fleet" || { echo "error: $(FLEET) does not look like a fleet checkout (no deploy/helm/fleet)"; exit 2; }
endef

# ── python: the MCP servers ─────────────────────────────────────────────────
.PHONY: venv
venv: ## create .venv and install runtime + dev deps
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip
	$(VENV)/bin/pip install --quiet -r mcp/requirements.txt -r mcp/requirements-dev.txt

.PHONY: test
test: ## run the MCP server + manifest tests (excludes the `expensive` marker)
	$(VENV)/bin/python -m pytest mcp/ -m 'not expensive' -q

.PHONY: lint
lint: ## ruff check + format --check over mcp/ and plugins/
	$(VENV)/bin/python -m ruff check mcp/ plugins/
	$(VENV)/bin/python -m ruff format --check mcp/ plugins/

# ── images ──────────────────────────────────────────────────────────────────
.PHONY: fleet-bin
fleet-bin: ## build the fleet binary from $FLEET into the build context
	$(call need_fleet)
	cd "$(FLEET)" && CGO_ENABLED=0 go build \
	  -ldflags "-X github.com/ElcanoTek/fleet/internal/version.version=$$(cat VERSION)" \
	  -o "$(CURDIR)/deploy/kubernetes/fleet" ./cmd/fleet
	@echo "staged $$(du -h deploy/kubernetes/fleet | cut -f1) binary at deploy/kubernetes/fleet (gitignored)"

.PHONY: control-plane
control-plane: fleet-bin ## build the control-plane image ($CP_IMAGE)
	$(ENGINE) build -t "$(CP_IMAGE)" -f deploy/kubernetes/Containerfile.control-plane .

.PHONY: sandbox-base
sandbox-base: ## build the base sandbox image from sandbox/Containerfile
	$(ENGINE) build --pull=newer -t "$(SB_BASE)" -f sandbox/Containerfile sandbox/

.PHONY: sandbox
sandbox: sandbox-base ## build the sandbox image with the bundle docs baked in ($SB_IMAGE)
	$(ENGINE) build -t "$(SB_IMAGE)" \
	  --build-arg BASE="$(SB_BASE)" \
	  -f deploy/kubernetes/Containerfile.sandbox .

.PHONY: images
images: control-plane sandbox ## build BOTH images from this one commit
	@echo "built $(CP_IMAGE)"
	@echo "built $(SB_IMAGE)"

.PHONY: push
push: ## push both images to $REGISTRY
	$(ENGINE) push "$(CP_IMAGE)"
	$(ENGINE) push "$(SB_IMAGE)"

# ── helm ────────────────────────────────────────────────────────────────────
# CI cannot run these — it has no fleet checkout — so they are the LOCAL gate.
# A misspelled values key renders nothing and Helm never complains (the chart
# ships no values schema), which is why `helm-template` prints the env block:
# reading it is the check.
.PHONY: helm-lint
helm-lint: ## lint fleet's chart against the production overlay
	$(call need_fleet)
	helm lint "$(CHART)" -f deploy/kubernetes/values-example.yaml \
	  --set image.repository="$(REGISTRY)/larkspur-fleet" --set image.tag="$(TAG)" \
	  --set sandbox.image="$(SB_IMAGE)"

.PHONY: helm-template
helm-template: helm-lint ## render the chart and print the control-plane env block
	helm template "$(RELEASE)" "$(CHART)" --namespace "$(NAMESPACE)" \
	  -f deploy/kubernetes/values-example.yaml \
	  --set image.repository="$(REGISTRY)/larkspur-fleet" --set image.tag="$(TAG)" \
	  --set sandbox.image="$(SB_IMAGE)" \
	  | sed -n '/^          env:/,/^          envFrom:/p'

.PHONY: install
install: ## helm upgrade --install with the production overlay
	$(call need_fleet)
	helm upgrade --install "$(RELEASE)" "$(CHART)" \
	  --namespace "$(NAMESPACE)" --create-namespace \
	  -f deploy/kubernetes/values-example.yaml \
	  --set image.repository="$(REGISTRY)/larkspur-fleet" --set image.tag="$(TAG)" \
	  --set sandbox.image="$(SB_IMAGE)"

# ── kind ────────────────────────────────────────────────────────────────────
.PHONY: kind-up
kind-up: ## create the local kind cluster
	kind create cluster --name "$(KIND_CLUSTER)" --config deploy/kubernetes/kind-cluster.yaml

.PHONY: kind-load
kind-load: ## side-load both images into the kind cluster
	$(ENGINE) save "localhost/larkspur-fleet:$(TAG)" -o /tmp/larkspur-cp.tar
	kind load image-archive /tmp/larkspur-cp.tar --name "$(KIND_CLUSTER)"
	$(ENGINE) save "localhost/larkspur-sandbox:$(TAG)" -o /tmp/larkspur-sb.tar
	kind load image-archive /tmp/larkspur-sb.tar --name "$(KIND_CLUSTER)"
	rm -f /tmp/larkspur-cp.tar /tmp/larkspur-sb.tar

.PHONY: kind-install
kind-install: ## helm install into kind with the evaluation overlay
	$(call need_fleet)
	helm upgrade --install "$(RELEASE)" "$(CHART)" \
	  --namespace "$(NAMESPACE)" --create-namespace \
	  -f deploy/kubernetes/values-kind.yaml \
	  --set image.repository=localhost/larkspur-fleet --set image.tag="$(TAG)" \
	  --set sandbox.image="localhost/larkspur-sandbox:$(TAG)"

.PHONY: kind-down
kind-down: ## delete the kind cluster (and everything in it)
	kind delete cluster --name "$(KIND_CLUSTER)"

# ── misc ────────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## remove the staged binary and python caches
	rm -f deploy/kubernetes/fleet
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache

.PHONY: help
help: ## show this list
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'
