import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: 'workspace-setup.spec.ts',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:4198', browserName: 'chromium', headless: true },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4198 --strictPort',
    url: 'http://127.0.0.1:4198',
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
