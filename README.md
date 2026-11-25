# sunshine-dashboard

Dashboard for issues

FastAPI application

## Start dashboard

Requires [uv](https://docs.astral.sh/uv/) and the [GitHub CLI](https://cli.github.com/), logged in with `gh auth login`.

```bash
GH_TOKEN=$(gh auth token) uv run sunshine-dashboard
```

Then open <http://127.0.0.1:8000>.
