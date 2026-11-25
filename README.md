# sunshine-dashboard

Dashboard for issues

FastAPI application

## Running locally

Requires [uv](https://docs.astral.sh/uv/) and the [GitHub CLI](https://cli.github.com/), logged in with `gh auth login`.

From the repository root:

```bash
GH_TOKEN=$(env -u GH_TOKEN gh auth token) uv run sunshine-dashboard
```

Then open <http://127.0.0.1:8000>.

The `env -u GH_TOKEN` part makes `gh` return your logged-in (keyring) token
even if a `GH_TOKEN` is already set in your shell. Without it, `gh auth token`
just echoes that existing value, and if it's expired or revoked the dashboard
shows `HTTP 401: Bad credentials`.
