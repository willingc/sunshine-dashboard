from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .fetch import FetchResult, fetch_issues, iso_to_datetime

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

app = FastAPI(title="Issue Report Dashboard")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    repo: str = Query(default="napari/napari"),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    descending: bool = Query(default=True),
    refresh: bool = Query(default=False),
) -> Response:
    repo = repo.strip()
    state = "open"
    error_message = ""
    result: FetchResult | None = None
    try:
        result = fetch_issues(repo=repo, state=state, refresh=refresh)
    except RuntimeError as exc:
        error_message = str(exc)
    else:
        if refresh:
            # Redirect so reloading or bookmarking the page doesn't refetch every time.
            return RedirectResponse(
                str(request.url.remove_query_params("refresh")), status_code=303
            )

    # Sort a copy: result.rows is the shared cached tuple.
    rows = sorted(
        result.rows if result else (),
        key=lambda row: iso_to_datetime(getattr(row, sort_by)),
        reverse=descending,
    )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "repo": repo,
            "sort_by": sort_by,
            "descending": descending,
            "issues": rows,
            "total": len(rows),
            "fetched_at": result.fetched_at if result else None,
            "now": datetime.now(UTC),
            "error_message": error_message,
        },
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
