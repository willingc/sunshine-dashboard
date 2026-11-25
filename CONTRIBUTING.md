# Contributing

Thanks for helping improve sunshine-dashboard!

## Setup

You need Python 3.15+, [uv](https://docs.astral.sh/uv/), and the
[GitHub CLI](https://cli.github.com/) logged in with `gh auth login`.
See the [README](README.md#running-locally) for how to run the dashboard
and how it finds a GitHub token.

```bash
uv sync                          # create .venv with app and dev dependencies
uv run sunshine-dashboard --reload
```

## Project layout

```
src/sunshine_dashboard/
  app.py         FastAPI app, routes, and the CLI entry point
  fetch.py       GitHub GraphQL fetching, token lookup, and caching
  templates/     Jinja2 templates (shipped inside the package)
  static/        CSS (shipped inside the package)
tests/           pytest tests; conftest.py blocks all network and gh calls
```

New template or static file types must be added to
`[tool.setuptools.package-data]` in `pyproject.toml`, or they won't be
included when the package is installed.

## Before opening a pull request

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

CI runs the same checks on every pull request. Tests must not reach GitHub:
mock `fetch._fetch_with_any_token` (or `fetch.urlopen`) instead.

If you change dependencies, commit the updated `uv.lock`. CI uses
`uv sync --locked`, which fails if the lockfile is out of date.

## Pull requests

- Branch from `main` and keep each PR focused on one change.
- Describe what changed, why, and how you tested it.
- GitHub Actions are pinned to commit SHAs. Dependabot opens PRs to update them.
