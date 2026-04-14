from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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


def _fetch_with_gh(repo: str, state: str) -> list[list[dict[str, Any]]] | None:
    endpoint = f"/repos/{repo}/issues?state={state}&per_page=100"
    try:
        proc = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", endpoint],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    parsed = json.loads(proc.stdout)
    if not isinstance(parsed, list):
        return None
    return parsed


def _fetch_with_rest(repo: str, state: str) -> list[list[dict[str, Any]]]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    page = 1
    pages: list[list[dict[str, Any]]] = []
    max_pages = 200
    while page <= max_pages:
        query = urlencode({"state": state, "per_page": 100, "page": page})
        request = Request(f"https://api.github.com/repos/{repo}/issues?{query}", headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                "GitHub API request failed. "
                "Set a valid GH_TOKEN to avoid low unauthenticated limits.\n"
                f"HTTP {exc.code}: {details}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                "Unable to connect to GitHub API. Check your network, proxy, or VPN settings."
            ) from exc
        current_page = json.loads(payload)
        if isinstance(current_page, dict):
            raise RuntimeError(
                "GitHub API returned an unexpected response. "
                "This often means rate-limiting or authentication failure.\n"
                f"Payload: {current_page}"
            )
        if not isinstance(current_page, list):
            raise RuntimeError(f"Unexpected GitHub payload type: {type(current_page)}")
        if not current_page:
            break
        pages.append(current_page)
        page += 1
    else:
        raise RuntimeError(
            "Stopped after 200 pages to avoid an infinite fetch loop. "
            "Try narrowing results with state=open/closed or verify API responses."
        )
    return pages


def fetch_issues(repo: str, state: str = "all") -> list[IssueRow]:
    pages = _fetch_with_gh(repo, state)
    if pages is None:
        pages = _fetch_with_rest(repo, state)

    rows: list[IssueRow] = []
    for page in pages:
        for item in page:
            if "pull_request" in item:
                continue
            rows.append(
                IssueRow(
                    number=item["number"],
                    title=item["title"],
                    state=item["state"],
                    created_at=item["created_at"],
                    updated_at=item["updated_at"],
                    closed_at=item["closed_at"],
                    author=item["user"]["login"] if item.get("user") else "",
                    comments=item["comments"],
                    labels=", ".join(label["name"] for label in item.get("labels", [])),
                    url=item["html_url"],
                )
            )
    return rows
