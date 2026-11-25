from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient
from test_app import TRIAGE_EXPECTED, TRIAGE_NODES

from sunshine_dashboard import app as app_module
from sunshine_dashboard import fetch


@pytest.fixture
def client() -> TestClient:
    return TestClient(app_module.app)


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    fake = mock.Mock(return_value=TRIAGE_NODES)
    monkeypatch.setattr(fetch, "_fetch_with_any_token", fake)
    return fake


def numbers(body: dict) -> list[int]:
    return [issue["number"] for issue in body["issues"]]


def test_returns_issues_and_summary(client: TestClient, fake_fetch: mock.Mock) -> None:
    response = client.get("/api/issues", params={"repo": " o/n "})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["repo"] == "o/n"
    assert body["state"] == "open"
    assert body["total"] == body["count"] == 4
    assert body["triage"] is None
    assert body["triage_counts"] == {
        key: len(nums) for key, nums in TRIAGE_EXPECTED.items()
    }
    assert body["fetched_at"].endswith("Z") or "+00:00" in body["fetched_at"]


def test_issue_fields(client: TestClient, fake_fetch: mock.Mock) -> None:
    body = client.get("/api/issues", params={"repo": "o/n"}).json()
    issue = next(i for i in body["issues"] if i["number"] == 2)
    assert issue == {
        "number": 2,
        "title": "Issue 2",
        "state": "open",
        "created_at": issue["created_at"],
        "updated_at": issue["updated_at"],
        "closed_at": None,
        "author": "someone",
        "comments": 4,
        "labels": [{"name": "bug", "color": "d73a4a"}],
        "url": "https://github.com/o/n/issues/2",
    }


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, [1, 4, 3, 2]),  # created_at, newest first
        ({"descending": "false"}, [2, 3, 4, 1]),
        ({"sort_by": "updated_at"}, [1, 4, 3, 2]),
    ],
)
def test_sorting(
    client: TestClient, fake_fetch: mock.Mock, params: dict, expected: list[int]
) -> None:
    body = client.get("/api/issues", params={"repo": "o/n", **params}).json()
    assert numbers(body) == expected


@pytest.mark.parametrize(("key", "expected"), TRIAGE_EXPECTED.items())
def test_triage_filter(
    client: TestClient, fake_fetch: mock.Mock, key: str, expected: list[int]
) -> None:
    body = client.get("/api/issues", params={"repo": "o/n", "triage": key}).json()
    assert sorted(numbers(body)) == expected
    assert body["triage"] == key
    assert body["count"] == len(expected)
    assert body["total"] == 4  # summary still covers all open issues
    assert body["triage_counts"]["no_comments"] == 2


def test_shares_cache_with_page(client: TestClient, fake_fetch: mock.Mock) -> None:
    client.get("/", params={"repo": "o/n"})
    client.get("/api/issues", params={"repo": "O/N"})
    assert fake_fetch.call_count == 1


def test_refresh_refetches(client: TestClient, fake_fetch: mock.Mock) -> None:
    client.get("/api/issues", params={"repo": "o/n"})
    response = client.get("/api/issues", params={"repo": "o/n", "refresh": "true"})
    assert response.status_code == 200  # no redirect, unlike the page
    assert fake_fetch.call_count == 2


@pytest.mark.parametrize(
    "params",
    [
        {},  # repo is required
        {"repo": "o/n", "sort_by": "title"},
        {"repo": "o/n", "triage": "bogus"},
    ],
)
def test_bad_parameters_are_422(
    client: TestClient, fake_fetch: mock.Mock, params: dict
) -> None:
    assert client.get("/api/issues", params=params).status_code == 422


def test_invalid_repo_is_422_with_message(
    client: TestClient, fake_fetch: mock.Mock
) -> None:
    response = client.get("/api/issues", params={"repo": "not-a-repo"})
    assert response.status_code == 422
    assert "Invalid repo" in response.json()["detail"]
    fake_fetch.assert_not_called()


def test_github_failure_is_502_with_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fetch,
        "_fetch_with_any_token",
        mock.Mock(side_effect=RuntimeError("GitHub GraphQL error: Not found")),
    )
    response = client.get("/api/issues", params={"repo": "o/n"})
    assert response.status_code == 502
    assert response.json() == {"detail": "GitHub GraphQL error: Not found"}


def test_openapi_documents_endpoint(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/issues" in schema["paths"]
    assert "/" not in schema["paths"]  # the HTML page isn't part of the API
    params = {p["name"] for p in schema["paths"]["/api/issues"]["get"]["parameters"]}
    assert params == {"repo", "sort_by", "descending", "triage", "refresh"}


def test_github_timeout_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_fetch import _FailingResponse

    monkeypatch.setattr(
        fetch, "urlopen", lambda *a, **k: _FailingResponse(TimeoutError("timed out"))
    )
    monkeypatch.setattr(fetch, "_candidate_tokens", lambda: iter([("GH_TOKEN", "t")]))
    client = TestClient(app_module.app, raise_server_exceptions=False)
    response = client.get("/api/issues", params={"repo": "o/n"})
    assert response.status_code == 502
    assert response.json()["detail"].startswith("Lost the connection to GitHub")
