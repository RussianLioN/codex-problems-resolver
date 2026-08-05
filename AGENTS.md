# Repository Guidelines

## Project Structure & Module Organization

This repository is currently a minimal scaffold. The only project directory is `tests/smart_subagents/`, reserved for checks covering smart-subagent behavior. Keep production code in a clearly named top-level directory such as `src/`, and mirror its module boundaries under `tests/`. Place reusable fixtures in `tests/fixtures/`; do not mix generated output, local logs, or temporary runtime data with committed sources.

When introducing the first implementation, add a short `README.md` that identifies the language, runtime version, entry point, and dependency installation procedure.

## Build, Test, and Development Commands

No build system, dependency manifest, or test runner is configured yet. Do not assume that `npm`, `pytest`, or `make` is available. The change that introduces a toolchain must also document reproducible commands here and in `README.md`, preferably behind stable entry points such as:

```sh
make test     # Run the complete test suite
make lint     # Run formatting and static checks
make build    # Produce distributable artifacts
```

Until then, use `find . -maxdepth 3 -type f` to inspect the repository contents.

## Coding Style & Naming Conventions

Use the canonical formatter and linter for the selected language, committed with project configuration. Avoid unrelated formatting changes. Prefer small modules with one responsibility, descriptive names, and explicit interfaces. Name test files according to the chosen framework, for example `test_scheduler.py` or `scheduler.test.ts`, and keep directory names lowercase.

## Testing Guidelines

Add tests with every behavior change and regression fix. Tests should be deterministic, isolated from external services, and safe to run concurrently. Put smart-subagent tests under `tests/smart_subagents/`. Document any required fixtures and ensure temporary resources are removed after each test. No coverage threshold exists yet; add one only with an automated coverage command and continuous-integration check.

## Commit & Pull Request Guidelines

Git history is not available in this directory, so no repository-specific convention can be inferred. Use concise Conventional Commit subjects such as `feat: add agent recovery probe` or `docs: add contributor guide`. Keep each commit focused.

Pull requests should explain the problem, summarize the solution, list verification commands and results, and link the relevant issue. Include screenshots only for user-visible interface changes. Never commit credentials, tokens, `.env` files, or machine-specific paths; provide sanitized examples instead.
