---
name: pytest-coverage
description: 'Run pytest coverage for Python code, inspect missing lines, and raise backend coverage with meaningful tests. Use when asked to improve pytest coverage, inspect uncovered lines, or increase coverage for Python modules.'
---

# Pytest Coverage

Use this skill only for Python code that is tested with pytest.

## When to Use This Skill

Use this skill when you need to:
- measure coverage for backend Python modules
- inspect uncovered lines before adding tests
- improve targeted coverage for a specific file or package
- close coverage gaps without adding low-value tests

## Repo-Specific Defaults

- Run commands from the repository root.
- Use `uv run` for Python commands.
- Prefer `src` as the default coverage target unless the user asks for a narrower scope.

## Recommended Commands

Full backend coverage:

```bash
uv run pytest --cov=src --cov-report=term-missing --cov-report=annotate:cov_annotate
```

Targeted module coverage:

```bash
uv run pytest tests/test_health.py --cov=src.api.health --cov-report=term-missing --cov-report=annotate:cov_annotate
```

Package coverage:

```bash
uv run pytest tests/ --cov=src.agents --cov-report=term-missing --cov-report=annotate:cov_annotate
```

## Workflow

1. Identify the exact Python module or package that needs better coverage.
2. Run pytest with `--cov` and `--cov-report=annotate:cov_annotate`.
3. Review `term-missing` output first for a quick signal.
4. Inspect files in `cov_annotate/` when you need exact uncovered lines.
5. Add focused tests for real behavior:
   - happy path
   - edge cases
   - error handling
6. Re-run coverage until the uncovered lines are explained or covered.

## Coverage Rules

- Prefer meaningful assertions over coverage theater.
- Do not add tests that only exercise language built-ins or trivial getters.
- Mock network and Azure dependencies where possible.
- Reuse existing pytest style, fixtures, and test naming patterns.
- If 100% is impractical because of external integration boundaries, explain the blocker clearly.

## Output Expectations

When you finish, report:
- the command you ran
- the coverage target
- the main uncovered areas you found
- the tests you added or changed
- the final coverage result
