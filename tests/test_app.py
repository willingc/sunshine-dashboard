from __future__ import annotations

import re
from datetime import UTC, datetime
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


def test_external_assets_have_sri(client: TestClient, fake_fetch: mock.Mock) -> None:
    html = client.get("/", params={"repo": "o/n"}).text
    tags = re.findall(r"<(?:script|link)\b[^>]*>", html)
    external = [t for t in tags if re.search(r'(?:src|href)="https?://', t)]
    assert len(external) == 5
    for tag in external:
        assert re.search(r'integrity="sha384-[A-Za-z0-9+/=]+"', tag), tag
        assert 'crossorigin="anonymous"' in tag, tag


NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-06-15T11:59:30Z", "just now"),
        ("2026-06-15T11:59:00Z", "1 minute ago"),
        ("2026-06-15T11:15:00Z", "45 minutes ago"),
        ("2026-06-15T11:00:00Z", "1 hour ago"),
        ("2026-06-15T01:00:00Z", "11 hours ago"),
        ("2026-06-14T12:00:00Z", "yesterday"),
        ("2026-06-12T12:00:00Z", "3 days ago"),
        ("2026-05-17T12:00:00Z", "29 days ago"),
        ("2026-05-16T12:00:00Z", "1 month ago"),  # 30 days
        ("2026-05-15T12:00:00Z", "1 month ago"),
        ("2025-06-16T12:00:00Z", "12 months ago"),
        ("2025-06-15T12:00:00Z", "1 year ago"),
        ("2023-01-01T00:00:00Z", "3 years ago"),
    ],
)
def test_age(value: str, expected: str) -> None:
    assert app_module.age(value, NOW) == expected


def test_dates_show_day_and_age_and_sort_by_timestamp(
    client: TestClient, fake_fetch: mock.Mock
) -> None:
    html = client.get("/", params={"repo": "o/n"}).text
    cell = re.search(
        r'<td class="text-nowrap" data-order="2026-01-01T00:00:00Z"[^>]*>(.*?)</td>',
        html,
        re.DOTALL,
    )
    assert cell, "created/updated cells keep the full timestamp for sorting"
    assert "2026-01-01" in cell.group(1)
    assert "ago" in cell.group(1)
    assert "T00:00:00Z" not in cell.group(1)  # raw timestamp no longer shown


def test_labels_render_as_colored_badges(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    node = make_node(
        labels={
            "nodes": [
                {"name": "bug", "color": "b60205"},
                {"name": "<b>sneaky</b>", "color": "x; background: url(evil)"},
            ]
        }
    )
    monkeypatch.setattr(fetch, "_fetch_with_any_token", mock.Mock(return_value=[node]))
    html = client.get("/", params={"repo": "o/n"}).text
    assert 'background-color: #b60205; color: #ffffff">bug</span>' in html
    # Invalid colors fall back to gray, and label names are HTML-escaped.
    assert "background-color: #ededed; color: #000000" in html
    assert "&lt;b&gt;sneaky&lt;/b&gt;" in html
    assert "evil" not in html
