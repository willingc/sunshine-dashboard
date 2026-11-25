from __future__ import annotations

import re
from unittest import mock

import pytest
from conftest import make_node
from fastapi.testclient import TestClient

from sunshine_dashboard import app as app_module
from sunshine_dashboard import fetch

NODES = [
    make_node(1, createdAt="2026-01-01T00:00:00Z", updatedAt="2026-03-01T00:00:00Z"),
    make_node(2, createdAt="2026-02-01T00:00:00Z", updatedAt="2026-01-15T00:00:00Z"),
    make_node(3, createdAt="2026-03-01T00:00:00Z", updatedAt="2026-02-01T00:00:00Z"),
]


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    fake = mock.Mock(return_value=NODES)
    monkeypatch.setattr(fetch, "_fetch_with_any_token", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(app_module.app, follow_redirects=False)


def issue_numbers(html: str) -> list[int]:
    return [int(n) for n in re.findall(r'/issues/(\d+)" target="_blank"', html)]


def test_page_lists_issues(client: TestClient, fake_fetch: mock.Mock) -> None:
    response = client.get("/", params={"repo": "o/n"})
    assert response.status_code == 200
    assert sorted(issue_numbers(response.text)) == [1, 2, 3]
    assert "Issue fetch failed" not in response.text
    assert "UTC" in response.text  # "Data fetched" timestamp


@pytest.mark.parametrize(
    ("params", "expected_order", "datatables_order"),
    [
        ({}, [3, 2, 1], '[[3, "desc"]]'),
        ({"descending": "false"}, [1, 2, 3], '[[3, "asc"]]'),
        ({"sort_by": "updated_at"}, [1, 3, 2], '[[4, "desc"]]'),
        ({"sort_by": "updated_at", "descending": "false"}, [2, 3, 1], '[[4, "asc"]]'),
    ],
)
def test_sorting(
    client: TestClient,
    fake_fetch: mock.Mock,
    params: dict[str, str],
    expected_order: list[int],
    datatables_order: str,
) -> None:
    response = client.get("/", params={"repo": "o/n", **params})
    assert issue_numbers(response.text) == expected_order
    assert f"order: {datatables_order}" in response.text


def test_sorting_does_not_change_cache(
    client: TestClient, fake_fetch: mock.Mock
) -> None:
    client.get("/", params={"repo": "o/n"})
    cached = fetch._cache[("o/n", "open")].rows
    client.get("/", params={"repo": "o/n", "descending": "false"})
    assert fetch._cache[("o/n", "open")].rows is cached
    assert [row.number for row in cached] == [1, 2, 3]


def test_invalid_sort_by_is_rejected(client: TestClient, fake_fetch: mock.Mock) -> None:
    assert client.get("/", params={"sort_by": "title"}).status_code == 422


def test_invalid_repo_shows_error_on_page(
    client: TestClient, fake_fetch: mock.Mock
) -> None:
    response = client.get("/", params={"repo": "not-a-repo"})
    assert response.status_code == 200
    assert "Issue fetch failed" in response.text
    assert "Invalid repo" in response.text
    fake_fetch.assert_not_called()


def test_fetch_error_shows_on_page(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fetch, "_fetch_with_any_token", mock.Mock(side_effect=RuntimeError("boom"))
    )
    response = client.get("/", params={"repo": "o/n"})
    assert response.status_code == 200
    assert "Issue fetch failed" in response.text
    assert "boom" in response.text
    assert issue_numbers(response.text) == []


def test_refresh_refetches_and_redirects_without_refresh_param(
    client: TestClient, fake_fetch: mock.Mock
) -> None:
    client.get("/", params={"repo": "o/n"})
    response = client.get(
        "/", params={"repo": "o/n", "sort_by": "updated_at", "refresh": "true"}
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert "refresh" not in location
    assert "sort_by=updated_at" in location
    assert fake_fetch.call_count == 2


def test_refresh_error_renders_page_instead_of_redirecting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fetch, "_fetch_with_any_token", mock.Mock(side_effect=RuntimeError("boom"))
    )
    response = client.get("/", params={"repo": "o/n", "refresh": "true"})
    assert response.status_code == 200
    assert "boom" in response.text


def test_refresh_link_on_page(client: TestClient, fake_fetch: mock.Mock) -> None:
    response = client.get("/", params={"repo": "o/n"})
    assert "refresh=true" in response.text


def test_static_css_is_served(client: TestClient) -> None:
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "dashboard-bg" in response.text


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], {"host": "127.0.0.1", "port": 8000, "reload": False}),
        (
            ["--host", "0.0.0.0", "--port", "9000", "--reload"],
            {"host": "0.0.0.0", "port": 9000, "reload": True},
        ),
    ],
)
def test_main_cli_options(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: dict
) -> None:
    run = mock.Mock()
    monkeypatch.setattr(app_module.uvicorn, "run", run)
    monkeypatch.setattr("sys.argv", ["sunshine-dashboard", *argv])
    app_module.main()
    run.assert_called_once_with("sunshine_dashboard.app:app", **expected)
