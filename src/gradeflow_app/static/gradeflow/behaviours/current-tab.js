// The current tab scrolled into view, on a tab row too narrow for every tab.

// A swap can settle a text node, which has no querySelector.
const showCurrentTab = (root) =>
  root.querySelector?.("#tabs [aria-current=page]")?.scrollIntoView({ block: "nearest", inline: "center" });

export function start() {
  showCurrentTab(document);
  document.addEventListener("htmx:after:settle", (event) => showCurrentTab(event.target));
}
