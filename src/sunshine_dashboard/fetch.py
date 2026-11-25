from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CACHE_TTL_SECONDS = 24 * 60 * 60
_MAX_PAGES = 200
_GRAPHQL_URL = "https://api.github.com/graphql"
_TOKEN_ENV_VARS = ("GH_TOKEN", "GITHUB_TOKEN")

# GitHub owners: letters, digits, hyphens. Repo names may also contain "." and "_".
_REPO_RE = re.compile(r"^(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+)$")

_CacheKey = tuple[str, str]
_cache: dict[_CacheKey, FetchResult] = {}
_cache_lock = threading.Lock()  # guards _cache and _key_locks
_key_locks: dict[_CacheKey, threading.Lock] = {}  # one fetch per (repo, state) at a time


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


@dataclass(frozen=True)
class FetchResult:
    rows: tuple[IssueRow, ...]
    fetched_at: datetime  # UTC


class _BadCredentials(RuntimeError):
    """GitHub rejected the token (HTTP 401)."""


def iso_to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_repo(repo: str) -> tuple[str, str]:
    """Split 'owner/name' and validate it, raising RuntimeError if malformed."""
    match = _REPO_RE.match(repo.strip())
    if not match:
        raise RuntimeError(f"Invalid repo '{repo}'. Expected 'owner/name', e.g. 'napari/napari'.")
    return match["owner"], match["name"]


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


def _issues_from_response(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") or {}
    repository = data.get("repository")
    if not repository:
        errors = response.get("errors") or []
        message = errors[0].get("message") if errors else "Repository not found."
        raise RuntimeError(f"GitHub GraphQL error: {message}")
    return repository["issues"]


def _gh_keyring_token() -> str | None:
    """Return the token from the gh CLI's own login, or None if unavailable.

    GH_TOKEN/GITHUB_TOKEN are removed from gh's environment; otherwise
    `gh auth token` just echoes them back instead of the keyring login.
    """
    env = {k: v for k, v in os.environ.items() if k not in _TOKEN_ENV_VARS}
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, check=False, timeout=10, env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    token = proc.stdout.strip()
    return token if proc.returncode == 0 and token else None


def _candidate_tokens() -> Iterator[tuple[str, str]]:
    """Yield (source, token) pairs in priority order, skipping duplicates.

    The gh keyring lookup runs only if earlier tokens are missing or rejected.
    """
    seen: set[str] = set()
    for var in _TOKEN_ENV_VARS:
        token = os.getenv(var, "").strip()
        if token and token not in seen:
            seen.add(token)
            yield var, token
    token = _gh_keyring_token()
    if token and token not in seen:
        yield "gh auth login", token


def _fetch_nodes(owner: str, name: str, state: str, token: str) -> list[dict[str, Any]]:
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
        request = Request(_GRAPHQL_URL, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code == 401:
                raise _BadCredentials() from exc
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


def _fetch_with_any_token(owner: str, name: str, state: str) -> list[dict[str, Any]]:
    """Fetch using the first token GitHub accepts; fall through on HTTP 401."""
    rejected: list[str] = []
    for source, token in _candidate_tokens():
        try:
            return _fetch_nodes(owner, name, state, token)
        except _BadCredentials:
            rejected.append(source)
    if rejected:
        raise RuntimeError(
            "GitHub rejected the token from "
            + ", ".join(rejected)
            + " (HTTP 401: Bad credentials). Unset or replace expired tokens, "
            "or run `gh auth login`."
        )
    raise RuntimeError(
        "No GitHub token found. Run `gh auth login`, or set GH_TOKEN or GITHUB_TOKEN."
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


def fetch_issues(repo: str, state: str = "all", *, refresh: bool = False) -> FetchResult:
    """Return issues for `repo`, cached for 24 hours.

    `refresh=True` bypasses the cache, unless another request finished a fetch
    while this one was waiting. Concurrent requests for the same repo share a
    single fetch instead of each downloading every page.
    """
    owner, name = parse_repo(repo)
    key = (f"{owner}/{name}".lower(), state)
    requested_at = datetime.now(timezone.utc)

    with _cache_lock:
        key_lock = _key_locks.setdefault(key, threading.Lock())

    with key_lock:
        with _cache_lock:
            cached = _cache.get(key)
        if cached:
            age = (datetime.now(timezone.utc) - cached.fetched_at).total_seconds()
            fresh = age < _CACHE_TTL_SECONDS
            if (fresh and not refresh) or cached.fetched_at >= requested_at:
                return cached

        nodes = _fetch_with_any_token(owner, name, state)
        result = FetchResult(
            rows=tuple(_to_row(node) for node in nodes),
            fetched_at=datetime.now(timezone.utc),
        )
        with _cache_lock:
            _cache[key] = result
        return result
