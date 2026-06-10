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
  'or install playwright-core and point CHROME_PATH at a Chrome/Chromium binary, then pass --browser system.',
].join("\n");

function requirePlaywright() {
  for (const name of ["playwright", "playwright-core"]) {
    try {
      return { chromium: require(name).chromium, package: name };
    } catch (_) {
      // Try the next candidate.
    }
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
  const { chromium, package: playwrightPackage } = requirePlaywright();
  // playwright-core has no bundled browser, so it implies the system browser.
  const channel = String(args.browser || (playwrightPackage === "playwright" ? "bundled" : "system"));
  // Without a forced sRGB profile, screenshots inherit the display color profile
  // (especially on macOS) and every pixel drifts against the reference.
  const launchOptions = { headless: true, args: ["--force-color-profile=srgb"] };

  if (channel === "system") {
    const executablePath = chromePath();
    if (!executablePath) {
      throw new Error("No system Chrome/Chromium found. Set CHROME_PATH, or install the full playwright package and pass --browser bundled.");
    }
    launchOptions.executablePath = executablePath;
  } else if (channel !== "bundled") {
    throw new Error(`Unknown --browser value: ${channel} (expected bundled or system)`);
  } else if (playwrightPackage === "playwright-core") {
    throw new Error("--browser bundled requires the full playwright package; playwright-core ships no browser.");
  }

  try {
    const browser = await chromium.launch(launchOptions);
    return { browser, playwrightPackage, channel, executablePath: launchOptions.executablePath || "bundled-chromium" };
  } catch (error) {
    if (channel === "bundled") {
      error.message += "\nBundled Chromium may be missing. Run: npx playwright install chromium, or pass --browser system.";
    }
    throw error;
  }
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || args.h) {
    console.log("Usage: capture_modes.cjs --url http://127.0.0.1:8787/ --out-dir /abs/lab [--modes reference,rebuilt,exact] [--width 1536 --height 1024] [--browser bundled|system]");
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

  const { browser, playwrightPackage, channel, executablePath } = await launchBrowser(args);
  const meta = {
    playwright_package: playwrightPackage,
    browser_channel: channel,
    browser_version: browser.version(),
    executable_path: executablePath,
    platform: process.platform,
    color_profile: "srgb",
    viewport: { width, height, deviceScaleFactor: 1 },
    modes,
  };
  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    for (const mode of modes) {
      await page.goto(modeUrl(args.url, mode), { waitUntil: "networkidle" });
      await page.screenshot({ path: path.join(outDir, `${mode}-capture.png`), fullPage: false });
    }
  } finally {
    await browser.close();
  }

  fs.writeFileSync(path.join(outDir, "capture-meta.json"), JSON.stringify(meta, null, 2));
  console.log(JSON.stringify({ outDir, ...meta }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
