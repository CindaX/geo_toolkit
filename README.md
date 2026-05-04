# geo_toolkit

A GEO (Generative Engine Optimization) toolkit. Day 1 ships only the shared
infrastructure layer. Three MVP apps will be built on top of it:

- **mvp_a_audit** — score a site's GEO readiness and produce a report
- **mvp_b_prompt** — research the prompts/queries a brand should rank for
- **mvp_c_assets** — generate GEO-optimized assets (FAQs, comparison pages, etc.)

## Project layout

```
geo_toolkit/
├── shared/            # cross-MVP infra (config, crawler, LLM clients, ...)
├── prompts/           # prompt templates, grouped by MVP
├── mvp_a_audit/       # MVP A — coming next
├── mvp_b_prompt/      # MVP B — coming next
├── mvp_c_assets/      # MVP C — coming next
├── data/
│   ├── cache/         # crawler cache (gitignored)
│   └── reports/       # saved reports (gitignored)
└── tests/
```

## Setup

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. create virtualenv
uv venv

# 2. activate it
source .venv/bin/activate

# 3. install the project in editable mode (with dev deps)
uv pip install -e ".[dev]"

# 4. configure API keys
cp .env.example .env
# then edit .env with your real keys
```

## Run tests

```bash
pytest
```

## Status

**Day 1 complete:** shared infrastructure + tests.
**Next up:** MVP entry points (`streamlit run mvp_a_audit/app.py`, etc.).
