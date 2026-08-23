#!/usr/bin/env python3
"""Runbook Library MCP Server.

An ALWAYS-ON Model Context Protocol server with no credentials. It loads a
bundled set of operational runbooks (``data/runbooks.json``) and exposes simple
search/read tools over them. This is the canonical "point an agent at your own
docs" pattern: swap ``data/runbooks.json`` for your content — or rewrite the
loader to read your wiki, a database, or a vector store — and the tools below
work unchanged.

WHERE THIS RUNS. On the single-box podman install, as a subprocess of the fleet
process. On the Kubernetes path, as a subprocess of the fleet process *in the
control-plane pod* — never in a sandbox pod. That is where brokered credentials
live (this server needs none, but the next one you write will), and it is why
``deploy/kubernetes/Containerfile.control-plane`` installs python3 and
``mcp/requirements.txt``.

Runs over stdio. Dependency-free apart from the MCP runtime: the search scorer
is a plain keyword ranker so the example stays portable.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# Log to stderr — stdout is reserved for the stdio transport.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("runbook_library")

# Path to the bundled runbooks. Override with RUNBOOKS_PATH to point at your own
# JSON file without editing this server. Under fleet the override only arrives
# because manifest.yaml passes it through: fleet spawns stdio servers with a
# MINIMAL environment (PATH/HOME/locale plus the manifest's env map), so an
# operator `export RUNBOOKS_PATH=…` never reaches this process on its own. The
# manifest maps it and lists it in optional_env, which drops the key when it is
# empty so this default stands.
DEFAULT_RUNBOOKS_PATH = Path(__file__).resolve().parent / "data" / "runbooks.json"
RUNBOOKS_PATH = Path(os.environ.get("RUNBOOKS_PATH") or DEFAULT_RUNBOOKS_PATH)


def _load_runbooks(path: Path) -> list[dict[str, Any]]:
    """Read and validate the runbook JSON.

    Raises FileNotFoundError when the file is missing and ValueError when it
    cannot be parsed or is not the expected shape. Callers surface these as
    clear, model-readable error messages rather than crashing the server — a
    server that dies at a bad file takes every tool with it, and fleet reports
    that as a dead connector rather than as "your JSON is malformed".
    """
    if not path.exists():
        raise FileNotFoundError(f"Runbook file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Runbook file is not valid JSON: {exc}") from exc

    runbooks = raw.get("runbooks", []) if isinstance(raw, dict) else raw
    if not isinstance(runbooks, list):
        raise ValueError("Runbook JSON must be a list, or an object with a 'runbooks' list.")
    return runbooks


def _text(runbook: dict[str, Any]) -> str:
    parts = [str(runbook.get(k, "")) for k in ("title", "category", "summary", "body")]
    parts.extend(str(t) for t in runbook.get("tags", []))
    return "\n".join(parts)


def _snippet(body: str, terms: list[str], width: int = 180) -> str:
    lowered = body.lower()
    for term in terms:
        idx = lowered.find(term)
        if idx >= 0:
            start = max(0, idx - width // 3)
            return ("…" if start else "") + body[start : start + width].strip() + "…"
    return body[:width].strip() + ("…" if len(body) > width else "")


@mcp.tool()
def rb_list_categories() -> list[dict[str, Any]]:
    """List every runbook category with how many runbooks it holds.

    Zero arguments, zero side effects, and it fails only if the runbook file
    itself failed to load — which is exactly what makes it the manifest's
    declared `probe:` canary for `fleet mcp test --deep`.
    """
    try:
        runbooks = _load_runbooks(RUNBOOKS_PATH)
    except (FileNotFoundError, ValueError) as exc:
        return [{"error": str(exc)}]
    counts: dict[str, int] = {}
    for rb in runbooks:
        counts[str(rb.get("category", "uncategorized"))] = counts.get(str(rb.get("category", "uncategorized")), 0) + 1
    return [{"category": name, "runbooks": n} for name, n in sorted(counts.items())]


@mcp.tool()
def rb_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search the runbook library. Returns ranked matches with an excerpt.

    Args:
        query: words to look for, e.g. "rotate a database credential".
        limit: maximum results (1-25).
    """
    try:
        runbooks = _load_runbooks(RUNBOOKS_PATH)
    except (FileNotFoundError, ValueError) as exc:
        return [{"error": str(exc)}]

    terms = [t for t in query.lower().split() if t]
    if not terms:
        return [{"error": "query must contain at least one word"}]
    limit = max(1, min(int(limit), 25))

    scored = []
    for rb in runbooks:
        haystack = _text(rb).lower()
        score = sum(haystack.count(t) for t in terms)
        # A title hit is worth more than a body hit.
        score += 5 * sum(str(rb.get("title", "")).lower().count(t) for t in terms)
        if score:
            scored.append((score, rb))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("title", ""))))

    return [
        {
            "id": rb.get("id"),
            "title": rb.get("title"),
            "category": rb.get("category"),
            "score": score,
            "excerpt": _snippet(str(rb.get("body", "")), terms),
        }
        for score, rb in scored[:limit]
    ]


@mcp.tool()
def rb_get_runbook(runbook_id: str) -> dict[str, Any]:
    """Read one runbook in full by its id (from rb_search results)."""
    try:
        runbooks = _load_runbooks(RUNBOOKS_PATH)
    except (FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}
    for rb in runbooks:
        if str(rb.get("id")) == str(runbook_id):
            return rb
    return {
        "error": f"No runbook with id {runbook_id!r}",
        "hint": "Call rb_search or rb_list_categories to find valid ids.",
    }


if __name__ == "__main__":
    logger.info("runbook_library: serving from %s", RUNBOOKS_PATH)
    mcp.run()
