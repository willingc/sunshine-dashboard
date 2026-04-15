from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CACHE_TTL_SECONDS = 24 * 60 * 60
_cache: dict[tuple[str, str], tuple[float, list["IssueRow"]]] = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class IssueRow:
    number: int
    title: str
    state: str
    created_at: str
    updated_at: str
    closed_at: str | None
    author: str
    comments: int
    labels: str
    url: str


def iso_to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_MAX_PAGES = 200


def _build_query(state: str) -> str:
    if state == "open":
        states_arg = ", states: [OPEN]"
    elif state == "closed":
        states_arg = ", states: [CLOSED]"
    else:
        states_arg = ""
    return (
        "query($owner: String!, $name: String!, $endCursor: String) {"
        "  repository(owner: $owner, name: $name) {"
        "    issues(first: 100, after: $endCursor,"
        "           orderBy: {field: CREATED_AT, direction: DESC}" + states_arg + ") {"
        "      pageInfo { hasNextPage endCursor }"
        "      nodes {"
        "        number title state createdAt updatedAt closedAt url"
        "        comments { totalCount }"
        "        author { login }"
        "        labels(first: 50) { nodes { name } }"
        "      }"
        "    }"
        "  }"
        "}"
    )


def _split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise RuntimeError(f"Invalid repo '{repo}'. Expected 'owner/name'.")
    return owner, name


def _issues_from_response(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") or {}
    repository = data.get("repository")
    if not repository:
        errors = response.get("errors") or []
        message = errors[0].get("message") if errors else "Repository not found."
        raise RuntimeError(f"GitHub GraphQL error: {message}")
    return repository["issues"]


def _fetch_with_gh(owner: str, name: str, state: str, token: str) -> list[dict[str, Any]] | None:
    query = _build_query(state)
    try:
        proc = subprocess.run(
            [
                "gh", "api", "graphql", "--paginate", "--slurp",
                "-f", f"query={query}",
                "-F", f"owner={owner}",
                "-F", f"name={name}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env={**os.environ, "GH_TOKEN": token},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        pages = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(pages, list):
        return None
    nodes: list[dict[str, Any]] = []
    for page in pages:
        nodes.extend(_issues_from_response(page).get("nodes", []) or [])
    return nodes


def _fetch_with_https(owner: str, name: str, state: str, token: str) -> list[dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    query = _build_query(state)
    nodes: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        body = json.dumps(
            {"query": query, "variables": {"owner": owner, "name": name, "endCursor": cursor}}
        ).encode("utf-8")
        request = Request(
            "https://api.github.com/graphql", data=body, headers=headers, method="POST"
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub GraphQL request failed. HTTP {exc.code}: {details}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                "Unable to connect to GitHub GraphQL. Check your network, proxy, or VPN."
            ) from exc
        issues = _issues_from_response(json.loads(payload))
        nodes.extend(issues.get("nodes", []) or [])
        page_info = issues.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        cursor = page_info.get("endCursor")
    raise RuntimeError(
        f"Stopped after {_MAX_PAGES} pages to avoid an infinite fetch loop."
    )


def _to_row(node: dict[str, Any]) -> IssueRow:
    author = node.get("author") or {}
    labels = (node.get("labels") or {}).get("nodes") or []
    return IssueRow(
        number=node["number"],
        title=node["title"],
        state=node["state"].lower(),
        created_at=node["createdAt"],
        updated_at=node["updatedAt"],
        closed_at=node.get("closedAt"),
        author=author.get("login", ""),
        comments=(node.get("comments") or {}).get("totalCount", 0),
        labels=", ".join(label["name"] for label in labels),
        url=node["url"],
    )


def fetch_issues(repo: str, state: str = "all") -> list[IssueRow]:
    key = (repo, state)
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "GitHub access token required. Set GH_TOKEN or GITHUB_TOKEN."
        )
    owner, name = _split_repo(repo)
    nodes = _fetch_with_gh(owner, name, state, token)
    if nodes is None:
        nodes = _fetch_with_https(owner, name, state, token)
    rows = [_to_row(node) for node in nodes]

    with _cache_lock:
        _cache[key] = (now, rows)
    return rows
