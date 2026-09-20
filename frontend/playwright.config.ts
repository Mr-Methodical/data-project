import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python =
  process.env.RINKCHECK_TEST_PYTHON ||
  (existsSync(path.join(root, ".venv/bin/python"))
    ? path.join(root, ".venv/bin/python")
    : "python3");
const external = process.env.RINKCHECK_BASE_URL;
const baseURL = external || "http://127.0.0.1:8011";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.RINKCHECK_BROWSER_EXECUTABLE
      ? {
          executablePath: process.env.RINKCHECK_BROWSER_EXECUTABLE,
          args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        }
      : undefined,
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
      },
    },
  ],
  webServer: external
    ? undefined
    : {
        command: `"${python}" -m uvicorn rinkcheck.api:app --host 127.0.0.1 --port 8011`,
        cwd: root,
        env: {
          PYTHONPATH: path.join(root, "backend"),
          RINKCHECK_DB_PATH: path.join(
            os.tmpdir(),
            `rinkcheck-browser-${Date.now()}.sqlite3`,
          ),
          RINKCHECK_SEED_DEMO: "true",
        },
        url: `${baseURL}/api/health`,
        reuseExistingServer: false,
        timeout: 30_000,
      },
});
