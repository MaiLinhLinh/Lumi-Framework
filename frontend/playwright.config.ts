import { defineConfig } from "@playwright/test";
import { existsSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

// Reuse an installed browser. Never download/change a machine-wide browser.
const browserRoot = resolve(process.env.LOCALAPPDATA!, "ms-playwright");
const executablePath = readdirSync(browserRoot).filter(name => name.startsWith("chromium-")).reverse()
  .map(name => resolve(browserRoot, name, "chrome-win64/chrome.exe")).find(existsSync);
export default defineConfig({
  globalTeardown: "./tests/teardown.ts",
  reporter: [["line"], ["json", { outputFile: "test-results/report.json" }]],
  testDir: "tests", testMatch: /cp[23456]\.spec\.ts/, workers: 1, timeout: 30000,
  use: { baseURL: "http://127.0.0.1:8012", headless: true, launchOptions: { executablePath, args: ["--disable-gpu", "--force-color-profile=srgb"] }, screenshot: "only-on-failure" },
  webServer: {
    command: 'C:\\Users\\ASUS\\anaconda3\\envs\\LumiMultiAgent\\python.exe tests/serve_cp2.py',
    url: "http://127.0.0.1:8012/fixture", reuseExistingServer: false, timeout: 30000,
  },
});
