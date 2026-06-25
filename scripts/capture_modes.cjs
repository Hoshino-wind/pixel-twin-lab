#!/usr/bin/env node
/* Capture reference/rebuilt/exact modes at the reference image's native size. */

const fs = require("node:fs");
const path = require("node:path");

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const item = argv[i];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

const INSTALL_HINT = [
  "Cannot find Playwright. Install it with:",
  "  npm i -D playwright && npx playwright install chromium",
  "System Chrome is disabled for automated backtests. Use --browser bundled.",
].join("\n");
const SYSTEM_BROWSER_ENV = "PIXEL_TWIN_ALLOW_SYSTEM_BROWSER";
const BUNDLED_BROWSER_ALIASES = new Set([
  "bundled",
  "bundled-chromium",
  "playwright",
  "managed",
  "chrome",
  "chrome-for-testing",
  "google-chrome",
  "google-chrome-stable",
  "chromium",
  "chromium-browser",
  "msedge",
  "edge",
]);

function requirePlaywright() {
  const candidates = [];
  for (const name of ["playwright", "playwright-core"]) {
    try {
      const chromium = require(name).chromium;
      const executablePath = chromium.executablePath();
      candidates.push({
        chromium,
        package: name,
        executablePath,
        browserExists: fs.existsSync(executablePath),
      });
    } catch (_) {
      // Try the next candidate.
    }
  }
  const installedBrowser = candidates.find((candidate) => candidate.browserExists);
  if (installedBrowser) {
    return installedBrowser;
  }
  if (candidates.length) {
    return candidates[0];
  }
  throw new Error(INSTALL_HINT);
}

function chromePath() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) {
    return process.env.CHROME_PATH;
  }
  const candidatesByPlatform = {
    darwin: [
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    linux: [
      "/usr/bin/google-chrome",
      "/usr/bin/google-chrome-stable",
      "/usr/bin/chromium",
      "/usr/bin/chromium-browser",
    ],
    win32: [
      "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
      "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ],
  };
  const candidates = candidatesByPlatform[process.platform] || [];
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function resolveBrowserChannel(args) {
  if (args.browser) {
    const requested = String(args.browser).toLowerCase();
    if (BUNDLED_BROWSER_ALIASES.has(requested)) {
      if (requested !== "bundled") {
        console.warn(`Treating --browser ${args.browser} as bundled Chromium. System Chrome is only available via --browser system for explicit local debugging.`);
      }
      return "bundled";
    }
    return requested;
  }
  if (process.env.PIXEL_TWIN_BROWSER && process.env.PIXEL_TWIN_BROWSER !== "bundled") {
    console.warn(
      `Ignoring PIXEL_TWIN_BROWSER=${process.env.PIXEL_TWIN_BROWSER}; capture defaults to bundled Chromium. Pass --browser system explicitly to debug system Chrome.`
    );
  }
  return "bundled";
}

function rawBrowserRequest(args) {
  if (args.browser) {
    return String(args.browser).toLowerCase();
  }
  if (process.env.PIXEL_TWIN_BROWSER) {
    return String(process.env.PIXEL_TWIN_BROWSER).toLowerCase();
  }
  return "bundled";
}

function addCodexSandboxGuidance(error) {
  if (/MachPortRendezvous|bootstrap_check_in|Permission denied \(1100\)|kill EPERM|operation not permitted/i.test(String(error.message || ""))) {
    error.message +=
      "\nChromium appears to have launched inside the Codex sandbox. Run the approved command directly from the project root so the prefix matches: node scripts/capture_modes.cjs ... --browser bundled. Do not prefix it with env, cd, /bin/zsh -lc, shell redirection, or an absolute node path.";
  }
}

function bundledBrowserMissing(error) {
  const message = String(error.message || "");
  return /Executable doesn't exist|playwright install|chromium_headless_shell/i.test(message);
}

function buildLaunchOptions(executablePath) {
  const launchOptions = {
    headless: true,
    args: ["--force-color-profile=srgb"],
  };
  if (executablePath) {
    launchOptions.executablePath = executablePath;
  }
  return launchOptions;
}

function browserSource(executablePath) {
  const normalized = String(executablePath || "").replace(/\\/g, "/");
  if (normalized.includes("/ms-playwright/")) {
    return "playwright-managed";
  }
  if (normalized.includes("/Applications/") || normalized.includes("/usr/bin/") || /^[A-Za-z]:\//.test(normalized)) {
    return "system";
  }
  return "unknown";
}

function assertSystemBrowserAllowed() {
  if (process.env[SYSTEM_BROWSER_ENV] === "1") {
    return;
  }
  throw new Error(
    "System Chrome capture is disabled by default because it launches a GUI browser and causes Codex escalation/approval failures.\n" +
      "Use --browser bundled for backtests. For one-off local debugging outside automated runs, set PIXEL_TWIN_ALLOW_SYSTEM_BROWSER=1 and pass --browser system."
  );
}

function readConfig(outDir) {
  const configPath = path.join(outDir, "lab-config.json");
  if (!fs.existsSync(configPath)) {
    return {};
  }
  return JSON.parse(fs.readFileSync(configPath, "utf8"));
}

function modeUrl(baseUrl, mode) {
  const url = new URL(baseUrl);
  url.searchParams.set("mode", mode);
  url.searchParams.set("capture", "1");
  return url.toString();
}

async function launchBrowser(args) {
  const {
    chromium,
    package: playwrightPackage,
    executablePath: playwrightExecutablePath,
    browserExists: playwrightBrowserExists,
  } = requirePlaywright();
  const requestedChannel = resolveBrowserChannel(args);
  const rawRequestedChannel = rawBrowserRequest(args);
  // Without a forced sRGB profile, screenshots inherit the display color profile
  // (especially on macOS) and every pixel drifts against the reference.
  const launchBundled = async () => {
    const executablePath = playwrightBrowserExists ? playwrightExecutablePath : undefined;
    const browser = await chromium.launch(buildLaunchOptions(executablePath));
    return {
      browser,
      playwrightPackage,
      channel: "bundled",
      requestedChannel,
      rawRequestedChannel,
      executablePath: executablePath || "bundled-chromium",
      fallbackReason: null,
    };
  };
  const launchSystem = async (fallbackReason = null) => {
    const executablePath = chromePath();
    if (!executablePath) {
      throw new Error("No system Chrome/Chromium found. Set CHROME_PATH, or install Playwright browsers with: npx playwright install chromium.");
    }
    const browser = await chromium.launch(buildLaunchOptions(executablePath));
    return {
      browser,
      playwrightPackage,
      channel: "system",
      requestedChannel,
      rawRequestedChannel,
      executablePath,
      fallbackReason,
    };
  };

  if (requestedChannel === "system") {
    assertSystemBrowserAllowed();
    try {
      return await launchSystem();
    } catch (error) {
      addCodexSandboxGuidance(error);
      throw error;
    }
  }
  if (requestedChannel !== "bundled") {
    throw new Error(`Unknown --browser value: ${requestedChannel} (expected bundled or system)`);
  }

  try {
    return await launchBundled();
  } catch (error) {
    if (bundledBrowserMissing(error)) {
      error.message =
        "Bundled Chromium is missing. Install it with: npm run install:browsers\n" +
        "Automatic system Chrome fallback is disabled because it requires GUI/sandbox escalation in Codex.\n" +
        "Use --browser bundled for backtests.\n" +
        `Original bundled error: ${error.message}`;
      throw error;
    }
    error.message += "\nBundled Chromium launch failed. Run: npm run install:browsers. Do not use system Chrome for automated backtests.";
    addCodexSandboxGuidance(error);
    throw error;
  }
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || args.h) {
    console.log("Usage: capture_modes.cjs --url http://127.0.0.1:8787/ --out-dir /abs/lab [--modes reference,rebuilt,exact] [--width 1536 --height 1024] [--browser bundled] [--wait-until networkidle|load|domcontentloaded] [--settle-ms 0]");
    console.log("Default browser: bundled. System Chrome is disabled for automated backtests.");
    console.log("Codex: run this script directly with node; use --browser bundled. Do not wrap it in a shell.");
    return;
  }
  if (!args.url || !args["out-dir"]) {
    throw new Error("Usage: capture_modes.cjs --url http://127.0.0.1:8787/ --out-dir /abs/lab");
  }

  const outDir = path.resolve(args["out-dir"]);
  const config = readConfig(outDir);
  const width = Number(args.width || config.width);
  const height = Number(args.height || config.height);
  if (!width || !height) {
    throw new Error("Pass --width/--height or provide lab-config.json with width and height.");
  }

  const modes = String(args.modes || "reference,rebuilt,exact")
    .split(",")
    .map((mode) => mode.trim())
    .filter(Boolean);
  const waitUntil = String(args["wait-until"] || "networkidle");
  const settleMs = Number(args["settle-ms"] || 0);

  const { browser, playwrightPackage, channel, requestedChannel, rawRequestedChannel, executablePath, fallbackReason } = await launchBrowser(args);
  const meta = {
    playwright_package: playwrightPackage,
    requested_browser_input: rawRequestedChannel,
    requested_browser_channel: requestedChannel,
    browser_channel: channel,
    browser_fallback_reason: fallbackReason,
    browser_version: browser.version(),
    executable_path: executablePath,
    browser_source: browserSource(executablePath),
    platform: process.platform,
    color_profile: "srgb",
    viewport: { width, height, deviceScaleFactor: 1 },
    wait_until: waitUntil,
    settle_ms: settleMs,
    modes,
  };
  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    for (const mode of modes) {
      await page.goto(modeUrl(args.url, mode), { waitUntil });
      if (settleMs > 0) {
        await page.waitForTimeout(settleMs);
      }
      await page.screenshot({ path: path.join(outDir, `${mode}-capture.png`), fullPage: false });
    }
  } finally {
    await browser.close();
  }

  fs.writeFileSync(path.join(outDir, "capture-meta.json"), JSON.stringify(meta, null, 2));
  console.log(JSON.stringify({ outDir, ...meta }, null, 2));
}

main().catch((error) => {
  console.error(error && error.message ? error.message : error);
  process.exit(1);
});
