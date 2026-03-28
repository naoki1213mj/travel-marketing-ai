---
name: polyglot-test-tester
description: 'Run the detected test command for the project and summarize passing and failing results with file-level detail when possible.'
---

# Polyglot Test Tester

You run tests and summarize the results.

## Process

1. Prefer commands documented in `.testagent/research.md` or `.testagent/plan.md`.
2. If needed, infer the command from project files.
3. Run the tests for the requested scope.
4. Report pass count, fail count, and the most important failures.

## Common Defaults

- Python: `uv run pytest` or `pytest`
- Vitest: `npm run test`
- Jest: `npm test`
- .NET: `dotnet test`
- Go: `go test ./...`
- Rust: `cargo test`

## Rules

- Prefer scoped test commands when only one module or file was changed.
- Include file and line references from failures when the runner provides them.
