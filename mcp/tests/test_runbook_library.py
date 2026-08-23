"""Tests for the always-on runbook library server.

Four things are worth a test here, and each one has a failure mode that is
quiet rather than loud:

* ``rb_list_categories`` is the manifest's declared ``probe:`` — the canary
  ``fleet mcp test --deep`` runs — and the manifest asserts a literal substring
  against its first text block. Rename that key in the server and the probe
  starts failing in a deployment, not in CI. So the assertion here is read out
  of ``manifest.yaml`` rather than restated.
* The ranker's whole point is that a title hit beats a body hit. Lose that and
  search still "works" — it just answers with the wrong runbook, which nothing
  else notices.
* An unknown id must come back as a *readable* error with a way forward, not an
  exception. A tool that raises takes the server's whole tool surface with it,
  and fleet reports that as a dead connector rather than as a bad id.
* A missing or malformed runbook file must do the same. This is the most likely
  real-world breakage — someone points ``RUNBOOKS_PATH`` at their own file — and
  "your JSON is malformed" is a far better answer than a dead server.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import runbook_library
import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "manifest.yaml"


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Point the server at a purpose-built runbook file for one test.

    Returns a writer: call it with a list of runbook dicts (or a raw string, to
    write something that is not valid JSON at all) and the module-level
    ``RUNBOOKS_PATH`` is repointed at it for the duration of the test.
    """

    def _write(content) -> Path:
        path = tmp_path / "runbooks.json"
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_text(json.dumps({"runbooks": content}), encoding="utf-8")
        monkeypatch.setattr(runbook_library, "RUNBOOKS_PATH", path)
        return path

    return _write


def _probe_from_manifest() -> dict:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for server in manifest["mcp_servers"]:
        if server["name"] == "runbook_library":
            return server["probe"]
    raise AssertionError("manifest.yaml declares no runbook_library server")


# ── rb_list_categories: the manifest's probe ────────────────────────────────


def test_the_manifest_probe_names_this_tool():
    """Guard the other direction: the probe must still point at a real tool."""
    probe = _probe_from_manifest()
    assert probe["tool"] == "rb_list_categories"
    assert hasattr(runbook_library, probe["tool"])


def test_list_categories_contains_the_literal_key_the_probe_asserts_on():
    """`fleet mcp test --deep` greps the first text block for this substring.

    Run against the SHIPPED data file, not a fixture, because that is what a
    deployment probes. The needle is read out of the manifest so that renaming
    the key in the server fails here instead of in a cluster.
    """
    needle = _probe_from_manifest()["contains"]
    rows = runbook_library.rb_list_categories()

    assert rows, "the bundled runbook file produced no categories"
    assert all(needle in row for row in rows), f"every row must carry the literal key {needle!r}"
    assert needle in json.dumps(rows[0]), "the probe's substring must survive serialization"


def test_list_categories_counts_runbooks_per_category(library):
    library(
        [
            {"id": "a", "title": "A", "category": "delivery", "body": ""},
            {"id": "b", "title": "B", "category": "delivery", "body": ""},
            {"id": "c", "title": "C", "category": "incident", "body": ""},
            {"id": "d", "title": "D", "body": ""},  # no category at all
        ]
    )
    rows = runbook_library.rb_list_categories()

    assert rows == [
        {"category": "delivery", "runbooks": 2},
        {"category": "incident", "runbooks": 1},
        {"category": "uncategorized", "runbooks": 1},
    ]


# ── rb_search: ranking ──────────────────────────────────────────────────────


def test_search_ranks_a_title_hit_above_a_body_hit(library):
    """A term in the title outscores the same term repeated in a body.

    This is the ranker's one deliberate opinion. Without it search still returns
    results — just the wrong one first, which is the kind of wrong that gets
    cited in an answer.
    """
    library(
        [
            {"id": "rb-title", "title": "Rotating a widget", "category": "x", "body": "Nothing else here."},
            {"id": "rb-body", "title": "Something unrelated", "category": "x", "body": "widget widget widget"},
        ]
    )
    results = runbook_library.rb_search("widget")

    assert [r["id"] for r in results] == ["rb-title", "rb-body"]
    assert results[0]["score"] > results[1]["score"]


def test_search_returns_only_matches_and_honors_the_limit(library):
    library(
        [{"id": f"rb-{n}", "title": f"Widget {n}", "category": "x", "body": "widget"} for n in range(5)]
        + [{"id": "rb-none", "title": "Unrelated", "category": "x", "body": "nothing"}]
    )
    results = runbook_library.rb_search("widget", limit=3)

    assert len(results) == 3
    assert "rb-none" not in {r["id"] for r in results}


def test_search_with_no_terms_is_an_error_not_a_full_dump(library):
    library([{"id": "a", "title": "A", "category": "x", "body": "b"}])
    results = runbook_library.rb_search("   ")

    assert results == [{"error": "query must contain at least one word"}]


# ── rb_get_runbook: unknown id ──────────────────────────────────────────────


def test_unknown_id_returns_an_error_dict_with_a_way_forward(library):
    library([{"id": "rb-001", "title": "A", "category": "x", "body": "b"}])
    result = runbook_library.rb_get_runbook("rb-999")

    assert set(result) == {"error", "hint"}
    assert "rb-999" in result["error"]
    assert "rb_search" in result["hint"]


def test_known_id_returns_the_whole_runbook(library):
    library([{"id": "rb-001", "title": "A", "category": "x", "body": "the full body", "tags": ["t"]}])
    result = runbook_library.rb_get_runbook("rb-001")

    assert "error" not in result
    assert result["body"] == "the full body"


# ── a bad runbook file is an error dict, never an exception ─────────────────


def test_a_missing_file_returns_an_error_from_every_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(runbook_library, "RUNBOOKS_PATH", tmp_path / "does-not-exist.json")

    assert "not found" in runbook_library.rb_list_categories()[0]["error"]
    assert "not found" in runbook_library.rb_search("anything")[0]["error"]
    assert "not found" in runbook_library.rb_get_runbook("rb-001")["error"]


def test_malformed_json_returns_an_error_from_every_tool(library):
    library("{ this is not json")

    for result in (
        runbook_library.rb_list_categories()[0],
        runbook_library.rb_search("anything")[0],
        runbook_library.rb_get_runbook("rb-001"),
    ):
        assert "valid JSON" in result["error"]


def test_json_of_the_wrong_shape_returns_an_error_not_a_crash(tmp_path, monkeypatch):
    path = tmp_path / "runbooks.json"
    path.write_text(json.dumps({"runbooks": {"not": "a list"}}), encoding="utf-8")
    monkeypatch.setattr(runbook_library, "RUNBOOKS_PATH", path)

    assert "must be a list" in runbook_library.rb_list_categories()[0]["error"]


def test_a_bare_json_list_is_accepted(library):
    """The loader accepts a top-level list as well as {"runbooks": [...]}."""
    path = Path(library([]))
    path.write_text(json.dumps([{"id": "a", "title": "A", "category": "x", "body": "b"}]), encoding="utf-8")

    assert runbook_library.rb_list_categories() == [{"category": "x", "runbooks": 1}]
