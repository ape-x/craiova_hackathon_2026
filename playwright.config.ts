import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5186",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: ".venv/bin/uvicorn backend.main:app --port 8186",
      url: "http://127.0.0.1:8186/api/health",
      reuseExistingServer: false,
      env: { STRANDED_DB: "/tmp/stranded-e2e.sqlite3" },
    },
    {
      command: "npm run dev -- --port 5186 --strictPort",
      url: "http://127.0.0.1:5186",
      reuseExistingServer: false,
      env: { STRANDED_API_TARGET: "http://127.0.0.1:8186" },
    },
  ],
});
