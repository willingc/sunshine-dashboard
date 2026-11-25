from __future__ import annotations

from typing import Any

import pytest

from sunshine_dashboard import fetch


def make_node(number: int = 1, **overrides: Any) -> dict[str, Any]:
    """A GraphQL issue node shaped like the GitHub API response."""
    node: dict[str, Any] = {
        "number": number,
        "title": f"Issue {number}",
        "state": "OPEN",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-02-01T00:00:00Z",
        "closedAt": None,
        "url": f"https://github.com/o/n/issues/{number}",
        "comments": {"totalCount": 0},
        "author": {"login": "someone"},
        "labels": {"nodes": []},
    }
    node.update(overrides)
    return node


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with an empty cache, no tokens, and no network or gh access."""
    fetch._cache.clear()
    fetch._key_locks.clear()
    for var in fetch._TOKEN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tests must not call GitHub; patch the fetch instead")

    monkeypatch.setattr(fetch, "urlopen", no_network)
    monkeypatch.setattr(fetch.subprocess, "run", no_network)
