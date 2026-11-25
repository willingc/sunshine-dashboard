from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .fetch import (
    FetchResult,
    InvalidRepo,
    IssueRow,
    fetch_issues,
    iso_to_datetime,
)

# templates/ and static/ ship inside the package so the app works when installed.
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def age(value: str, now: datetime) -> str:
    """Describe how long ago an ISO timestamp was, e.g. "3 months ago"."""
    seconds = (now - iso_to_datetime(value)).total_seconds()
    days = int(seconds // 86400)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        amount, unit = int(seconds // 60), "minute"
    elif days < 1:
        amount, unit = int(seconds // 3600), "hour"
    elif days == 1:
        return "yesterday"
    elif days < 30:
        amount, unit = days, "day"
    elif days < 365:
        amount, unit = days // 30, "month"
    else:
        amount, unit = days // 365, "year"
    return f"{amount} {unit}{'' if amount == 1 else 's'} ago"


templates.env.filters["age"] = age

STALE_AFTER = timedelta(days=180)
NEW_WITHIN = timedelta(days=30)


@dataclass(frozen=True)
class Triage:
    label: str
    matches: Callable[[IssueRow, datetime], bool]


# Triage stat tiles, in display order. Each one also filters the table (?triage=key).
TRIAGE: dict[str, Triage] = {
    "no_comments": Triage("No comments", lambda row, now: row.comments == 0),
    "unlabeled": Triage("Unlabeled", lambda row, now: not row.labels),
    "stale": Triage(
        "No activity in 6 months",
        lambda row, now: now - iso_to_datetime(row.updated_at) >= STALE_AFTER,
    ),
    "new": Triage(
        "Opened in the last 30 days",
        lambda row, now: now - iso_to_datetime(row.created_at) < NEW_WITHIN,
    ),
}
_TRIAGE_PATTERN = "^(" + "|".join(TRIAGE) + ")$"
_SORT_PATTERN = "^(created_at|updated_at)$"
STATE = "open"


def triage_counts(rows: Sequence[IssueRow], now: datetime) -> dict[str, int]:
    return {
        key: sum(1 for row in rows if item.matches(row, now))
        for key, item in TRIAGE.items()
    }


def select_rows(
    rows: Sequence[IssueRow],
    *,
    triage: str | None,
    sort_by: str,
    descending: bool,
    now: datetime,
) -> list[IssueRow]:
    """Apply the triage filter, then sort. Returns a new list; `rows` is untouched."""
    if triage:
        rows = [row for row in rows if TRIAGE[triage].matches(row, now)]
    return sorted(
        rows,
        key=lambda row: iso_to_datetime(getattr(row, sort_by)),
        reverse=descending,
    )


app = FastAPI(title="Sunshine Dashboard")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(
    request: Request,
    repo: str = Query(default="napari/napari"),
    sort_by: str = Query(default="created_at", pattern=_SORT_PATTERN),
    descending: bool = Query(default=True),
    refresh: bool = Query(default=False),
    triage: str | None = Query(default=None, pattern=_TRIAGE_PATTERN),
) -> Response:
    repo = repo.strip()
    error_message = ""
    result: FetchResult | None = None
    try:
        result = fetch_issues(repo=repo, state=STATE, refresh=refresh)
    except RuntimeError as exc:
        error_message = str(exc)
    else:
        if refresh:
            # Redirect so reloading or bookmarking the page doesn't refetch every time.
            return RedirectResponse(
                str(request.url.remove_query_params("refresh")), status_code=303
            )

    now = datetime.now(UTC)
    all_rows = result.rows if result else ()
    stats = []
    for key, count in triage_counts(all_rows, now).items():
        active = key == triage
        # Clicking the active tile clears the filter; any other tile switches to it.
        url = (
            request.url.remove_query_params("triage")
            if active
            else request.url.include_query_params(triage=key)
        )
        stats.append(
            {
                "key": key,
                "label": TRIAGE[key].label,
                "count": count,
                "percent": round(100 * count / len(all_rows)) if all_rows else 0,
                "active": active,
                "url": str(url),
            }
        )

    rows = select_rows(
        all_rows, triage=triage, sort_by=sort_by, descending=descending, now=now
    )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "repo": repo,
            "sort_by": sort_by,
            "descending": descending,
            "issues": rows,
            "total": len(all_rows),
            "shown": len(rows),
            "stats": stats,
            "triage": triage,
            "triage_label": TRIAGE[triage].label if triage else "",
            "clear_triage_url": str(request.url.remove_query_params("triage")),
            "fetched_at": result.fetched_at if result else None,
            "now": now,
            "error_message": error_message,
        },
    )


class LabelOut(BaseModel):
    name: str
    color: str = Field(description="6-digit hex color without '#'")


class IssueOut(BaseModel):
    number: int
    title: str
    state: str
    created_at: str
    updated_at: str
    closed_at: str | None
    author: str
    comments: int
    labels: list[LabelOut]
    url: str


class IssuesResponse(BaseModel):
    repo: str
    state: str = Field(description="Issue state fetched (currently always 'open')")
    fetched_at: datetime = Field(description="When the data was fetched (UTC)")
    total: int = Field(description="Number of open issues in the repo")
    count: int = Field(description="Number of issues returned after filtering")
    triage: str | None = Field(description="Triage filter applied, if any")
    triage_counts: dict[str, int] = Field(
        description="Open issues in each triage category (ignores the filter)"
    )
    issues: list[IssueOut]


class ErrorResponse(BaseModel):
    detail: str


class ValidationErrorDetail(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ValidationErrorResponse(BaseModel):
    detail: str | list[ValidationErrorDetail]


@app.get(
    "/api/issues",
    response_model=IssuesResponse,
    responses={
        422: {
            "model": ValidationErrorResponse,
            "description": "Invalid repository or query parameters",
        },
        502: {"model": ErrorResponse, "description": "GitHub request failed"},
    },
    summary="Open issues for a repo, as JSON",
)
def api_issues(
    repo: str = Query(
        description="Repository as 'owner/name'", examples=["napari/napari"]
    ),
    sort_by: str = Query(default="created_at", pattern=_SORT_PATTERN),
    descending: bool = Query(default=True),
    triage: str | None = Query(
        default=None,
        pattern=_TRIAGE_PATTERN,
        description="Only return issues in this triage category: " + ", ".join(TRIAGE),
    ),
    refresh: bool = Query(default=False, description="Bypass the 24-hour cache"),
) -> IssuesResponse:
    """Same data as the dashboard: cached for 24 hours, shared with the page."""
    repo = repo.strip()
    try:
        result = fetch_issues(repo=repo, state=STATE, refresh=refresh)
    except InvalidRepo as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    now = datetime.now(UTC)
    rows = select_rows(
        result.rows, triage=triage, sort_by=sort_by, descending=descending, now=now
    )
    return IssuesResponse(
        repo=repo,
        state=STATE,
        fetched_at=result.fetched_at,
        total=len(result.rows),
        count=len(rows),
        triage=triage,
        triage_counts=triage_counts(result.rows, now),
        issues=[
            IssueOut(
                **{f: getattr(row, f) for f in IssueOut.model_fields if f != "labels"},
                labels=[LabelOut(name=lb.name, color=lb.color) for lb in row.labels],
            )
            for row in rows
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the sunshine dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="default: %(default)s")
    parser.add_argument("--port", type=int, default=8000, help="default: %(default)s")
    parser.add_argument(
        "--reload", action="store_true", help="restart on code changes (development)"
    )
    args = parser.parse_args()
    uvicorn.run(
        "sunshine_dashboard.app:app", host=args.host, port=args.port, reload=args.reload
    )


if __name__ == "__main__":
    main()
