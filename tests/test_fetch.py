from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock
from urllib.error import HTTPError, URLError

import pytest
from conftest import make_node

from sunshine_dashboard import fetch

# --- parsing helpers -------------------------------------------------------


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ("napari/napari", ("napari", "napari")),
        ("  napari/napari  ", ("napari", "napari")),
        ("my-org/repo.name_2", ("my-org", "repo.name_2")),
    ],
)
def test_parse_repo_valid(repo: str, expected: tuple[str, str]) -> None:
    assert fetch.parse_repo(repo) == expected


@pytest.mark.parametrize(
    "repo", ["", "napari", "a/b/c", "/x", "o/", "foo bar/x", "o/n?x=1", "own_er/n"]
)
def test_parse_repo_invalid(repo: str) -> None:
    with pytest.raises(RuntimeError, match="Invalid repo"):
        fetch.parse_repo(repo)


@pytest.mark.parametrize(
    ("state", "fragment"),
    [("open", "states: [OPEN]"), ("closed", "states: [CLOSED]"), ("all", None)],
)
def test_build_query_state_filter(state: str, fragment: str | None) -> None:
    query = fetch._build_query(state)
    if fragment:
        assert fragment in query
    else:
        assert "states:" not in query


def test_iso_to_datetime_handles_z_suffix() -> None:
    assert fetch.iso_to_datetime("2026-01-02T03:04:05Z") == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC
    )


def test_to_row_maps_fields() -> None:
    node = make_node(
        7,
        state="CLOSED",
        closedAt="2026-03-01T00:00:00Z",
        comments={"totalCount": 3},
        labels={"nodes": [{"name": "bug"}, {"name": "good first issue"}]},
    )
    row = fetch._to_row(node)
    assert row.number == 7
    assert row.state == "closed"
    assert row.closed_at == "2026-03-01T00:00:00Z"
    assert row.comments == 3
    assert row.labels == "bug, good first issue"
    assert row.author == "someone"


def test_to_row_handles_deleted_author_and_missing_fields() -> None:
    row = fetch._to_row(make_node(author=None, labels=None, comments=None))
    assert row.author == ""
    assert row.labels == ""
    assert row.comments == 0


def test_issues_from_response_reports_graphql_error() -> None:
    response = {"data": {"repository": None}, "errors": [{"message": "Not found"}]}
    with pytest.raises(RuntimeError, match="GitHub GraphQL error: Not found"):
        fetch._issues_from_response(response)


# --- HTTPS fetching ---------------------------------------------------------


def _page(nodes: list[dict[str, Any]], next_cursor: str | None) -> io.BytesIO:
    body = {
        "data": {
            "repository": {
                "issues": {
                    "pageInfo": {
                        "hasNextPage": next_cursor is not None,
                        "endCursor": next_cursor,
                    },
                    "nodes": nodes,
                }
            }
        }
    }
    return io.BytesIO(json.dumps(body).encode())


def test_fetch_nodes_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [_page([make_node(1)], "c1"), _page([make_node(2)], None)]
    requests = []

    def fake_urlopen(request: Any, timeout: float) -> io.BytesIO:
        requests.append(request)
        return pages.pop(0)

    monkeypatch.setattr(fetch, "urlopen", fake_urlopen)
    nodes = fetch._fetch_nodes("o", "n", "open", "tok")

    assert [n["number"] for n in nodes] == [1, 2]
    assert requests[0].get_header("Authorization") == "Bearer tok"
    cursors = [json.loads(r.data)["variables"]["endCursor"] for r in requests]
    assert cursors == [None, "c1"]


def _http_error(code: int, body: bytes = b"{}") -> HTTPError:
    return HTTPError(fetch._GRAPHQL_URL, code, "error", {}, io.BytesIO(body))  # type: ignore[arg-type]


def test_fetch_nodes_401_raises_bad_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch, "urlopen", mock.Mock(side_effect=_http_error(401)))
    with pytest.raises(fetch._BadCredentials):
        fetch._fetch_nodes("o", "n", "open", "tok")


def test_fetch_nodes_other_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _http_error(502, b"bad gateway")
    monkeypatch.setattr(fetch, "urlopen", mock.Mock(side_effect=error))
    with pytest.raises(RuntimeError, match="HTTP 502: bad gateway"):
        fetch._fetch_nodes("o", "n", "open", "tok")


def test_fetch_nodes_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "urlopen", mock.Mock(side_effect=URLError("down")))
    with pytest.raises(RuntimeError, match="Unable to connect"):
        fetch._fetch_nodes("o", "n", "open", "tok")


# --- token lookup -----------------------------------------------------------


def test_gh_keyring_token_hides_env_tokens_from_gh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "env-token")
    monkeypatch.setenv("GITHUB_TOKEN", "other-env-token")
    run = mock.Mock(return_value=mock.Mock(returncode=0, stdout="keyring-token\n"))
    monkeypatch.setattr(fetch.subprocess, "run", run)

    assert fetch._gh_keyring_token() == "keyring-token"
    args, kwargs = run.call_args
    assert args[0] == ["gh", "auth", "token", "--hostname", "github.com"]
    assert not set(fetch._TOKEN_ENV_VARS) & set(kwargs["env"])


@pytest.mark.parametrize(
    "run",
    [
        mock.Mock(side_effect=FileNotFoundError),  # gh not installed
        mock.Mock(side_effect=subprocess.TimeoutExpired("gh", 10)),
        mock.Mock(return_value=mock.Mock(returncode=1, stdout="")),  # not logged in
    ],
)
def test_gh_keyring_token_unavailable(
    monkeypatch: pytest.MonkeyPatch, run: mock.Mock
) -> None:
    monkeypatch.setattr(fetch.subprocess, "run", run)
    assert fetch._gh_keyring_token() is None


def test_candidate_tokens_order_and_dedupe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "a")
    monkeypatch.setenv("GITHUB_TOKEN", "a")  # duplicate is skipped
    monkeypatch.setattr(fetch, "_gh_keyring_token", lambda: "b")
    assert list(fetch._candidate_tokens()) == [
        ("GH_TOKEN", "a"),
        ("gh auth login", "b"),
    ]


def test_candidate_tokens_keyring_lookup_is_lazy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "a")
    keyring = mock.Mock(return_value="b")
    monkeypatch.setattr(fetch, "_gh_keyring_token", keyring)
    tokens = fetch._candidate_tokens()
    assert next(tokens) == ("GH_TOKEN", "a")
    keyring.assert_not_called()


def _accept_only(good: str):
    def fake_fetch_nodes(owner: str, name: str, state: str, token: str) -> list:
        if token != good:
            raise fetch._BadCredentials()
        return [make_node()]

    return fake_fetch_nodes


def test_stale_env_token_falls_back_to_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "stale")
    monkeypatch.setattr(fetch, "_gh_keyring_token", lambda: "good")
    monkeypatch.setattr(fetch, "_fetch_nodes", _accept_only("good"))
    assert len(fetch._fetch_with_any_token("o", "n", "open")) == 1


def test_stale_gh_token_falls_back_to_github_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "stale")
    monkeypatch.setenv("GITHUB_TOKEN", "good")
    keyring = mock.Mock(return_value=None)
    monkeypatch.setattr(fetch, "_gh_keyring_token", keyring)
    monkeypatch.setattr(fetch, "_fetch_nodes", _accept_only("good"))
    assert len(fetch._fetch_with_any_token("o", "n", "open")) == 1
    keyring.assert_not_called()


def test_all_tokens_rejected_names_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "stale")
    monkeypatch.setattr(fetch, "_gh_keyring_token", lambda: "also-stale")
    monkeypatch.setattr(fetch, "_fetch_nodes", _accept_only("good"))
    with pytest.raises(RuntimeError, match=r"GH_TOKEN, gh auth login \(HTTP 401"):
        fetch._fetch_with_any_token("o", "n", "open")


def test_no_token_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "_gh_keyring_token", lambda: None)
    with pytest.raises(RuntimeError, match="No GitHub token found"):
        fetch._fetch_with_any_token("o", "n", "open")


# --- caching ----------------------------------------------------------------


@pytest.fixture
def counting_fetch(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """Replace the network fetch with a mock that returns one issue."""
    fake = mock.Mock(return_value=[make_node()])
    monkeypatch.setattr(fetch, "_fetch_with_any_token", fake)
    return fake


def test_cache_hit_and_case_insensitive_key(counting_fetch: mock.Mock) -> None:
    first = fetch.fetch_issues("napari/napari", "open")
    second = fetch.fetch_issues(" Napari/NAPARI ", "open")
    assert second is first
    assert counting_fetch.call_count == 1


def test_cache_is_per_state(counting_fetch: mock.Mock) -> None:
    fetch.fetch_issues("o/n", "open")
    fetch.fetch_issues("o/n", "closed")
    assert counting_fetch.call_count == 2


def test_refresh_bypasses_cache(counting_fetch: mock.Mock) -> None:
    first = fetch.fetch_issues("o/n", "open")
    second = fetch.fetch_issues("o/n", "open", refresh=True)
    assert second is not first
    assert second.fetched_at >= first.fetched_at
    assert counting_fetch.call_count == 2


def test_expired_cache_refetches(counting_fetch: mock.Mock) -> None:
    first = fetch.fetch_issues("o/n", "open")
    expired = first.fetched_at - timedelta(seconds=fetch._CACHE_TTL_SECONDS + 1)
    fetch._cache[("o/n", "open")] = fetch.FetchResult(first.rows, expired)
    fetch.fetch_issues("o/n", "open")
    assert counting_fetch.call_count == 2


def test_errors_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = mock.Mock(side_effect=[RuntimeError("boom"), [make_node()]])
    monkeypatch.setattr(fetch, "_fetch_with_any_token", fake)
    with pytest.raises(RuntimeError, match="boom"):
        fetch.fetch_issues("o/n", "open")
    assert len(fetch.fetch_issues("o/n", "open").rows) == 1


def test_invalid_repo_never_fetches(counting_fetch: mock.Mock) -> None:
    with pytest.raises(RuntimeError, match="Invalid repo"):
        fetch.fetch_issues("not-a-repo", "open")
    counting_fetch.assert_not_called()


def _run_concurrently(n: int, target, **kwargs) -> list:
    results: list = []
    threads = [
        threading.Thread(target=lambda: results.append(target(**kwargs)))
        for _ in range(n)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return results


def _slow_fetch(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    def slow(*args: Any) -> list:
        time.sleep(0.2)  # long enough for the other threads to queue up
        return [make_node()]

    fake = mock.Mock(side_effect=slow)
    monkeypatch.setattr(fetch, "_fetch_with_any_token", fake)
    return fake


def test_concurrent_requests_share_one_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _slow_fetch(monkeypatch)
    results = _run_concurrently(
        8, fetch.fetch_issues, repo="napari/napari", state="open"
    )
    assert fake.call_count == 1
    assert len(results) == 8
    assert all(r is results[0] for r in results)


def test_concurrent_refreshes_share_one_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _slow_fetch(monkeypatch)
    _run_concurrently(4, fetch.fetch_issues, repo="o/n", state="open", refresh=True)
    assert fake.call_count == 1


def test_different_repos_fetch_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Barrier(2, timeout=5)

    def wait_for_other(*args: Any) -> list:
        started.wait()  # raises BrokenBarrierError if the fetches run one at a time
        return [make_node()]

    monkeypatch.setattr(fetch, "_fetch_with_any_token", wait_for_other)
    threads = [
        threading.Thread(target=fetch.fetch_issues, args=(repo, "open"))
        for repo in ("a/one", "b/two")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert ("a/one", "open") in fetch._cache
    assert ("b/two", "open") in fetch._cache
