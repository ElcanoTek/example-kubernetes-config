#!/usr/bin/env python3
"""Release Tracker MCP Server — the CREDENTIAL-GATED connector pattern.

A generic REST connector over a fictional release-tracking API. It registers
but stays DARK until ``DEPLOY_API_TOKEN`` is set, so a fresh checkout of this
bundle runs clean with no secrets at all.

THE CREDENTIAL BOUNDARY, on both deployment shapes. The manifest names the
variable, never the value. fleet holds the secret host-side and injects it only
when it runs a delegated MCP call — on the Kubernetes path "host-side" means
*inside the control-plane pod*, and sandbox pods receive no env, no secret
mounts and no service-account token. Nothing in this file ever reaches a
sandbox.

WHERE OUTPUT GOES. ``DEPLOY_API_OUTPUT_DIR`` is mapped in the manifest to
``${FLEET_WORKSPACE}/outputs`` — a reserved runtime token fleet substitutes at
subprocess launch, and DROPS entirely on a spawn path with no workspace to
offer. So this server treats it as optional and degrades gracefully.

That mapping matters far more on a cluster than on a box. The control-plane
pod's ``$HOME`` is ephemeral and invisible to sandbox pods, so a connector that
writes a receipt to ``~/something`` produces an artifact no agent can ever read.
The workspace claim is the one filesystem the control plane and every sandbox
pod share, at the same absolute path. Never default a connector's output
directory to a home-relative path.

Runs over stdio.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("release_tracker")

DEFAULT_BASE_URL = "https://releases.example.com/api/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _base_url() -> str:
    return (os.environ.get("DEPLOY_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    raw = os.environ.get("DEPLOY_API_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("DEPLOY_API_TIMEOUT_SECONDS=%r is not a number; using the default", raw)
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _output_dir() -> str | None:
    """The workspace directory for receipts, or None when fleet offered none.

    An unset value is the normal case on a spawn path with no workspace — not
    an error. Callers skip the receipt and say so in their result.
    """
    raw = (os.environ.get("DEPLOY_API_OUTPUT_DIR") or "").strip()
    return raw or None


def _headers() -> dict[str, str]:
    token = os.environ.get("DEPLOY_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """A model-readable error. Never includes the token, in any form."""
    return {"ok": False, "error": message, **extra}


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    if not os.environ.get("DEPLOY_API_TOKEN"):
        return _error(
            "DEPLOY_API_TOKEN is not set, so this connector is not authenticated. "
            "On a cluster it belongs in the deployment Secret named by "
            "config.existingSecret; on a box, in the .env file."
        )
    if path.startswith(("http://", "https://")) or ".." in path:
        return _error(f"refusing suspicious path {path!r}: pass a resource path, not a URL")

    url = f"{_base_url()}/{path.lstrip('/')}"
    try:
        response = httpx.request(method, url, headers=_headers(), timeout=_timeout(), **kwargs)
    except httpx.RequestError as exc:
        # Note what a cluster failure here usually means, since it is the least
        # obvious: the control-plane pod's egress, not the sandbox's.
        return _error(
            f"request to the release tracker failed: {exc}. On Kubernetes this call leaves the "
            "CONTROL-PLANE pod, so check that pod's egress — sandbox NetworkPolicies do not apply."
        )
    if response.status_code >= 400:
        return _error(f"release tracker returned HTTP {response.status_code}", status=response.status_code)
    try:
        return {"ok": True, "data": response.json()}
    except ValueError:
        return _error("release tracker returned a non-JSON body")


def _write_receipt(kind: str, payload: dict[str, Any], result: dict[str, Any]) -> str | None:
    """Write a JSON receipt of a write into the workspace. Returns its path, or None."""
    out = _output_dir()
    if not out:
        return None
    try:
        directory = Path(out)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = directory / f"{kind}-{stamp}.json"
        target.write_text(
            json.dumps({"kind": kind, "submitted": payload, "result": result}, indent=2),
            encoding="utf-8",
        )
        return str(target)
    except OSError as exc:
        logger.warning("could not write receipt to %s: %s", out, exc)
        return None


@mcp.tool()
def rt_list_releases(service: str, limit: int = 20) -> dict[str, Any]:
    """List recent deployments of a service, newest first.

    Args:
        service: the service name as the tracker knows it.
        limit: maximum releases to return (1-100).
    """
    limit = max(1, min(int(limit), 100))
    return _request("GET", f"services/{service}/releases", params={"limit": limit})


@mcp.tool()
def rt_get_release(release_id: str) -> dict[str, Any]:
    """Read one deployment in full by its id."""
    return _request("GET", f"releases/{release_id}")


@mcp.tool()
def rt_open_change_request(service: str, summary: str, detail: str = "") -> dict[str, Any]:
    """Open a change request against a service. THIS WRITES.

    Listed in the manifest's agent_policy.critical_tools (by the bare suffix
    ``open_change_request``), so fleet holds the call for an explicit audit
    confirmation before it runs, with a per-tool approval window from
    critical_tool_timeouts. Do not add a write tool without adding it there in
    the same PR.

    Args:
        service: the service the change targets.
        summary: a one-line summary. Required.
        detail: optional longer description.
    """
    if not summary.strip():
        return _error("summary is required")
    payload = {"service": service, "summary": summary, "detail": detail}
    result = _request("POST", "change-requests", json=payload)
    receipt = _write_receipt("change-request", payload, result)
    if receipt:
        result["receipt_path"] = receipt
    else:
        result["receipt"] = "not written — no workspace directory was offered for this run"
    return result


if __name__ == "__main__":
    logger.info(
        "release_tracker: base_url=%s authenticated=%s output_dir=%s",
        _base_url(),
        bool(os.environ.get("DEPLOY_API_TOKEN")),
        _output_dir() or "(none)",
    )
    mcp.run()
