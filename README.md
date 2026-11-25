# sunshine-dashboard

Dashboard for issues

FastAPI application

## Running locally

Requires [uv](https://docs.astral.sh/uv/) and the [GitHub CLI](https://cli.github.com/), logged in with `gh auth login`.

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
