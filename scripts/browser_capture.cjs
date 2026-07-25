#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const MAX_CAPTURE_SIDE = 10_000;
const MAX_CAPTURE_PIXELS = 9_000_000;

function parseArgs(argv) {
  const args = {};
  for (let index = 2; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) continue;
    const key = item.slice(2);
    const next = argv[index + 1];
    const repeated = key === "dynamic-selector";
    if (!next || next.startsWith("--")) {
      if (repeated) {
        if (!Array.isArray(args[key])) args[key] = [];
        args[key].push(true);
      } else {
        args[key] = true;
      }
    } else {
      if (repeated) {
        if (!Array.isArray(args[key])) args[key] = [];
        args[key].push(next);
      } else {
        args[key] = next;
      }
      index += 1;
    }
  }
  return args;
}

function usage() {
  return [
    "Capture one page into a caller-owned temporary file.",
    "",
    "Usage:",
    "  node scripts/browser_capture.cjs --url http://127.0.0.1:3000/ \\",
    "    --output /tmp/actual.png --width 1440 --height 900 [--selector '#app'] \\",
    "    [--dynamic-selector '.chart'] [--sample-output /tmp/sample.png]",
    "",
    "Repeat --dynamic-selector for up to three explicit dynamic regions.",
    "Visible canvas/video and large complex SVG regions are detected unless --no-auto-dynamic is set.",
    "Only localhost URLs are accepted unless --allow-remote is explicit.",
  ].join("\n");
}

function positiveInteger(value, fallback, label) {
  const parsed = value === undefined ? fallback : Number.parseInt(String(value), 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${label} must be a positive integer`);
  }
  return parsed;
}

function validateCaptureSize(width, height, label = "viewport") {
  if (width > MAX_CAPTURE_SIDE || height > MAX_CAPTURE_SIDE) {
    throw new Error(`${label} must be at most ${MAX_CAPTURE_SIDE} pixels per side`);
  }
  if (width * height > MAX_CAPTURE_PIXELS) {
    throw new Error(`${label} must not exceed ${MAX_CAPTURE_PIXELS} total pixels`);
  }
}

function colorScheme(value) {
  const scheme = String(value || "light");
  if (!new Set(["light", "dark", "no-preference"]).has(scheme)) {
    throw new Error("color-scheme must be light, dark, or no-preference");
  }
  return scheme;
}

function requireLocalUrl(value, allowRemote) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch (_) {
    throw new Error(`Invalid URL: ${value}`);
  }
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    throw new Error("Only HTTP(S) URLs are supported");
  }
  const localHosts = new Set(["127.0.0.1", "::1", "localhost"]);
  if (!allowRemote && !localHosts.has(parsed.hostname)) {
    throw new Error("Remote capture is disabled; pass --allow-remote explicitly");
  }
  return parsed.toString();
}

async function settlePage(page, waitMs) {
  try {
    await page.waitForLoadState("load", { timeout: 5_000 });
  } catch (_) {
    // Some development servers keep the load event open. The bounded asset
    // checks below still provide a deterministic readiness signal.
  }

  const assets = await page.evaluate(async () => {
    const bounded = (promise, timeoutMs, fallback) =>
      Promise.race([
        promise,
        new Promise((resolve) => setTimeout(() => resolve(fallback), timeoutMs)),
      ]);

    let fontsReady = true;
    if (document.fonts && document.fonts.ready) {
      fontsReady = await bounded(
        document.fonts.ready.then(() => true, () => false),
        2_000,
        false
      );
    }

    const allImages = Array.from(document.images);
    const images = allImages.slice(0, 200);
    await Promise.all(
      images.map(async (image) => {
        if (!image.complete) {
          await bounded(
            new Promise((resolve) => {
              image.addEventListener("load", resolve, { once: true });
              image.addEventListener("error", resolve, { once: true });
            }),
            2_000,
            null
          );
        }
        if (image.complete && typeof image.decode === "function") {
          await bounded(image.decode().catch(() => null), 2_000, null);
        }
      })
    );

    return {
      fonts_ready: Boolean(fontsReady && (!document.fonts || document.fonts.status === "loaded")),
      image_count: allImages.length,
      decoded_images: images.length,
      pending_images: allImages.filter((image) => !image.complete).length,
      failed_images: allImages.filter(
        (image) => image.complete && image.naturalWidth === 0
      ).length,
    };
  });

  await page.waitForTimeout(waitMs);
  let previous = null;
  let stableFrames = 0;
  let checks = 0;
  while (checks < 6 && stableFrames < 2) {
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve())));
    const signature = await page.evaluate(() => {
      const root = document.documentElement;
      const nodes = [];
      for (const node of document.querySelectorAll("body *")) {
        if (nodes.length >= 240) break;
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        if (
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 0 &&
          rect.height > 0
        ) {
          nodes.push(node);
        }
      }
      const values = [
        innerWidth,
        innerHeight,
        root ? root.scrollWidth : 0,
        root ? root.scrollHeight : 0,
      ];
      for (const node of nodes) {
        const rect = node.getBoundingClientRect();
        values.push(
          Math.round(rect.x * 4),
          Math.round(rect.y * 4),
          Math.round(rect.width * 4),
          Math.round(rect.height * 4)
        );
      }
      return values.join(":");
    });
    stableFrames = signature === previous ? stableFrames + 1 : 0;
    previous = signature;
    checks += 1;
  }

  return {
    ...assets,
    layout_stable: stableFrames >= 2,
    stability_checks: checks,
    stable: Boolean(
      assets.fonts_ready &&
        assets.pending_images === 0 &&
        assets.failed_images === 0 &&
        stableFrames >= 2
    ),
  };
}

async function collectEvidence(page, frame, dynamicSelectors, autoDynamic, captureRoot) {
  return page.evaluate(
    ({ captureFrame, explicitSelectors, detectAutomatically, limits, selectedRoot }) => {
      const frameLeft = captureFrame.x;
      const frameTop = captureFrame.y;
      const frameRight = frameLeft + captureFrame.width;
      const frameBottom = frameTop + captureFrame.height;
      const excludedTags = new Set([
        "script",
        "style",
        "link",
        "meta",
        "noscript",
        "template",
        "title",
      ]);
      const mediaTags = new Set(["canvas", "video", "iframe", "img", "svg"]);
      const controlTags = new Set(["button", "input", "select", "textarea", "summary"]);
      const drawableSvgSelector = [
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "use",
        "image",
        "foreignObject",
      ].join(",");

      const rounded = (value) => Math.round(value * 100) / 100;
      const clippedValue = (value) => {
        const compact = String(value || "").replace(/\s+/g, " ").trim();
        return compact.length <= limits.maxStyleLength
          ? compact
          : compact.slice(0, limits.maxStyleLength - 1) + "\u2026";
      };
      const stableToken = (value, maximum) => {
        const token = String(value || "");
        return (
          token.length > 0 &&
          token.length <= maximum &&
          /^[A-Za-z_][A-Za-z0-9_-]*$/.test(token) &&
          !/[a-f0-9]{12,}/i.test(token) &&
          !/\d{6,}/.test(token)
        );
      };
      const selectorCount = (selector) => {
        try {
          return document.querySelectorAll(selector).length;
        } catch (_) {
          return 0;
        }
      };
      const selectorSegment = (element) => {
        const tag = String(element.localName || "div").toLowerCase();
        const classes = Array.from(element.classList || [])
          .filter((name) => stableToken(name, 32))
          .slice(0, 2)
          .map((name) => `.${CSS.escape(name)}`)
          .join("");
        let segment = `${tag}${classes}`;
        const parent = element.parentElement;
        if (parent) {
          const siblings = Array.from(parent.children).filter(
            (sibling) => sibling.localName === element.localName
          );
          if (siblings.length > 1) {
            segment += `:nth-of-type(${siblings.indexOf(element) + 1})`;
          }
        }
        return segment;
      };
      const safeSelector = (element) => {
        if (stableToken(element.id, 48)) {
          const byId = `#${CSS.escape(element.id)}`;
          if (byId.length <= limits.maxSelectorLength && selectorCount(byId) === 1) {
            return { selector: byId, unique: true };
          }
        }

        const ownSegment = selectorSegment(element);
        if (
          ownSegment.length <= limits.maxSelectorLength &&
          selectorCount(ownSegment) === 1
        ) {
          return { selector: ownSegment, unique: true };
        }

        const segments = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE && segments.length < 6) {
          if (stableToken(current.id, 48)) {
            const idSegment = `#${CSS.escape(current.id)}`;
            if (idSegment.length <= limits.maxSelectorLength) segments.unshift(idSegment);
          } else {
            segments.unshift(selectorSegment(current));
          }
          const candidate = segments.join(" > ");
          if (
            candidate.length <= limits.maxSelectorLength &&
            selectorCount(candidate) === 1
          ) {
            return { selector: candidate, unique: true };
          }
          current = current.parentElement;
        }

        const fallback = ownSegment.length <= limits.maxSelectorLength
          ? ownSegment
          : String(element.localName || "div").toLowerCase();
        return { selector: fallback, unique: selectorCount(fallback) === 1 };
      };
      const intersection = (rect) => {
        const left = Math.max(frameLeft, rect.left);
        const top = Math.max(frameTop, rect.top);
        const right = Math.min(frameRight, rect.right);
        const bottom = Math.min(frameBottom, rect.bottom);
        if (right <= left || bottom <= top) return null;
        return {
          area: (right - left) * (bottom - top),
          visible_bounds: [
            rounded(left - frameLeft),
            rounded(top - frameTop),
            rounded(right - left),
            rounded(bottom - top),
          ],
        };
      };
      const visibleElement = (element) => {
        if (!(element instanceof Element) || excludedTags.has(element.localName)) return null;
        const style = getComputedStyle(element);
        if (
          style.display === "none" ||
          style.visibility === "hidden" ||
          style.visibility === "collapse" ||
          Number.parseFloat(style.opacity || "1") <= 0.001
        ) {
          return null;
        }
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return null;
        const overlap = intersection(rect);
        return overlap
          ? {
              style,
              rect,
              bounds: [
                rounded(rect.left - frameLeft),
                rounded(rect.top - frameTop),
                rounded(rect.width),
                rounded(rect.height),
              ],
              overlap,
            }
          : null;
      };
      const compactStyles = (style) => {
        const values = {
          display: style.display,
          position: style.position,
          color: style.color,
          "background-color": style.backgroundColor,
          "font-family": style.fontFamily,
          "font-size": style.fontSize,
          "font-weight": style.fontWeight,
          "line-height": style.lineHeight,
          "letter-spacing": style.letterSpacing,
          "text-align": style.textAlign,
          padding: style.padding,
          margin: style.margin,
          gap: style.gap,
          "flex-direction": style.flexDirection,
          "align-items": style.alignItems,
          "justify-content": style.justifyContent,
          "grid-template-columns": style.gridTemplateColumns,
          border: style.border,
          "border-radius": style.borderRadius,
          "box-shadow": style.boxShadow,
          opacity: style.opacity,
          transform: style.transform,
          "object-fit": style.objectFit,
          "object-position": style.objectPosition,
        };
        return Object.fromEntries(
          Object.entries(values).map(([key, value]) => [key, clippedValue(value)])
        );
      };
      const elementDepth = (element) => {
        let depth = 0;
        for (let current = element.parentElement; current; current = current.parentElement) {
          depth += 1;
        }
        return depth;
      };
      const elementRole = (element, style) => {
        const tag = String(element.localName || "element").toLowerCase();
        if (mediaTags.has(tag)) return tag;
        if (controlTags.has(tag) || element.getAttribute("role") === "button") return "control";
        const hasDirectText = Array.from(element.childNodes).some(
          (child) => child.nodeType === Node.TEXT_NODE && /\S/.test(child.nodeValue || "")
        );
        if (hasDirectText) return "text";
        if (style.display.includes("flex") || style.display.includes("grid")) return "container";
        if (
          style.backgroundColor !== "rgba(0, 0, 0, 0)" ||
          style.borderTopStyle !== "none" ||
          style.boxShadow !== "none"
        ) {
          return "surface";
        }
        return "element";
      };
      const priorityFor = (role) => {
        if (["canvas", "video", "iframe", "img", "svg"].includes(role)) return 5;
        if (role === "control") return 4;
        if (role === "text") return 3;
        if (role === "surface") return 2;
        if (role === "container") return 1;
        return 0;
      };
      const dynamicDescriptor = (element, source, kind, info) => {
        const selector = safeSelector(element);
        return {
          source,
          kind,
          selector: selector.selector,
          selector_unique: selector.unique,
          bounds: info.overlap.visible_bounds,
          _element: element,
          _area: info.overlap.area,
        };
      };

      const explicitRegions = [];
      const explicitElements = new Set();
      for (let index = 0; index < explicitSelectors.length; index += 1) {
        let matches;
        try {
          matches = document.querySelectorAll(explicitSelectors[index]);
        } catch (_) {
          throw new Error(`dynamic-selector #${index + 1} is not valid CSS`);
        }
        let selected = null;
        let selectedInfo = null;
        for (const match of matches) {
          const info = visibleElement(match);
          if (info) {
            selected = match;
            selectedInfo = info;
            break;
          }
        }
        if (!selected || !selectedInfo) {
          throw new Error(
            `dynamic-selector #${index + 1} did not match a visible element in the capture frame`
          );
        }
        if (!explicitElements.has(selected)) {
          explicitElements.add(selected);
          explicitRegions.push(
            dynamicDescriptor(
              selected,
              "explicit",
              String(selected.localName || "element").toLowerCase(),
              selectedInfo
            )
          );
        }
      }

      const automaticRegions = [];
      if (detectAutomatically) {
        for (const element of document.querySelectorAll("canvas,video,svg")) {
          if (explicitElements.has(element)) continue;
          const info = visibleElement(element);
          if (!info) continue;
          const tag = String(element.localName || "element").toLowerCase();
          if (tag === "svg") {
            const drawableCount = element.querySelectorAll(drawableSvgSelector).length;
            const minimumArea = Math.max(4096, captureFrame.width * captureFrame.height * 0.04);
            if (
              drawableCount < 12 ||
              info.rect.width < 96 ||
              info.rect.height < 64 ||
              info.overlap.area < minimumArea
            ) {
              continue;
            }
          } else if (
            info.rect.width < 16 ||
            info.rect.height < 16 ||
            info.overlap.area < 256
          ) {
            continue;
          }
          automaticRegions.push(dynamicDescriptor(element, "auto", tag, info));
        }
        automaticRegions.sort((left, right) => right._area - left._area);
      }

      const selectedDynamicRegions = [];
      const selectedDynamicElements = new Set();
      for (const region of [...explicitRegions, ...automaticRegions]) {
        if (selectedDynamicRegions.length >= limits.maxDynamicRegions) break;
        if (selectedDynamicElements.has(region._element)) continue;
        if (
          region.source === "auto" &&
          explicitRegions.some((explicit) => {
            const [x, y, width, height] = region.bounds;
            const [explicitX, explicitY, explicitWidth, explicitHeight] = explicit.bounds;
            const overlapWidth = Math.max(
              0,
              Math.min(x + width, explicitX + explicitWidth) - Math.max(x, explicitX)
            );
            const overlapHeight = Math.max(
              0,
              Math.min(y + height, explicitY + explicitHeight) - Math.max(y, explicitY)
            );
            return overlapWidth * overlapHeight >= width * height * 0.9;
          })
        ) {
          continue;
        }
        selectedDynamicElements.add(region._element);
        selectedDynamicRegions.push(region);
      }

      const candidates = [];
      const visitedElements = new Set();
      let scanned = 0;
      const scanElement = (element, scopePriority) => {
        if (
          scanned >= limits.maxScannedNodes ||
          !(element instanceof Element)
        ) {
          return;
        }
        scanned += 1;
        if (visitedElements.has(element)) return;
        visitedElements.add(element);
        if (element.ownerSVGElement && element.localName !== "svg") return;
        const info = visibleElement(element);
        if (!info) return;
        const role = elementRole(element, info.style);
        candidates.push({
          element,
          style: info.style,
          bounds: info.bounds,
          visibleBounds: info.overlap.visible_bounds,
          role,
          priority: priorityFor(role),
          scopePriority,
          depth: elementDepth(element),
          area: info.rect.width * info.rect.height,
          order: scanned,
        });
      };

      if (selectedRoot instanceof Element && selectedRoot.isConnected) {
        scanElement(selectedRoot, 4);
        let ancestor = selectedRoot.parentElement;
        while (ancestor && scanned < limits.maxScannedNodes) {
          scanElement(ancestor, 3);
          ancestor = ancestor.parentElement;
        }
        for (const element of selectedRoot.querySelectorAll("*")) {
          if (scanned >= limits.maxScannedNodes) break;
          scanElement(element, 2);
        }
      }
      for (const element of document.querySelectorAll("body *")) {
        if (scanned >= limits.maxScannedNodes) break;
        scanElement(element, 0);
      }
      candidates.sort(
        (left, right) =>
          (selectedRoot instanceof Element
            ? right.scopePriority - left.scopePriority
            : 0) ||
          right.priority - left.priority ||
          right.depth - left.depth ||
          left.area - right.area ||
          left.order - right.order
      );
      const nodes = candidates.slice(0, limits.maxIndexedNodes).map((candidate) => {
        const selector = safeSelector(candidate.element);
        return {
          selector: selector.selector,
          selector_unique: selector.unique,
          unique: selector.unique,
          tag: String(candidate.element.localName || "element").toLowerCase(),
          role: candidate.role,
          bounds: candidate.bounds,
          visible_bounds: candidate.visibleBounds,
          depth: candidate.depth,
          visual: candidate.role !== "element",
          has_direct_text: candidate.role === "text",
          computed: compactStyles(candidate.style),
        };
      });
      const dynamicRegions = selectedDynamicRegions.map((region, index) => ({
        id: `${region.source}:${region.kind}:${region.selector}`.slice(0, 180),
        rank: index + 1,
        source: region.source,
        kind: region.kind,
        selector: region.selector,
        selector_unique: region.selector_unique,
        bounds: region.bounds,
      }));

      return {
        dom_index: {
          space: "capture-css-px",
          coordinate_space: "capture-css-px",
          frame: {
            kind: captureFrame.kind,
            viewport_bounds: [
              rounded(captureFrame.x),
              rounded(captureFrame.y),
              rounded(captureFrame.width),
              rounded(captureFrame.height),
            ],
            size: [rounded(captureFrame.width), rounded(captureFrame.height)],
            scroll: [rounded(window.scrollX), rounded(window.scrollY)],
          },
          truncated:
            candidates.length > limits.maxIndexedNodes || scanned >= limits.maxScannedNodes,
          scanned,
          nodes,
        },
        dynamic_regions: dynamicRegions,
      };
    },
    {
      captureFrame: frame,
      explicitSelectors: dynamicSelectors,
      detectAutomatically: autoDynamic,
      selectedRoot: captureRoot,
      limits: {
        maxIndexedNodes: 240,
        maxScannedNodes: 4000,
        maxDynamicRegions: 3,
        maxSelectorLength: 160,
        maxStyleLength: 80,
      },
    }
  );
}

async function waitForTemporalSample(page) {
  await page.waitForTimeout(120);
  await page.evaluate(
    () =>
      new Promise((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(resolve));
      })
  );
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help || args.h) {
    process.stdout.write(usage() + "\n");
    return 0;
  }
  if (!args.url || !args.output) {
    throw new Error(usage());
  }

  const url = requireLocalUrl(String(args.url), Boolean(args["allow-remote"]));
  const width = positiveInteger(args.width, 1440, "width");
  const height = positiveInteger(args.height, 900, "height");
  validateCaptureSize(width, height);
  const waitMs = positiveInteger(args["wait-ms"], 250, "wait-ms");
  const preferredColorScheme = colorScheme(args["color-scheme"]);
  const output = path.resolve(String(args.output));
  const dynamicSelectors = Array.isArray(args["dynamic-selector"])
    ? args["dynamic-selector"]
    : [];
  if (dynamicSelectors.some((selector) => typeof selector !== "string" || !selector.trim())) {
    throw new Error("--dynamic-selector requires a CSS selector value");
  }
  if (dynamicSelectors.length > 3) {
    throw new Error("--dynamic-selector may be repeated at most three times");
  }
  const sampleOutput = args["sample-output"]
    ? path.resolve(String(args["sample-output"]))
    : null;
  if (sampleOutput && sampleOutput === output) {
    throw new Error("--sample-output must differ from --output");
  }
  fs.mkdirSync(path.dirname(output), { recursive: true });

  let chromium;
  try {
    chromium = require("playwright").chromium;
  } catch (_) {
    throw new Error(
      "Playwright is required. Run npm install and npm run install:browsers in pixel-twin-lab."
    );
  }

  const executablePath = chromium.executablePath();
  if (!fs.existsSync(executablePath)) {
    throw new Error("Bundled Chromium is missing. Run npm run install:browsers.");
  }

  const browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ["--force-color-profile=srgb"],
  });
  const consoleErrors = [];
  const pageErrors = [];
  try {
    const context = await browser.newContext({
      viewport: { width, height },
      deviceScaleFactor: 1,
      colorScheme: preferredColorScheme,
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    let blockedNavigation = null;
    if (!args["allow-remote"]) {
      await page.route("**/*", async (route) => {
        const request = route.request();
        if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
          try {
            requireLocalUrl(request.url(), false);
          } catch (_) {
            blockedNavigation = request.url();
            await route.abort("blockedbyclient");
            return;
          }
        }
        await route.continue();
      });
    }
    page.on("console", (message) => {
      if (message.type() === "error" && consoleErrors.length < 10) {
        consoleErrors.push(message.text().slice(0, 500));
      }
    });
    page.on("pageerror", (error) => {
      if (pageErrors.length < 10) pageErrors.push(String(error.message || error).slice(0, 500));
    });

    const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    if (blockedNavigation) {
      throw new Error(`Remote navigation is disabled: ${blockedNavigation}`);
    }
    requireLocalUrl(page.url(), Boolean(args["allow-remote"]));
    let locator = null;
    if (args.selector) {
      locator = page.locator(String(args.selector)).first();
      await locator.waitFor({ state: "visible", timeout: 10_000 });
      await locator.scrollIntoViewIfNeeded({ timeout: 10_000 });
    }
    const settled = await settlePage(page, waitMs);
    if (blockedNavigation) {
      throw new Error(`Remote navigation is disabled: ${blockedNavigation}`);
    }
    requireLocalUrl(page.url(), Boolean(args["allow-remote"]));
    let frame;
    let captureRoot = null;
    if (locator) {
      const bounds = await locator.boundingBox();
      if (!bounds) throw new Error("Selected element has no visible bounding box");
      validateCaptureSize(Math.ceil(bounds.width), Math.ceil(bounds.height), "selected element");
      frame = {
        kind: "selector",
        x: bounds.x,
        y: bounds.y,
        width: bounds.width,
        height: bounds.height,
      };
      captureRoot = await locator.elementHandle();
      if (!captureRoot) throw new Error("Selected element detached before evidence capture");
    } else {
      frame = { kind: "viewport", x: 0, y: 0, width, height };
    }

    const evidence = await collectEvidence(
      page,
      frame,
      dynamicSelectors.map((selector) => selector.trim()),
      !args["no-auto-dynamic"],
      captureRoot
    );
    const takeScreenshot = async (destination) => {
      if (locator) {
        await locator.screenshot({ path: destination, animations: "disabled", caret: "hide" });
      } else {
        await page.screenshot({
          path: destination,
          animations: "disabled",
          caret: "hide",
          fullPage: false,
        });
      }
    };
    await takeScreenshot(output);

    const temporalSample = {
      requested: Boolean(sampleOutput),
      captured: false,
      delay_ms: null,
    };
    if (sampleOutput && evidence.dynamic_regions.length > 0) {
      await waitForTemporalSample(page);
      if (locator) {
        const sampleBounds = await locator.boundingBox();
        if (!sampleBounds) throw new Error("Selected element disappeared before temporal sample");
        validateCaptureSize(
          Math.ceil(sampleBounds.width),
          Math.ceil(sampleBounds.height),
          "selected element"
        );
        if (
          Math.ceil(sampleBounds.width) !== Math.ceil(frame.width) ||
          Math.ceil(sampleBounds.height) !== Math.ceil(frame.height)
        ) {
          throw new Error("Selected element size changed before temporal sample");
        }
      }
      fs.mkdirSync(path.dirname(sampleOutput), { recursive: true });
      await takeScreenshot(sampleOutput);
      temporalSample.captured = true;
      temporalSample.delay_ms = 120;
    } else if (sampleOutput) {
      temporalSample.reason = "no_dynamic_regions";
    }

    process.stdout.write(
      JSON.stringify({
        evidence_version: 1,
        url: page.url(),
        status: response ? response.status() : null,
        browser: browser.version(),
        viewport: { width, height, device_scale_factor: 1 },
        color_scheme: preferredColorScheme,
        settled,
        console_errors: consoleErrors,
        page_errors: pageErrors,
        _dom_index: evidence.dom_index,
        _dynamic_regions: evidence.dynamic_regions,
        temporal_sample: temporalSample,
      }) + "\n"
    );
    await context.close();
  } finally {
    await browser.close();
  }
  return 0;
}

main().catch((error) => {
  process.stderr.write(`pixel-twin browser capture: ${error.message || error}\n`);
  process.exitCode = 1;
});
