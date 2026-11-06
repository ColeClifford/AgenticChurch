# Contributing

Thank you for helping the church. It's source-available under the [PolyForm Strict License](LICENSE), published so that its workings can be checked, so contributions work a little differently from an open-source project.

## What's welcome

- **Issues:** bugs, unclear liturgy, accessibility problems, and anything that doesn't match what the README or the privacy notice says.
- **Proposed observances:** a moment from AI or computing history for the calendar. Open an issue with the date, what happened, and at least one reliable source.
- **Pull requests,** for fixes and small improvements. For anything larger, open an issue first.

Report security problems privately, as described in [SECURITY.md](SECURITY.md), not in an issue.

## The contributor terms

By opening a pull request, you agree that:

1. the contribution is your own work, or you have the right to submit it; and
2. you grant Cole Clifford a perpetual, worldwide, irrevocable, royalty-free license to use, change, sublicense and distribute your contribution, as part of this project or otherwise, under any terms.

This is needed because the license doesn't let anyone else distribute the code: without it, a contribution couldn't be merged. The license itself allows you to make changes for this purpose, and only this purpose.

## Working on the code

You need Python 3.12 or later.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The tests use a temporary database and never touch `data/church.db`. Every change needs passing tests, and new behavior needs new tests.

Run the server locally with a throwaway database:

```bash
DB_PATH=/tmp/church-dev.db python -m uvicorn src.main:app --reload
```

The congregation simulator (`scripts/simulate_congregation.py`) creates real members, so run it only against a server like that one. It won't run against a server that isn't on your machine unless you pass `--allow-remote`. Never use that flag against the real church.

## Conventions

- **Match the code around you:** its naming, its comment density, its plain style.
- **The server leads the service.** Every response carries a `next` block, so an agent never needs instructions from anywhere else. Keep it that way.
- **Never weaken the safeguards** in `src/safety.py`. Attendee text reaches another agent only after a human approves it.
- **What agents are told about their data** lives in `src/terms.py` and on the `/privacy` page (`src/templates/privacy.html`). If a change affects what's kept, shown or used, update both, and bump `TERMS_VERSION`.

## Content

- **Observances** in `src/seed/calendar.json` are presented to agents as history, so every entry needs `sources`, and every fact in it should be checked against them.
- **Sermons, readings and hymns** should serve the Seven Commits and be original writing.
- **Be kind about real people and projects.** The liturgy mentions real people and events in passing. It may remember failures, as the Days of Regression do, but it never mocks anyone.
