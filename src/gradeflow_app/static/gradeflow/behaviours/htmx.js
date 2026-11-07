// htmx on every page: what its requests carry, and how responses swap in.

import { modelHeaders, readModelService } from "../services/model-service.js";
import { csrfToken } from "../services/requests.js";

const LIVE_REFRESH = "X-Live-Refresh";
const TOPIC_PREFIX = "live-";

export function start() {
  readModelService(); // decrypts it now: modelHeaders reads it synchronously

  // Changes carry the CSRF token, and the AI service in case they start marking. Live refreshes (events named
  // TOPIC_PREFIX… by live/topics.py) say so, and leave the messages to the next page.
  document.addEventListener("htmx:config:request", (event) => {
    const { request, sourceEvent } = event.detail.ctx;
    if (request.method !== "GET") Object.assign(request.headers, { "X-CSRFToken": csrfToken(), ...modelHeaders() });
    if (sourceEvent?.type?.startsWith(TOPIC_PREFIX)) request.headers[LIVE_REFRESH] = "true";
  });
  // Search forms leave empty fields out of the URL.
  document.addEventListener("htmx:config:request", (event) => {
    const { body, method } = event.detail.ctx.request;
    if (method !== "GET" || !event.target.matches("form[role=search]")) return;
    for (const [name, value] of [...body]) if (value === "") body.delete(name);
  });
  // A boosted link's error page replaces the page whole, as it would without htmx.
  document.addEventListener("htmx:before:swap", (event) => {
    const { ctx, tasks } = event.detail;
    if (!ctx.request.headers["HX-Boosted"] || ctx.request.method !== "GET" || ctx.response.status < 400) return;
    for (const task of tasks) if (task.type === "main") task.swapSpec.style = "innerHTML";
  });
  // A live refresh can return after its element was swapped out, and morphing a detached element throws.
  document.addEventListener("htmx:before:swap", (event) => {
    if (event.detail.tasks.some((task) => task.type === "main" && !task.target?.isConnected)) event.preventDefault();
  });
  // htmx 4.0.0's SSE extension leaves the abort of its stream on navigation unhandled. Remove once htmx fixes it.
  window.addEventListener("unhandledrejection", (event) => {
    if (event.reason?.name === "AbortError") event.preventDefault();
  });
  // A morph tells each element it keeps with a "morph" event first, so a component can take down the markup its
  // library added. It leaves `x-show`'s style and `x-cloak` to Alpine.
  htmx.registerExtension("morph-event", {
    htmx_before_morph_node: (_, { oldNode }) => oldNode.dispatchEvent?.(new Event("morph")),
    htmx_before_morph_attr: (element, { attrName }) =>
      !(element.hasAttribute("x-show") && (attrName === "style" || attrName === "x-cloak")),
  });
}
