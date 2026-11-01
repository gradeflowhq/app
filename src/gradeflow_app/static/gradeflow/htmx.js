// htmx on every page: the headers its requests carry, what their responses say, and how pages swap in.

import { keepModelState, modelHeaders, modelState } from "./models.js";
import { report } from "./toasts.js";

// The server shows times in this browser's time zone.
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
if (!document.cookie.split("; ").includes(`tz=${zone}`)) {
  document.cookie = `tz=${zone}; path=/; max-age=31536000; samesite=lax`;
}

// Signing in rotates the CSRF token, and boosted pages keep the first page's <body>, so each request reads the
// token from its cookie.
const csrfToken = () => document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ?? "";
// A live refresh (live/topics.py names its events "live-…") leaves the messages to the page the person goes to. A
// change may start marking that asks this device's model service, so it carries the service's endpoint and token;
// the model service's own requests carry all the device knows of it, and their responses what it now knows.
document.addEventListener("htmx:config:request", (event) => {
  const { request, sourceEvent, sourceElement } = event.detail.ctx;
  if (request.method !== "GET") Object.assign(request.headers, { "X-CSRFToken": csrfToken(), ...modelHeaders() });
  if (request.method !== "GET" && sourceElement?.closest("[data-model-service]")) request.body.set("model_state", modelState());
  if (sourceEvent?.type?.startsWith("live-")) request.headers["X-Live-Refresh"] = "true";
});
document.addEventListener("htmx:finally:request", (event) => keepModelState(event.detail.ctx.response));

// Messages from in-page requests arrive in a header (ui/middleware.py). A failed change that shows nothing, such
// as a form's errors (`hx-status:422`), says so plainly.
const messagesOf = (ctx) => JSON.parse(ctx.response?.headers.get("X-Messages") ?? "[]");
document.addEventListener("htmx:finally:request", (event) => {
  const { ctx } = event.detail;
  const messages = messagesOf(ctx);
  for (const { text, level } of messages) report(text, level);
  const unshown = ctx.response?.status >= 400 && ctx.swap === "none";
  if (unshown && ctx.request.method !== "GET" && !messages.length) report("That didn't work. Try again in a moment.");
});
document.addEventListener("htmx:error", (event) => {
  const { ctx, error } = event.detail;
  if (error?.name === "AbortError") return; // a newer request replaced it
  if (ctx?.request.method !== "GET" && !ctx?.response) report("GradeFlow can't be reached. Check your connection.");
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

// A loading bar for pages someone opens that are slow enough to notice, not for live refreshes. It sits outside
// body, which boosted swaps replace.
const PROGRESS_DELAY_MS = 150;
document.documentElement.insertAdjacentHTML("beforeend", '<div id="progress" aria-hidden="true"></div>');
const progress = document.getElementById("progress");
let progressing;
const opened = ({ sourceEvent, request }) => sourceEvent?.isTrusted && request.headers["HX-Request-Type"] === "full";
document.addEventListener("htmx:before:request", (event) => {
  if (event.defaultPrevented || !opened(event.detail.ctx)) return;
  delete progress.dataset.state;
  clearTimeout(progressing);
  progressing = setTimeout(() => (progress.dataset.state = "loading"), PROGRESS_DELAY_MS);
});
document.addEventListener("htmx:finally:request", (event) => {
  if (!opened(event.detail.ctx)) return;
  clearTimeout(progressing);
  if (progress.dataset.state === "loading") progress.dataset.state = "done";
});

// Search forms leave empty fields out of the URL.
document.body.addEventListener("htmx:config:request", (event) => {
  const { body, method } = event.detail.ctx.request;
  if (method !== "GET" || !event.target.matches("form[role=search]")) return;
  for (const [name, value] of [...body]) if (value === "") body.delete(name);
});
