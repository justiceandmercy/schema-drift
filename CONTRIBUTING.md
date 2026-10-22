# Contributing

Thanks for your interest in contributing!

## Getting started

1. Fork the repository and create your branch from `main`.
2. Install in development mode: `pip install -e ".[dev]"` (Python) or `npm install` (JavaScript).
3. Make your change, keeping the style of the surrounding code.
4. Add or update tests for anything you touch.
5. Run the test suite and linter before opening a PR:

```sh
pytest -q        # Python
ruff check .     # lint
npm test         # JavaScript
```

## Pull requests

- Keep PRs focused; one feature or fix per PR.
- Write clear commit messages (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
- Update `CHANGELOG.md` under the *Unreleased* section when user-facing behavior changes.

## Reporting issues

Open a GitHub issue with reproduction steps, expected vs actual behavior,
and the version you are running.
