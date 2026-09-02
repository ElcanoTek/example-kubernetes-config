"""Contract tests between manifest.yaml, the servers, and deploy/kubernetes/.

These are the checks that keep a two-image Kubernetes bundle honest, and there
is nothing clever in any of them — each one exists because the failure it
catches is silent:

* A tool renamed in code but still allowlisted in the manifest is dropped by
  fleet at boot with no error.
* An env key a server reads but the manifest never maps never reaches the
  subprocess: fleet spawns stdio servers with a MINIMAL environment
  (PATH/HOME/locale plus exactly the manifest's env map), so an operator
  ``export`` does nothing.
* A directory the agent reads from inside a sandbox that
  ``Containerfile.sandbox`` does not bake resolves on the single-box podman
  install and fails only on a cluster — except ``skills/``, which fleet stages
  into the workspace claim itself (ADR-0055) and which must therefore NOT be
  baked (a baked copy is a snapshot nothing reads).
* A directory fleet reads at all that ``Containerfile.control-plane`` does not
  copy is simply absent from the control-plane image, because that file
  enumerates its COPY lines rather than using ``COPY . .``.
* ``bundle_docs_in_image`` set in one of its two homes and not the other is a
  refused ``view_file`` in production and a green test suite here.

None of these need a cluster, a fleet checkout, or a network. They are the part
of the deployment CI *can* verify — the Helm rendering is a local gate, see
AGENTS.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "manifest.yaml"
CF_CONTROL_PLANE = REPO / "deploy" / "kubernetes" / "Containerfile.control-plane"
CF_SANDBOX = REPO / "deploy" / "kubernetes" / "Containerfile.sandbox"
VALUES_PROD = REPO / "deploy" / "kubernetes" / "values-example.yaml"
VALUES_KIND = REPO / "deploy" / "kubernetes" / "values-kind.yaml"

# Directories the AGENT reads from inside a sandbox that reach a pod ONLY via
# the sandbox image. Every one of these must be baked into it, or the read
# fails on the cluster path only. skills/ is deliberately absent: fleet stages
# the merged skills tree into the workspace claim at boot (ADR-0055).
SANDBOX_DOC_DIRS = ("protocols", "personas", "system_prompts")

# Directories FLEET reads out of the bundle at all. Every one must be copied
# into the control-plane image.
BUNDLE_DIRS = (
    "mcp",
    "personas",
    "protocols",
    "prompts",
    "skills",
    "plugins",
    "system_prompts",
    "evals",
    "assets",
    "sandbox",
)

PLUGINS_DIR = REPO / "plugins"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _copy_sources(containerfile: Path) -> set[str]:
    """Every COPY source path in a Containerfile, normalized without a trailing slash."""
    sources = set()
    for line in containerfile.read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        parts = line.split()
        # COPY <src>... <dest> — this repo's Containerfiles use exactly one src.
        src = parts[1]
        sources.add(src.rstrip("/"))
    return sources


# ── manifest ↔ code ─────────────────────────────────────────────────────────


def test_declared_server_scripts_exist(manifest):
    for server in manifest["mcp_servers"]:
        if server.get("type", "stdio") != "stdio":
            continue
        for arg in server.get("args", []):
            if arg.endswith(".py"):
                assert (REPO / arg).is_file(), f"{server['name']}: {arg} does not exist"


def test_every_declared_tool_exists_in_its_server(manifest):
    """A tool named in agent_policy must exist in the server's source.

    Matched by the ``mcp_<server>_<tool>`` runtime naming fleet uses. A rename
    in code that forgets the manifest is dropped silently at boot; this makes it
    a red test instead.
    """
    server_sources = {
        s["name"]: (REPO / s["args"][0]).read_text(encoding="utf-8")
        for s in manifest["mcp_servers"]
        if s.get("type", "stdio") == "stdio" and s.get("args")
    }
    for runtime_name in manifest["agent_policy"]["parallel_safe_tools"]:
        assert runtime_name.startswith("mcp_"), runtime_name
        for server, source in server_sources.items():
            prefix = f"mcp_{server}_"
            if runtime_name.startswith(prefix):
                tool = runtime_name[len(prefix) :]
                assert re.search(rf"^def {re.escape(tool)}\(", source, re.M), (
                    f"{runtime_name}: {server} defines no tool named {tool}"
                )
                break
        else:
            pytest.fail(f"{runtime_name} names no server in mcp_servers")


def test_critical_tools_are_real_write_tools(manifest):
    """Every critical_tools suffix must match a tool some server actually defines."""
    sources = "\n".join(
        (REPO / s["args"][0]).read_text(encoding="utf-8")
        for s in manifest["mcp_servers"]
        if s.get("type", "stdio") == "stdio" and s.get("args")
    )
    for suffix in manifest["agent_policy"]["critical_tools"]:
        assert re.search(rf"^def \w*{re.escape(suffix)}\(", sources, re.M), (
            f"critical_tools names {suffix!r}, which no server defines"
        )


def test_manifest_names_variables_never_values(manifest):
    """Credential env values are ${VAR} references, never literals."""
    for server in manifest["mcp_servers"]:
        for key, value in (server.get("env") or {}).items():
            if any(t in key for t in ("TOKEN", "KEY", "SECRET", "PASSWORD")):
                assert value.startswith("${"), f"{server['name']}.{key} is not a ${{VAR}} reference"


# ── manifest ↔ deployment ───────────────────────────────────────────────────


def test_bundle_docs_in_image_agrees_across_its_two_homes(manifest):
    """The manifest and the production values overlay must not disagree.

    fleet's env (the chart's value) wins, so a disagreement is not a boot
    failure — it is a bundle that behaves differently depending on whether it
    was installed with the chart. Make them agree.
    """
    from_manifest = manifest["sandbox"]["kubernetes"]["bundle_docs_in_image"]
    values = yaml.safe_load(VALUES_PROD.read_text(encoding="utf-8"))
    from_values = values["sandbox"]["kubernetes"]["bundleDocsInImage"]
    assert from_manifest == from_values, (
        "manifest sandbox.kubernetes.bundle_docs_in_image and values sandbox.kubernetes.bundleDocsInImage disagree"
    )


def test_sandbox_image_bakes_every_dir_the_agent_reads(manifest):
    """Anything the agent reads in-sandbox must be in Containerfile.sandbox."""
    baked = _copy_sources(CF_SANDBOX)
    for d in SANDBOX_DOC_DIRS:
        assert d in baked, (
            f"deploy/kubernetes/Containerfile.sandbox does not COPY {d}/ — "
            "in-sandbox reads of it will fail on the cluster path"
        )


def test_sandbox_image_does_not_bake_the_connectors(manifest):
    """mcp/ and manifest.yaml must never be readable from a sandbox."""
    baked = _copy_sources(CF_SANDBOX)
    assert "mcp" not in baked, "the connectors must not be reachable from a sandbox"
    assert "manifest.yaml" not in baked, (
        "the manifest names every connector's env contract; it must not be one `cat` away from model-authored code"
    )


def test_control_plane_image_copies_every_bundle_dir(manifest):
    """Containerfile.control-plane enumerates its COPYs, so a new dir needs a line."""
    copied = _copy_sources(CF_CONTROL_PLANE)
    assert "manifest.yaml" in copied
    for d in BUNDLE_DIRS:
        if not (REPO / d).is_dir():
            continue
        assert d in copied, (
            f"deploy/kubernetes/Containerfile.control-plane does not COPY {d}/ — "
            "it will be absent from the control-plane image"
        )


def test_both_images_use_the_same_bundle_path():
    """The baked doc paths must equal FLEET_CLIENT_CONFIG_DIR, byte for byte.

    The workspace symlinks fleet drops are absolute; a sandbox pod resolves them
    against its own filesystem, so a one-character difference here is a dangling
    symlink and a read that fails not-found.
    """
    cp = CF_CONTROL_PLANE.read_text(encoding="utf-8")
    sb = CF_SANDBOX.read_text(encoding="utf-8")
    match = re.search(r"FLEET_CLIENT_CONFIG_DIR=(\S+)", cp)
    assert match, "control-plane image does not set FLEET_CLIENT_CONFIG_DIR"
    root = match.group(1).rstrip("/")
    for d in SANDBOX_DOC_DIRS:
        assert f"{root}/{d}/" in sb, f"sandbox image does not bake {d}/ under {root}/"
        assert f"{root}/{d}/" in cp, f"control-plane image does not place {d}/ under {root}/"


def test_sandbox_image_does_not_bake_skills(manifest):
    """skills/ is staged into the workspace claim by fleet, never baked.

    On the kubernetes backend fleet re-materializes the merged skills tree
    (built-in pack + plugins/*/skills + skills/) at <workspace root>/skills and
    mounts it read-only into every pod (fleet ADR-0055). A `COPY skills/` line
    in Containerfile.sandbox would be a snapshot nothing reads, and its
    presence would tempt the next reader into believing the image is how skills
    get there. The pack stays inherited for the same reason: the staged tree is
    what makes that work on a cluster, and the README says so.
    """
    assert "skills" not in _copy_sources(CF_SANDBOX), (
        "deploy/kubernetes/Containerfile.sandbox COPYs skills/ — fleet stages skills into the "
        "workspace claim itself (ADR-0055); drop the line. See README.md 'Skills on Kubernetes'."
    )
    assert manifest.get("skills_builtin", True) is True, (
        "manifest skills_builtin is false, but README.md and the getting-started guide say this "
        "bundle inherits the built-in pack; change both in the same PR or restore the default"
    )


# ── plugins ─────────────────────────────────────────────────────────────────


def _plugin_dirs() -> list[Path]:
    return sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir())


def test_bundle_ships_at_least_one_plugin():
    """The example plugin is part of this template's contract, like the skills."""
    assert _plugin_dirs(), "plugins/ has no plugin directories"


def test_every_plugin_has_a_valid_manifest_and_self_contained_servers():
    """plugin.json + mcp.json parse, and every stdio server script exists in the plugin.

    fleet rejects a plugin whose manifest is invalid and skips a server entry
    whose ${PLUGIN_ROOT} path does not exist — both as advisories, never as a
    boot failure — so this is the only place they turn red.
    """
    for plugin in _plugin_dirs():
        manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
        assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", plugin.name
        assert manifest["name"] == plugin.name, f"{plugin.name}: plugin.json name must equal the folder name"
        mcp_path = plugin / "mcp.json"
        if not mcp_path.exists():
            continue
        mcp_json = json.loads(mcp_path.read_text(encoding="utf-8"))
        for server, entry in mcp_json["mcpServers"].items():
            if entry.get("type") != "stdio":
                continue
            for arg in entry.get("args", []):
                if arg.startswith("${PLUGIN_ROOT}/"):
                    rel = arg[len("${PLUGIN_ROOT}/") :]
                    assert (plugin / rel).is_file(), f"{plugin.name}/{server}: {arg} does not exist in the plugin"
            for key, value in (entry.get("env") or {}).items():
                assert not value.startswith("${") or value.startswith(("${PLUGIN_ROOT}", "${PLUGIN_DATA}")), (
                    f"{plugin.name}/{server}.{key}: only PLUGIN_ROOT/PLUGIN_DATA expand in a plugin; "
                    "a credential belongs in manifest.yaml, not in a plugin"
                )


def test_plugin_servers_need_nothing_the_control_plane_image_lacks():
    """A plugin server runs in the control-plane pod, so its imports must be
    satisfied by mcp/requirements.txt — the file Containerfile.control-plane
    installs. This checks the one import family the example uses."""
    runtime = (REPO / "mcp" / "requirements.txt").read_text(encoding="utf-8")
    for plugin in _plugin_dirs():
        for script in (plugin / "server").glob("*.py") if (plugin / "server").is_dir() else []:
            source = script.read_text(encoding="utf-8")
            if "from mcp.server.fastmcp import FastMCP" in source:
                assert re.search(r"^mcp[>=<]", runtime, re.M), (
                    f"{script}: imports mcp but requirements.txt does not pin it"
                )


def test_kind_overlay_never_claims_a_seal_kindnet_cannot_enforce():
    """values-kind.yaml must not set lockdown: kindnet does not enforce NetworkPolicy."""
    values = yaml.safe_load(VALUES_KIND.read_text(encoding="utf-8"))
    mode = values["config"]["env"]["FLEET_DEFAULT_NETWORK_MODE"]
    assert mode != "lockdown", (
        "values-kind.yaml sets lockdown, which kindnet cannot enforce — the seal would be "
        "claimed and not delivered. Use `open`, or document the Calico path."
    )
    assert mode != "allowlisted", "allowlisted is refused at boot under the kubernetes backend"


def test_production_overlay_selects_the_kubernetes_backend():
    values = yaml.safe_load(VALUES_PROD.read_text(encoding="utf-8"))
    assert values["sandbox"]["backend"] == "kubernetes"
    assert values["config"]["env"]["FLEET_DEFAULT_NETWORK_MODE"] in ("lockdown", "open")


def test_manifest_does_not_pin_the_sandbox_backend(manifest):
    """The backend is a deployment property; pinning it here breaks the podman path."""
    assert "backend" not in manifest["sandbox"], (
        "manifest.yaml pins sandbox.backend — this bundle would then refuse to boot on any "
        "single box and break `fleet mcp test` locally. Let the chart's env set it."
    )


# ── manifest ↔ personas ─────────────────────────────────────────────────────


def test_every_persona_file_parses():
    """A persona that does not parse takes the WHOLE bundle down at boot.

    fleet's loader is strict, and a persona is loaded at startup along with the
    manifest — so one stray YAML indicator in a bullet is not a degraded
    persona, it is a control plane that will not serve. Nothing else in this
    repo reads these files.
    """
    for path in sorted((REPO / "personas").glob("*.yaml")):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict), f"{path.name} is not a YAML mapping"
        assert loaded.get("name"), f"{path.name} has no name:"
        assert loaded.get("role"), f"{path.name} has no role:"


def test_every_persona_named_anywhere_resolves_to_a_file(manifest):
    """A persona is selected by FILE BASENAME, in three places, checked in none.

    `personas[].name` in the manifest carries the least-privilege tool gate; a
    task template's `persona:` and an eval case's `persona:` pick who runs. All
    three are basenames of personas/<name>.yaml. Rename the file and none of
    them error — the gate simply stops applying and the task quietly runs as the
    default persona, with more tools than it was meant to see.
    """
    named = {entry["name"] for entry in (manifest.get("personas") or [])}
    named |= {t["task"]["persona"] for t in (manifest.get("task_templates") or []) if t["task"].get("persona")}
    for eval_set in sorted((REPO / "evals").glob("*.yaml")):
        doc = yaml.safe_load(eval_set.read_text(encoding="utf-8"))
        named |= {c["persona"] for c in (doc.get("cases") or []) if c.get("persona")}

    for basename in sorted(named):
        assert (REPO / "personas" / f"{basename}.yaml").is_file(), (
            f"persona {basename!r} is named in the bundle but personas/{basename}.yaml does not exist"
        )
