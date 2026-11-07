// allauth starts its passkey forms on DOMContentLoaded, which a page htmx swaps in never fires, so they start here
// once their scripts have loaded. A full page load leaves them to allauth.

const UNSTARTED = "script[data-allauth-onload]:not([data-started])";

function startForms() {
  for (const script of document.querySelectorAll(UNSTARTED)) {
    const startForm = script.dataset.allauthOnload.split(".").reduce((found, part) => found?.[part], window);
    if (typeof startForm !== "function") continue;
    script.dataset.started = "";
    startForm(JSON.parse(script.textContent));
  }
}

export function start() {
  for (const script of document.querySelectorAll(UNSTARTED)) script.dataset.started = "";
  document.addEventListener("htmx:after:settle", () => {
    startForms();
    for (const script of document.querySelectorAll("script[src]"))
      script.addEventListener("load", startForms, { once: true });
  });
}
