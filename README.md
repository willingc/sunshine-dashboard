# sunshine-dashboard

Dashboard for issues

FastAPI application

## Running locally

Requires Python 3.15+, [uv](https://docs.astral.sh/uv/) and the [GitHub CLI](https://cli.github.com/), logged in with `gh auth login`.

From the repository root:

```bash
uv run sunshine-dashboard
```

Then open <http://127.0.0.1:8000>.

Options: `--host`, `--port`, and `--reload` (restart on code changes, for development).

### GitHub token

The dashboard tries these tokens in order, moving on to the next one if GitHub
rejects a token as expired or revoked (`HTTP 401: Bad credentials`):

1. `GH_TOKEN`
2. `GITHUB_TOKEN`
3. Your GitHub CLI login (`gh auth token`)

Issue data is cached for 24 hours. Use the **Refresh** button to fetch fresh data.

## JSON API

The same data is available as JSON, sharing the dashboard's 24-hour cache:

```bash
curl 'http://127.0.0.1:8000/api/issues?repo=napari/napari&triage=unlabeled'
```

The response includes `total`, `count`, `fetched_at`, `triage_counts`, and the
`issues` list. Parameters: `repo` (required), `sort_by` (`created_at` or
`updated_at`), `descending`, `triage` (`no_comments`, `unlabeled`, `stale`,
`new`), and `refresh`. Interactive docs are at <http://127.0.0.1:8000/docs>.

## Development

```bash
uv run pytest              # tests (no network access needed)
uv run ruff check .        # lint
uv run ruff format .       # format
```

CI runs these on every pull request.
