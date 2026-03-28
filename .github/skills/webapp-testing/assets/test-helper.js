/**
 * Small helpers for Playwright-based web application testing.
 */

async function waitForCondition(check, timeout = 5000, interval = 100) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeout) {
    if (await check()) {
      return true;
    }

    await new Promise((resolve) => setTimeout(resolve, interval));
  }

  throw new Error('Condition not met within timeout');
}

function collectConsoleLogs(page) {
  const entries = [];

  page.on('console', (message) => {
    entries.push({
      type: message.type(),
      text: message.text(),
      timestamp: new Date().toISOString(),
    });
  });

  return entries;
}

async function saveScreenshot(page, baseName) {
  const safeTimestamp = new Date().toISOString().replace(/[:.]/g, '-');
  const fileName = `${baseName}-${safeTimestamp}.png`;

  await page.screenshot({ path: fileName, fullPage: true });
  return fileName;
}

module.exports = {
  waitForCondition,
  collectConsoleLogs,
  saveScreenshot,
};
