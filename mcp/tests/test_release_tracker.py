"""Tests for the credential-gated release tracker connector.

The interesting properties of this server are not "does it parse JSON" — they
are the boundary ones, and each is easy to break without noticing:

* **The gate is real.** With no ``DEPLOY_API_TOKEN`` the connector must refuse
  locally and make **no** network call at all. A version that builds a request
  first and fails on a 401 leaks the fact that it tried, wastes the timeout, and
  behaves differently on a cluster (where the call leaves the CONTROL-PLANE pod)
  than on a box.
* **The token never escapes.** Not into a returned dict, not into a receipt, not
  into a log line. ``mcp/README.md`` says a ``repr(headers)`` in an exception
  handler is how a token reaches a log; this is the test that says so too.
* **The path validator holds.** ``_request`` takes a resource path, never a URL,
  so a model-supplied id cannot redirect the call to another host or climb out
  of the API's namespace.
* **The receipt degrades gracefully.** ``DEPLOY_API_OUTPUT_DIR`` is mapped to
  ``${FLEET_WORKSPACE}/outputs``, a token fleet DROPS on a spawn path with no
  workspace, so "unset" is a normal state that must produce a note rather than a
  crash.

``respx`` mocks httpx at the transport, so a "no request was made" assertion is
a real one: nothing reaches the network in this file.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import release_tracker
import respx

BASE_URL = "https://tracker.example.test/api/v1"

# An obvious non-secret. It is distinctive enough that a leak into a dict, a
# receipt, or a log line cannot hide behind ordinary words.
FAKE_TOKEN = "fake-token-for-tests"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts from a fully unconfigured connector."""
    for key in (
        "DEPLOY_API_TOKEN",
        "DEPLOY_API_BASE_URL",
        "DEPLOY_API_TIMEOUT_SECONDS",
        "DEPLOY_API_OUTPUT_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DEPLOY_API_BASE_URL", BASE_URL)


@pytest.fixture
def authenticated(monkeypatch):
    monkeypatch.setenv("DEPLOY_API_TOKEN", FAKE_TOKEN)


@pytest.fixture
def api():
    """A router with one catch-all route, so 'was anything called?' is answerable."""
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200, json={"releases": []}))
        yield router


# ── the gate: unset token means no request at all ───────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        lambda: release_tracker.rt_list_releases("checkout"),
        lambda: release_tracker.rt_get_release("rel-1"),
        lambda: release_tracker.rt_open_change_request("checkout", "bump the timeout"),
    ],
    ids=["list", "get", "open_change_request"],
)
def test_without_a_token_every_tool_refuses_before_the_network(api, call):
    result = call()

    assert result["ok"] is False
    assert "DEPLOY_API_TOKEN is not set" in result["error"]
    assert api.calls.call_count == 0, "the connector must not touch the network while unauthenticated"


def test_the_refusal_says_where_the_token_belongs_on_each_deployment_shape(api):
    error = release_tracker.rt_get_release("rel-1")["error"]

    assert "existingSecret" in error, "the cluster remedy must be named"
    assert ".env" in error, "the single-box remedy must be named"


# ── the write tool's own guard, ahead of the request ────────────────────────


def test_an_empty_summary_is_rejected_before_any_request(api, authenticated):
    result = release_tracker.rt_open_change_request("checkout", "   ")

    assert result == {"ok": False, "error": "summary is required"}
    assert api.calls.call_count == 0


# ── the path validator ──────────────────────────────────────────────────────


def test_the_path_validator_rejects_a_full_url(api, authenticated):
    result = release_tracker._request("GET", "https://elsewhere.example.test/releases")

    assert result["ok"] is False
    assert "refusing suspicious path" in result["error"]
    assert api.calls.call_count == 0


def test_the_path_validator_rejects_a_traversal_in_a_model_supplied_id(api, authenticated):
    """The id reaches the path, so the guard has to survive interpolation."""
    result = release_tracker.rt_get_release("../../admin/tokens")

    assert result["ok"] is False
    assert "refusing suspicious path" in result["error"]
    assert api.calls.call_count == 0


# ── the token never escapes ─────────────────────────────────────────────────


def _returned_text(result: dict) -> str:
    return json.dumps(result, default=str)


def test_the_token_is_sent_but_never_returned_or_logged(api, authenticated, caplog):
    with caplog.at_level(logging.DEBUG):
        ok = release_tracker.rt_list_releases("checkout")

    # It really is used…
    assert api.calls.last.request.headers["Authorization"] == f"Bearer {FAKE_TOKEN}"
    # …and it really does not come back.
    assert ok["ok"] is True
    assert FAKE_TOKEN not in _returned_text(ok)
    assert FAKE_TOKEN not in caplog.text


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"detail": "nope"}),
        httpx.Response(500, text="upstream on fire"),
        httpx.Response(200, text="<html>not json</html>"),
    ],
    ids=["unauthorized", "server-error", "non-json"],
)
def test_no_error_path_leaks_the_token(authenticated, caplog, response):
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=response)
        with caplog.at_level(logging.DEBUG):
            result = release_tracker.rt_get_release("rel-1")

    assert result["ok"] is False
    assert FAKE_TOKEN not in _returned_text(result)
    assert FAKE_TOKEN not in caplog.text


def test_a_transport_failure_is_an_error_dict_that_names_the_control_plane(authenticated, caplog):
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=httpx.ConnectError("no route to host"))
        with caplog.at_level(logging.DEBUG):
            result = release_tracker.rt_list_releases("checkout")

    assert result["ok"] is False
    assert "CONTROL-PLANE" in result["error"], (
        "on Kubernetes this call leaves the control-plane pod; the error should say so, "
        "because the first guess is always the sandbox NetworkPolicy"
    )
    assert FAKE_TOKEN not in _returned_text(result)
    assert FAKE_TOKEN not in caplog.text


# ── receipts: the ${FLEET_WORKSPACE} degrade-gracefully path ────────────────


def test_a_receipt_is_written_when_a_workspace_directory_is_offered(api, authenticated, monkeypatch, tmp_path):
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("DEPLOY_API_OUTPUT_DIR", str(outputs))

    result = release_tracker.rt_open_change_request("checkout", "bump the timeout", "detail here")

    path = result["receipt_path"]
    assert path.startswith(str(outputs))
    written = json.loads((outputs / path.rsplit("/", 1)[-1]).read_text(encoding="utf-8"))
    assert written["kind"] == "change-request"
    assert written["submitted"] == {
        "service": "checkout",
        "summary": "bump the timeout",
        "detail": "detail here",
    }
    assert FAKE_TOKEN not in json.dumps(written), "a receipt must never carry the credential"


def test_the_output_directory_is_created_if_it_does_not_exist(api, authenticated, monkeypatch, tmp_path):
    nested = tmp_path / "workspace" / "outputs"
    monkeypatch.setenv("DEPLOY_API_OUTPUT_DIR", str(nested))

    release_tracker.rt_open_change_request("checkout", "bump the timeout")

    assert nested.is_dir()
    assert len(list(nested.glob("change-request-*.json"))) == 1


def test_without_a_workspace_directory_the_result_says_the_receipt_was_not_written(api, authenticated):
    result = release_tracker.rt_open_change_request("checkout", "bump the timeout")

    assert "receipt_path" not in result
    assert result["receipt"] == "not written — no workspace directory was offered for this run"


def test_an_empty_output_directory_is_treated_as_unset(api, authenticated, monkeypatch):
    """fleet drops the key when there is no workspace; an operator can also blank it."""
    monkeypatch.setenv("DEPLOY_API_OUTPUT_DIR", "   ")

    result = release_tracker.rt_open_change_request("checkout", "bump the timeout")

    assert result["receipt"].startswith("not written")


# ── small contract details worth pinning ────────────────────────────────────


def test_the_release_limit_is_clamped(api, authenticated):
    release_tracker.rt_list_releases("checkout", limit=5000)

    assert api.calls.last.request.url.params["limit"] == "100"


def test_a_bad_timeout_value_falls_back_instead_of_raising(api, authenticated, monkeypatch, caplog):
    monkeypatch.setenv("DEPLOY_API_TIMEOUT_SECONDS", "soon")

    with caplog.at_level(logging.WARNING):
        result = release_tracker.rt_list_releases("checkout")

    assert result["ok"] is True
    assert "is not a number" in caplog.text
