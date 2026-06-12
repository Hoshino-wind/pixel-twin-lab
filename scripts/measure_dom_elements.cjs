#!/usr/bin/env node
/* Measure rendered [data-element] nodes in the lab's rebuilt layer, in reference-image coordinates. */

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
  const channel = String(args.browser || (playwrightPackage === "playwright" ? "bundled" : "system"));
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

  const browser = await chromium.launch(launchOptions);
  return browser;
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || args.h) {
    console.log(
      "Usage: measure_dom_elements.cjs --url http://127.0.0.1:8787/ --out-dir /abs/lab [--mode rebuilt] [--selector [data-element]] [--browser bundled|system]"
    );
    return;
  }
  if (!args.url || !args["out-dir"]) {
    throw new Error("Usage: measure_dom_elements.cjs --url http://127.0.0.1:8787/ --out-dir /abs/lab");
  }

  const outDir = path.resolve(args["out-dir"]);
  const config = readConfig(outDir);
  const width = Number(args.width || config.width);
  const height = Number(args.height || config.height);
  if (!width || !height) {
    throw new Error("Pass --width/--height or provide lab-config.json with width and height.");
  }
  const mode = String(args.mode || "rebuilt");
  const selector = String(args.selector || "[data-element]");

  const browser = await launchBrowser(args);
  let payload;
  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    await page.goto(modeUrl(args.url, mode), { waitUntil: "networkidle" });
    payload = await page.evaluate((sel) => {
      const stage = document.querySelector(".stage");
      const origin = stage ? stage.getBoundingClientRect() : { x: 0, y: 0, width: 0, height: 0 };
      const elements = [];
      for (const el of document.querySelectorAll(sel)) {
        const rect = el.getBoundingClientRect();
        const section = el.closest("[data-track]");
        const items = el.querySelectorAll("[data-element-item]");
        const firstItem = items.length ? items[0] : null;
        elements.push({
          id: el.getAttribute("data-element"),
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute("role") || null,
          track: section ? section.getAttribute("data-track") : null,
          text: (el.textContent || "").replace(/\s+/g, " ").trim(),
          has_svg: Boolean(el.matches("svg") || el.querySelector("svg")),
          has_img: Boolean(el.matches("img") || el.querySelector("img")),
          has_canvas: Boolean(el.matches("canvas") || el.querySelector("canvas")),
          item_count: items.length,
          first_item_text: firstItem ? (firstItem.textContent || "").replace(/\s+/g, " ").trim() : null,
          bounds: {
            x: Math.round(rect.x - origin.x),
            y: Math.round(rect.y - origin.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
        });
      }
      return {
        stage: { x: origin.x, y: origin.y, width: Math.round(origin.width), height: Math.round(origin.height) },
        stage_found: Boolean(stage),
        elements,
      };
    }, selector);
  } finally {
    await browser.close();
  }

  if (!payload.stage_found) {
    console.error("Warning: .stage element not found; element coordinates are viewport-relative, not reference-relative.");
  }

  const report = {
    generated_at: new Date().toISOString(),
    url: args.url,
    mode,
    selector,
    viewport: { width, height, deviceScaleFactor: 1 },
    stage: payload.stage,
    element_count: payload.elements.length,
    elements: payload.elements,
  };
  const jsonName = String(args["json-name"] || "dom-elements.json");
  fs.writeFileSync(path.join(outDir, jsonName), JSON.stringify(report, null, 2));
  console.log(
    JSON.stringify(
      {
        outDir,
        mode,
        element_count: payload.elements.length,
        json: path.join(outDir, jsonName),
      },
      null,
      2
    )
  );
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
