const params = new URLSearchParams(window.location.search);
const root = document.documentElement;
const opacityInput = document.querySelector("#overlayOpacity");
const modeButtons = Array.from(document.querySelectorAll("[data-mode-button]"));
const sliceLayer = document.querySelector(".slice-layer");
const canvasMeta = document.querySelector("#canvasMeta");
const capture = params.get("capture") === "1";

function setMode(mode) {
  root.dataset.mode = mode;
  modeButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.modeButton === mode);
  });
  const url = new URL(window.location.href);
  url.searchParams.set("mode", mode);
  if (capture) {
    url.searchParams.set("capture", "1");
  }
  window.history.replaceState(null, "", url);
}

function setOverlayOpacity(value) {
  const normalized = Math.max(0, Math.min(100, Number(value))) / 100;
  root.style.setProperty("--overlay-opacity", normalized.toFixed(2));
}

async function loadConfig() {
  const response = await fetch("./lab-config.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load lab-config.json: ${response.status}`);
  }
  const config = await response.json();
  root.style.setProperty("--canvas-width", `${config.width}px`);
  root.style.setProperty("--canvas-height", `${config.height}px`);
  root.style.setProperty("--canvas-bg", config.background || "#e5e5e7");
  if (canvasMeta) {
    canvasMeta.textContent = `${config.width} x ${config.height} reference canvas`;
  }
  if (sliceLayer) {
    sliceLayer.replaceChildren();
    for (const slice of config.slices || []) {
      const img = document.createElement("img");
      img.src = slice.src;
      img.alt = "";
      img.style.left = `${slice.x}px`;
      img.style.top = `${slice.y}px`;
      img.style.width = `${slice.width}px`;
      img.style.height = `${slice.height}px`;
      sliceLayer.appendChild(img);
    }
  }
}

if (capture) {
  root.dataset.capture = "1";
}

modeButtons.forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.modeButton));
});

if (opacityInput) {
  setOverlayOpacity(opacityInput.value);
  opacityInput.addEventListener("input", (event) => setOverlayOpacity(event.target.value));
}

function showConfigError(message) {
  const banner = document.createElement("div");
  banner.className = "config-error";
  banner.textContent = `Lab config failed to load — stage size falls back to defaults and pixel diff will report size-mismatch. ${message}`;
  document.body.prepend(banner);
}

loadConfig()
  .then(() => setMode(params.get("mode") || "rebuilt"))
  .catch((error) => {
    console.error(error);
    showConfigError(error.message);
    setMode(params.get("mode") || "rebuilt");
  });
