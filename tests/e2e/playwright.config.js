// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: '.',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:8010',
    headless: true,
    actionTimeout: 15_000,
  },
});
