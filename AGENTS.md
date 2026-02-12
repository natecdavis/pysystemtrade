# Repository Guidelines

## Project Structure & Module Organization
- Source is split across many top-level packages prefixed with `sys*` (e.g., `syscore`, `sysdata`, `systems`, `sysexecution`). Treat each as a first-class module.
- Tests live in `tests/` and alongside packages (e.g., `sysdata/tests`, `sysobjects/tests`, `systems/tests`). Some test paths are configured in `pyproject.toml`.
- Data and configuration assets are stored in `data/` and `private/` (YAML/CSV), with additional package data under `sysdata/config` and `systems/provided`.
- Documentation and examples are in `docs/` and `examples/`.

## Build, Test, and Development Commands
- `pytest` — run the standard unit test suite.
- `pytest sysdata/tests/test_config.py` — run a single test module.
- `pytest --ignore=sysinit/futures/tests/test_sysinit_futures.py` — skip a specific test module.
- `pytest --runslow` — include tests marked `@pytest.mark.slow`.
- `black .` — format codebase with Black.
- `black . --exclude '/.venv\/.+/'` — avoid formatting a local virtual environment.

## Coding Style & Naming Conventions
- Follow PEP 8 and Black formatting (Black version 23.11.0, line length 88, Python 3.10 target).
- Prefer explicit parameters; use `arg_not_supplied` for default arguments where relevant.
- Use type hints; docstrings should be concise and avoid verbose parameter lists.
- Naming: classes favor mixedCase (single-word CamelCase); common method prefixes include `get`, `calculate`, `read`, `write`; dict-like classes may use a `dict_` prefix.
- Data hierarchy naming is important; see `docs/data.md` if adding new data objects.

## Testing Guidelines
- Pytest is the primary runner; doctests are enabled via `--doctest-modules`.
- Prefer unit tests over doctests for class methods.
- Test coverage is limited; add targeted tests for new or fixed behavior.

## Commit & Pull Request Guidelines
- Branch from `develop` using `bug-<issue#>-<description>` or `feature-<issue#>-<description>`.
- Commit messages in history are short, imperative subjects (often sentence case); include issue/PR references when relevant.
- Open PRs against `upstream/develop` with a clear summary, linked issue, and test/Black status.

## Configuration & Data Tips
- Keep local credentials/config in `private/` (not shared in PRs).
- CSV/YAML assets should live in the package data locations noted above to ensure they ship with the distribution.
