// htmx on every page: the headers its requests carry, what their responses say, and how pages swap in.

import { storeModelState, modelHeaders, modelState } from "./models.js";
import { showToast } from "./toasts.js";

const LIVE_REFRESH = "X-Live-Refresh";
const MESSAGES_HEADER = "X-Messages";
const TOPIC_PREFIX = "live-";

// The server shows times in this browser's time timeZone.
const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
if (!document.cookie.split("; ").includes(`tz=${timeZone}`)) {
  document.cookie = `tz=${timeZone}; path=/; max-age=31536000; samesite=lax`;
}

// Signing in rotates the CSRF token, and boosted pages keep the first page's <body>, so each request reads the
// token from its cookie.
const csrfToken = () => document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ?? "";
// A live refresh (live/topics.py starts its event names with TOPIC_PREFIX) leaves the messages to the page the person
// goes to. A change may start marking that asks this device's model service, so it carries the service's endpoint and
// token; the model service's own requests carry all the device knows of it, and their responses what it now knows.
document.addEventListener("htmx:config:request", (event) => {
  const { request, sourceEvent, sourceElement } = event.detail.ctx;
  if (request.method !== "GET") Object.assign(request.headers, { "X-CSRFToken": csrfToken(), ...modelHeaders() });
  if (request.method !== "GET" && sourceElement?.closest("[data-model-service]")) request.body.set("model_state", modelState());
  if (sourceEvent?.type?.startsWith(TOPIC_PREFIX)) request.headers[LIVE_REFRESH] = "true";
});
document.addEventListener("htmx:finally:request", (event) => storeModelState(event.detail.ctx.response));

// Messages from in-page requests arrive in a header (ui/middleware.py). A failed change that shows nothing, such
// as a form's errors (`hx-status:422`), says so plainly.
const messagesOf = (ctx) => JSON.parse(ctx.response?.headers.get(MESSAGES_HEADER) ?? "[]");
document.addEventListener("htmx:finally:request", (event) => {
  const { ctx } = event.detail;
  const messages = messagesOf(ctx);
  for (const { text, level } of messages) showToast(text, level);
  const nothingSwapped = ctx.response?.status >= 400 && ctx.swap === "none";
  if (nothingSwapped && ctx.request.method !== "GET" && !messages.length) showToast("That didn't work. Try again in a moment.");
});
document.addEventListener("htmx:error", (event) => {
  const { ctx, error } = event.detail;
  if (error?.name === "AbortError") return; // a newer request replaced it
  if (ctx?.request.method !== "GET" && !ctx?.response) showToast("GradeFlow can't be reached. Check your connection.");
});
// htmx 4.0.0's SSE extension aborts its stream on navigation without handling the rejection. Remove once htmx
// fixes it.
window.addEventListener("unhandledrejection", (event) => {
  if (event.reason?.name === "AbortError") event.preventDefault();
});
// Error pages from boosted links show as the browser would show them without htmx.
document.addEventListener("htmx:before:swap", (event) => {
  const { ctx, tasks } = event.detail;
  if (!ctx.request.headers["HX-Boosted"] || ctx.request.method !== "GET" || ctx.response.status < 400) return;
  for (const task of tasks) if (task.type === "main") task.swapSpec.style = "innerHTML";
});
// A live refresh can return after another swap removed its element; morphing a detached element throws.
document.addEventListener("htmx:before:swap", (event) => {
  if (event.detail.tasks.some((task) => task.type === "main" && !task.target?.isConnected)) event.preventDefault();
});

// htmx runs its morph hooks only for extensions. Elements a morph keeps get a "morph" event first, so a component
// can take down markup its library added, which the server doesn't send. What an `x-show` shows stays Alpine's: a
// morph brings back neither the server's style nor its `x-cloak`.
htmx.registerExtension("morph-event", {
  htmx_before_morph_node: (_, { oldNode }) => oldNode.dispatchEvent?.(new Event("morph")),
  htmx_before_morph_attr: (element, { attrName }) =>
    !(element.hasAttribute("x-show") && (attrName === "style" || attrName === "x-cloak")),
});

// A page someone opens answers the click before it arrives, as an app's would: a loading bar starts, the link
// becomes current in its nav, and what the page replaces is busy, which styles.css dims if it takes a moment. Live
// refreshes don't count. The bar sits outside body, which boosted swaps replace.
document.documentElement.insertAdjacentHTML("beforeend", '<div id="page-loading-bar" aria-hidden="true"></div>');
const loadingBar = document.getElementById("page-loading-bar");
const isPageOpen = ({ sourceEvent, request }) => sourceEvent?.isTrusted && request.headers["HX-Request-Type"] === "full";
const replacedRegion = ({ target }) => (target === document.body ? document.getElementById("main") : target);
// Kept from the start, since a swap can change the request's elements.
const openingPages = new WeakMap(); // a page's request → what it replaces, and the link it made current
let latestPageRequest; // the latest page's request, whose link goes back to not current if the page never arrives
document.addEventListener("htmx:before:request", (event) => {
  const { ctx } = event.detail;
  if (event.defaultPrevented || !isPageOpen(ctx)) return;
  delete loadingBar.dataset.state;
  loadingBar.offsetWidth; // restart from empty, even mid-fade
  loadingBar.dataset.state = "loading";
  const link = ctx.sourceElement;
  const current = link.closest("nav")?.querySelector('[aria-current="page"]');
  if (current && current !== link) {
    current.removeAttribute("aria-current");
    link.setAttribute("aria-current", "page");
  }
  const region = replacedRegion(ctx);
  region?.setAttribute("aria-busy", "true");
  openingPages.set(ctx, { region, link, current });
  latestPageRequest = ctx;
});
document.addEventListener("htmx:finally:request", (event) => {
  const { ctx } = event.detail;
  const page = openingPages.get(ctx);
  if (!page) return;
  if (loadingBar.dataset.state === "loading") loadingBar.dataset.state = "done";
  page.region?.removeAttribute("aria-busy");
  if (ctx !== latestPageRequest || ctx.response || !page.current?.isConnected) return;
  page.link.removeAttribute("aria-current");
  page.current.setAttribute("aria-current", "page");
});

// A button or method choice someone presses, or confirms, is busy until its request ends, and ignores presses
// meanwhile, as do the other choices in its form: htmx would queue them. Pressing Enter in a form clicks its button
// too. A wait long enough to notice shows a spinner (styles.css), which then stays long enough to see rather than flash.
const SPINNER_DELAY_MS = 300;
const SPINNER_SHOWN_MS = 200;
const spinners = new WeakMap(); // a pressed button's request → the button, its spinner's timer, and when it showed
const pressedButton = ({ sourceEvent, sourceElement }) => {
  const button = sourceEvent?.submitter ?? sourceElement;
  return ["click", "submit"].includes(sourceEvent?.type) && button.matches("button:is(.btn, .method-choice)") ? button : null;
};
document.addEventListener("htmx:before:request", (event) => {
  const { ctx } = event.detail;
  const button = event.defaultPrevented ? null : pressedButton(ctx);
  if (!button) return;
  button.setAttribute("aria-busy", "true");
  const spinner = { button };
  spinner.timer = setTimeout(() => {
    button.dataset.spinner = "";
    spinner.shown = performance.now();
  }, SPINNER_DELAY_MS);
  spinners.set(ctx, spinner);
});
document.addEventListener("htmx:finally:request", (event) => {
  const spinner = spinners.get(event.detail.ctx);
  if (!spinner) return;
  clearTimeout(spinner.timer);
  const stop = () => {
    spinner.button.removeAttribute("aria-busy");
    delete spinner.button.dataset.spinner;
  };
  const remainingMs = spinner.shown + SPINNER_SHOWN_MS - performance.now(); // NaN if it never showed
  if (remainingMs > 0) setTimeout(stop, remainingMs);
  else stop();
});
addEventListener(
  "click",
  (event) => {
    if (!event.target.closest?.('.btn[aria-busy="true"], form:has(.method-choice[aria-busy="true"]) .method-choice')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  },
  true,
);

// Search forms leave empty fields out of the URL.
document.body.addEventListener("htmx:config:request", (event) => {
  const { body, method } = event.detail.ctx.request;
  if (method !== "GET" || !event.target.matches("form[role=search]")) return;
  for (const [name, value] of [...body]) if (value === "") body.delete(name);
});
