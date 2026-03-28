---
name: polyglot-test-agent
description: 'Generate workable unit tests across Python, TypeScript, JavaScript, Go, Rust, Java, and more. Use when asked to create tests, improve coverage, add test files, or detect whether the project uses pytest, Vitest, Jest, or another test framework.'
---

# Polyglot Test Agent

This skill coordinates test generation across multiple languages and test frameworks.

## When to Use This Skill

Use this skill when you need to:
- generate tests for a whole project or a scoped set of files
- improve coverage in a mixed-language repository
- detect whether the project uses pytest, Vitest, Jest, unittest, go test, cargo test, or similar tooling
- create tests that compile and pass using the framework already present in the codebase

## How It Works

Use a Research -> Plan -> Implement flow.

### Research

Determine:
- the language and framework in scope
- the test framework already configured
- the build, test, and lint commands
- where tests should live
- which files are high priority

Write findings to `.testagent/research.md`.

### Plan

Create a phased plan that groups files by priority, dependency order, and complexity.

Write the plan to `.testagent/plan.md`.

### Implement

Implement one phase at a time.

For each phase:
1. read the source files fully
2. write tests that match existing conventions
3. build if applicable
4. run tests
5. fix failures before moving on

## Agent Usage

If the supporting custom agents are available, use them in this order:
- `polyglot-test-generator`
- `polyglot-test-researcher`
- `polyglot-test-planner`
- `polyglot-test-implementer`
- `polyglot-test-builder`
- `polyglot-test-tester`
- `polyglot-test-fixer`
- `polyglot-test-linter`

If those agents are unavailable, execute the same phases manually using the available tools.

## Framework Detection

Check these files first:
- `pyproject.toml`, `pytest.ini`, `tox.ini` for Python
- `package.json` for Jest, Vitest, Mocha, and frontend scripts
- `go.mod` for Go
- `Cargo.toml` for Rust
- `pom.xml`, `build.gradle`, or `gradle.properties` for Java
- `*.csproj` and solution files for .NET

If the user did not specify style or coverage goals, use the guidance in [unit-test-generation.prompt.md](unit-test-generation.prompt.md).

## State Management

Store pipeline state in `.testagent/`:
- `.testagent/research.md`
- `.testagent/plan.md`
- `.testagent/status.md`

## Repo Notes

- Python in this repo uses `uv run pytest`.
- Frontend test framework is not currently configured, so detect before writing frontend tests.
- Do not add a brand new framework unless the user explicitly asks for that setup.

## Output Expectations

Report:
- detected language and framework
- test command used
- files covered in each phase
- pass/fail status
- unresolved blockers if any
