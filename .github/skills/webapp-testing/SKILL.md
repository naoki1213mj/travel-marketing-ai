---
name: webapp-testing
description: 'Test local or remote web applications with Playwright. Use when asked to verify frontend behavior, debug UI flows, capture screenshots, inspect browser logs, or run browser-based end-to-end checks.'
---

# Web Application Testing

Use this skill for browser-based testing and debugging.

Prefer the Playwright MCP Server when it is available. If it is not available, fall back to a local Node.js environment with Playwright installed.

## When to Use This Skill

Use this skill when you need to:
- test frontend functionality in a real browser
- validate user flows and form submissions
- inspect browser console errors
- capture screenshots for debugging
- verify responsive behavior across viewports
- reproduce and isolate UI regressions

## Prerequisites

- Node.js must be available.
- The target app must be running locally or deployed to an accessible URL.
- For this repo, the usual frontend URL is the Vite dev server on `http://localhost:5173`.

## Core Capabilities

### Browser automation

- open pages
- click buttons and links
- fill forms
- wait for navigation and visible elements
- resize the viewport

### Verification

- assert element presence
- verify text content
- confirm URL changes
- check visibility and responsive layouts

### Debugging

- capture screenshots
- inspect console logs
- inspect network failures when needed

## Workflow

1. Confirm the target URL and that the app is running.
2. Start with a simple smoke path before deeper flows.
3. Use stable selectors such as role, label, or `data-testid`.
4. Capture screenshots when a flow fails or looks wrong.
5. Summarize observed behavior and any failing step precisely.

## Guidelines

1. Always verify the app is reachable before testing.
2. Use explicit waits for navigation and visible elements.
3. Prefer role-based selectors and `data-testid` over brittle CSS chains.
4. Clean up browser contexts and tabs when done.
5. Test incrementally: smoke path first, then edge cases.

## Common Patterns

Wait for a visible element:

```javascript
await page.waitForSelector('[data-testid="submit"]', { state: 'visible' })
```

Capture console messages:

```javascript
page.on('console', (message) => {
  console.log(message.type(), message.text())
})
```

Capture a screenshot on failure:

```javascript
try {
  await page.click('button[type="submit"]')
} catch (error) {
  await page.screenshot({ path: 'debug-submit.png', fullPage: true })
  throw error
}
```

## Helper Functions

Helper utilities live in [assets/test-helper.js](./assets/test-helper.js).
Use them for repeated patterns such as waiting on conditions, recording console logs, and timestamped screenshots.

## Limitations

- This skill is for browser testing, not native mobile apps.
- Complex auth flows may require app-specific setup.
- If Playwright is not configured in the project, you may need to use the MCP path or install Playwright explicitly.
