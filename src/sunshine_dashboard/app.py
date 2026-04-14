from __future__ import annotations

from collections import Counter
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .fetch import fetch_issues, iso_to_datetime

BASE_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Issue Report Dashboard")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    repo: str = Query(default="napari/napari"),
    sort_by: str = Query(default="created_at", pattern="^(created_at|updated_at)$"),
    descending: bool = Query(default=True),
) -> HTMLResponse:
    state = "open"
    error_message = ""
    rows = []
    try:
        rows = fetch_issues(repo=repo, state=state)
        rows.sort(key=lambda row: iso_to_datetime(getattr(row, sort_by)), reverse=descending)
    except RuntimeError as exc:
        error_message = str(exc)
    counts = Counter(row.state for row in rows)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "repo": repo,
            "sort_by": sort_by,
            "descending": descending,
            "issues": rows,
            "total": len(rows),
            "open_count": counts.get("open", 0),
            "closed_count": counts.get("closed", 0),
            "error_message": error_message,
        },
    )


def main() -> None:
    uvicorn.run("issue_report_dashboard.app:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
