# Issue Report Dashboard

A standalone web app that fetches GitHub issues and displays them in a polished, sortable dashboard.

## Features

- Pulls all issues for any GitHub repository (excluding pull requests)
- Sort and filter by:
  - creation date
  - last active date
  - state (`open`, `closed`, `all`)
- Searchable and sortable table with summary cards

## Quick Start

```bash
cd issue-report-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
issue-report-dashboard
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Authentication

The app tries `gh api` first and falls back to GitHub REST.

For higher API limits, set:

```bash
export GITHUB_TOKEN=your_token_here
```
