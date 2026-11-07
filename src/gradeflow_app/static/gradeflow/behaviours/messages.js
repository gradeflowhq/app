// The server's messages as toasts: with a page (#messages), or in the X-Messages header of an in-page response
// (ui/middleware.py). Requests that fail without saying why get one too.

import { readJson } from "../services/page-data.js";
import { clearToasts, showToast } from "../services/toasts.js";

const MESSAGES_HEADER = "X-Messages";

function showPageMessages() {
  for (const { text, level } of readJson("messages", [])) showToast(text, level);
  document.getElementById("messages")?.remove();
}

export function start() {
  showPageMessages();
  document.addEventListener("htmx:after:settle", showPageMessages);
  // Toasts belong to the page that raised them.
  for (const name of ["htmx:after:history:push", "htmx:before:history:restore"])
    document.addEventListener(name, clearToasts);

  document.addEventListener("htmx:finally:request", (event) => {
    const { ctx } = event.detail;
    const messages = JSON.parse(ctx.response?.headers.get(MESSAGES_HEADER) ?? "[]");
    for (const { text, level } of messages) showToast(text, level);
    // A failed change that swaps nothing, such as a form's errors with `hx-status:422`.
    const failedQuietly = ctx.response?.status >= 400 && ctx.swap === "none" && ctx.request.method !== "GET";
    if (failedQuietly && !messages.length) showToast("That didn't work. Try again in a moment.");
  });
  document.addEventListener("htmx:error", (event) => {
    const { ctx, error } = event.detail;
    if (error?.name === "AbortError") return; // a newer request replaced it
    if (ctx?.request.method !== "GET" && !ctx?.response)
      showToast("GradeFlow can't be reached. Check your connection.");
  });
}
