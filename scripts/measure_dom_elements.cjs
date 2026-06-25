#!/usr/bin/env node
/* Measure rendered [data-element] and [data-component] nodes in the lab's rebuilt layer, in reference-image coordinates. */

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
      `Ignoring PIXEL_TWIN_BROWSER=${process.env.PIXEL_TWIN_BROWSER}; DOM measurement defaults to bundled Chromium. Pass --browser system explicitly to debug system Chrome.`
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
      "\nChromium appears to have launched inside the Codex sandbox. Run the approved command directly from the project root so the prefix matches: node scripts/measure_dom_elements.cjs ... --browser bundled. Do not prefix it with env, cd, /bin/zsh -lc, shell redirection, or an absolute node path.";
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
    "System Chrome DOM measurement is disabled by default because it launches a GUI browser and causes Codex escalation/approval failures.\n" +
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
    console.log(
      "Usage: measure_dom_elements.cjs --url http://127.0.0.1:8787/ --out-dir /abs/lab [--mode rebuilt] [--selector [data-element]] [--browser bundled]"
    );
    console.log("Default browser: bundled. System Chrome is disabled for automated backtests.");
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

  const { browser, playwrightPackage, channel, requestedChannel, rawRequestedChannel, executablePath, fallbackReason } = await launchBrowser(args);
  let payload;
  try {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    await page.goto(modeUrl(args.url, mode), { waitUntil: "networkidle" });
    payload = await page.evaluate((sel) => {
      const stage = document.querySelector(".stage");
      const origin = stage ? stage.getBoundingClientRect() : { x: 0, y: 0, width: 0, height: 0 };
      // --- computed-style capture (for the element style contract) ---
      const toHex = (c) => {
        if (!c) return null;
        const m = String(c).match(/rgba?\(([^)]+)\)/);
        if (!m) return null;
        const p = m[1].split(",").map((s) => parseFloat(s.trim()));
        if (p.length > 3 && p[3] === 0) return null; // fully transparent -> no color
        const h = (n) => Math.max(0, Math.min(255, Math.round(n || 0))).toString(16).padStart(2, "0");
        return "#" + h(p[0]) + h(p[1]) + h(p[2]);
      };
      const round2 = (n) => Math.round((n || 0) * 100) / 100;
      const styleOf = (node) => {
        const cs = getComputedStyle(node);
        const fs = parseFloat(cs.fontSize) || 0;
        const lh = cs.lineHeight === "normal" ? round2(fs * 1.2) : (parseFloat(cs.lineHeight) || 0);
        const borderWidth = parseFloat(cs.borderTopWidth) || 0;
        return {
          font_size_px: round2(fs),
          font_weight: Number(cs.fontWeight) || ({ normal: 400, bold: 700, lighter: 300, bolder: 700 }[cs.fontWeight] || null),
          color: toHex(cs.color),
          line_height_px: round2(lh),
          letter_spacing_px: cs.letterSpacing === "normal" ? 0 : round2(parseFloat(cs.letterSpacing) || 0),
          text_align: cs.textAlign || null,
          vertical_align: cs.verticalAlign || null,
          text_transform: cs.textTransform || null,
          position: cs.position || null,
          z_index: cs.zIndex === "auto" ? null : (Number.isNaN(Number(cs.zIndex)) ? null : Number(cs.zIndex)),
          font_family: (cs.fontFamily || "").split(",")[0].replace(/["']/g, "").trim() || null,
          background_color: toHex(cs.backgroundColor),
          border_radius_px: round2(parseFloat(cs.borderTopLeftRadius) || 0),
          border_width_px: round2(borderWidth),
          border_style: cs.borderTopStyle || null,
          border_color: borderWidth > 0 ? toHex(cs.borderTopColor) : null,
          box_shadow: cs.boxShadow && cs.boxShadow !== "none" ? cs.boxShadow : null,
          opacity: round2(parseFloat(cs.opacity) || 0),
        };
      };
      const layoutOf = (node) => {
        const cs = getComputedStyle(node);
        const clientWidth = node.clientWidth || 0;
        const clientHeight = node.clientHeight || 0;
        const scrollWidth = node.scrollWidth || 0;
        const scrollHeight = node.scrollHeight || 0;
        return {
          client_width: clientWidth,
          client_height: clientHeight,
          scroll_width: scrollWidth,
          scroll_height: scrollHeight,
          offset_width: node.offsetWidth || 0,
          offset_height: node.offsetHeight || 0,
          overflow_x: cs.overflowX || null,
          overflow_y: cs.overflowY || null,
          white_space: cs.whiteSpace || null,
          text_overflow: cs.textOverflow || null,
          overflows_x: scrollWidth > clientWidth + 1,
          overflows_y: scrollHeight > clientHeight + 1,
        };
      };
      const elements = [];
      const nodePayload = (el, idAttr) => {
        const rect = el.getBoundingClientRect();
        const section = el.closest("[data-track]");
        const componentOwner = idAttr === "data-element" ? el.closest("[data-component]") : null;
        const items = el.querySelectorAll("[data-element-item]");
        const firstItem = items.length ? items[0] : null;
        const assetNode = el.matches("[data-element-asset-id]") ? el : el.querySelector("[data-element-asset-id]");
        const imgNode = el.matches("img") ? el : el.querySelector("img");
        const assetImgNode = assetNode
          ? (assetNode.matches("img") ? assetNode : assetNode.querySelector("img"))
          : null;
        return {
          id: el.getAttribute(idAttr),
          component_id: componentOwner ? componentOwner.getAttribute("data-component") : null,
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute("role") || null,
          track: section ? section.getAttribute("data-track") : null,
          text: (el.textContent || "").replace(/\s+/g, " ").trim(),
          has_svg: Boolean(el.matches("svg") || el.querySelector("svg")),
          has_img: Boolean(el.matches("img") || el.querySelector("img")),
          has_canvas: Boolean(el.matches("canvas") || el.querySelector("canvas")),
          asset_id: assetNode ? assetNode.getAttribute("data-element-asset-id") : null,
          asset_type: assetNode ? assetNode.getAttribute("data-asset-type") : null,
          img_src: imgNode ? imgNode.getAttribute("src") : null,
          asset_src: assetImgNode ? assetImgNode.getAttribute("src") : null,
          item_count: items.length,
          first_item_text: firstItem ? (firstItem.textContent || "").replace(/\s+/g, " ").trim() : null,
          bounds: {
            x: Math.round(rect.x - origin.x),
            y: Math.round(rect.y - origin.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
          },
          style: styleOf(el),
          layout: layoutOf(el),
          runs: (() => {
            const rs = el.querySelectorAll("[data-run]");
            if (!rs.length) return undefined;
            return Array.from(rs).map((r) => ({
              id: r.getAttribute("data-run"),
              text: (r.textContent || "").replace(/\s+/g, " ").trim(),
              style: styleOf(r),
            }));
          })(),
        };
      };
      for (const el of document.querySelectorAll(sel)) {
        elements.push(nodePayload(el, "data-element"));
      }
      const components = Array.from(document.querySelectorAll("[data-component]")).map((el) => nodePayload(el, "data-component"));
      return {
        stage: { x: origin.x, y: origin.y, width: Math.round(origin.width), height: Math.round(origin.height) },
        stage_found: Boolean(stage),
        elements,
        components,
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
    browser: {
      playwright_package: playwrightPackage,
      requested_browser_input: rawRequestedChannel,
      requested_browser_channel: requestedChannel,
      browser_channel: channel,
      browser_fallback_reason: fallbackReason,
      executable_path: executablePath,
      browser_source: browserSource(executablePath),
    },
    stage: payload.stage,
    element_count: payload.elements.length,
    component_count: payload.components.length,
    elements: payload.elements,
    components: payload.components,
  };
  const jsonName = String(args["json-name"] || "dom-elements.json");
  fs.writeFileSync(path.join(outDir, jsonName), JSON.stringify(report, null, 2));
  console.log(
    JSON.stringify(
      {
        outDir,
        mode,
        browser_channel: channel,
        browser_fallback_reason: fallbackReason,
        element_count: payload.elements.length,
        json: path.join(outDir, jsonName),
      },
      null,
      2
    )
  );
}

main().catch((error) => {
  console.error(error && error.message ? error.message : error);
  process.exit(1);
});
