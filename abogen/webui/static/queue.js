import { initReaderUI } from "./reader.js";

const queueState = (window.AbogenQueueState = window.AbogenQueueState || {
  boundOverwritePrompt: false,
  boundAfterSwap: false,
});

const applyProgressBars = (root = document) => {
  const scope = root instanceof Element ? root : document;
  const bars = scope.querySelectorAll(".progress-bar[data-progress]");
  bars.forEach((bar) => {
    const raw = Number.parseFloat(bar.getAttribute("data-progress") || "0");
    const bounded = Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : 0;
    bar.style.setProperty("--progress", `${bounded}%`);
  });
};

const handleOverwritePrompt = (event) => {
  const detail = event?.detail || {};
  const title = detail.title || "this item";
  const message = detail.message || `Audiobookshelf already has "${title}". Overwrite?`;
  if (!window.confirm(message)) {
    return;
  }

  const url = detail.url;
  if (!url || typeof htmx === "undefined") {
    return;
  }

  const target = detail.target || "#jobs-panel";
  const values = { overwrite: "true" };
  if (detail.values && typeof detail.values === "object") {
    Object.assign(values, detail.values);
  }

  htmx.ajax("POST", url, {
    target,
    swap: "innerHTML",
    values,
  });
};

const initQueuePage = () => {
  initReaderUI();
  applyProgressBars(document);
  if (!queueState.boundOverwritePrompt) {
    queueState.boundOverwritePrompt = true;
    document.addEventListener("audiobookshelf-overwrite-prompt", handleOverwritePrompt);
  }
  if (!queueState.boundAfterSwap) {
    queueState.boundAfterSwap = true;
    document.addEventListener("htmx:afterSwap", (event) => {
      const target = event?.detail?.target;
      if (target) {
        applyProgressBars(target);
      }
    });
  }
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initQueuePage, { once: true });
} else {
  initQueuePage();
}
