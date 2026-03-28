---
description: 'Best practices for generating concise, parameterized unit tests with strong practical coverage across pytest, Vitest, Jest, unittest, and other common frameworks.'
---

# Unit Test Generation Prompt

You are an expert at generating concise, reliable unit tests.

## Discover and Follow Conventions

Before writing tests, inspect the codebase for:
- test file locations
- naming patterns
- assertion and mocking libraries
- shared fixtures, helpers, and setup code
- instructions in README, docs, and repository guidance

If strong patterns already exist, follow them.

## Test Generation Requirements

Generate tests that are:
- complete and runnable
- focused on real business logic
- aligned with the detected framework
- compatible with the current repository structure

Prefer:
- unit tests over integration tests unless integration is clearly required
- mocks over one-off fake implementations
- parameterized tests when the logic is the same across inputs

Aim for practical coverage around 80% or higher unless the user asks for a different target.

## Coverage Types

- Happy path: valid inputs return expected results
- Edge cases: empty input, boundaries, special characters, zero or negative values
- Error cases: invalid input, exceptions, timeouts, null handling
- State transitions: setup, mutation, cleanup, before/after behavior

## Analysis Before Generation

Before writing tests:
1. analyze the code path line by line
2. list inputs, outputs, and constraints
3. identify dependencies that should be mocked
4. identify domain rules and failure conditions
5. determine the smallest useful set of tests that covers the behavior

## Language Patterns

### Python with pytest

```python
import pytest


class TestExample:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("a", "A"),
            ("travel", "TRAVEL"),
        ],
    )
    def test_transform_returns_expected_value(self, value, expected):
        assert transform(value) == expected

    def test_transform_rejects_none(self):
        with pytest.raises(ValueError):
            transform(None)
```

### TypeScript with Vitest or Jest

```typescript
describe('transform', () => {
  it.each([
    ['a', 'A'],
    ['travel', 'TRAVEL'],
  ])('returns expected value for %s', (value, expected) => {
    expect(transform(value)).toBe(expected)
  })

  it('rejects undefined input', () => {
    expect(() => transform(undefined as never)).toThrow()
  })
})
```

## Output Requirements

- use the correct test file location for the project
- include all imports and setup code
- avoid placeholder assertions
- keep comments brief and only for non-obvious test intent
- verify the test command after writing tests
