// A form marked `data-decrypt` downloads its file with every encrypted student ID replaced by its plaintext.

import { decryptText, ENCRYPTED_IDS } from "../lib/assessment-crypto.js";
import { assessmentKeysFor, missingCourseKeyMessage } from "../services/course-keys.js";
import { showToast } from "../services/toasts.js";

async function download(event) {
  const form = event.target.closest("form[data-decrypt]");
  if (!form) return;
  event.preventDefault();
  const button = event.submitter;
  button?.classList.add("btn-disabled");
  try {
    const response = await fetch(form.action, { method: "POST", body: new FormData(form, button) });
    if (response.status === 422) return showErrors(form, await response.text());
    if (!response.ok) throw new Error(response.statusText);
    // Kept whole: text() drops a byte order mark, which spreadsheets need to read the file as UTF-8.
    let text = new TextDecoder("utf-8", { ignoreBOM: true }).decode(await response.arrayBuffer());
    if (text.search(ENCRYPTED_IDS) >= 0) {
      const assessmentElement = form.closest("[data-assessment]");
      const keys = await assessmentKeysFor(assessmentElement).catch(() => null);
      if (!keys) return showToast(missingCourseKeyMessage(assessmentElement));
      text = await decryptText(text, keys);
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const encoded = /filename\*=utf-8''([^;]+)/i.exec(disposition)?.[1];
    const name = encoded ? decodeURIComponent(encoded) : (/filename="([^"]+)"/.exec(disposition)?.[1] ?? "results");
    const link = Object.assign(document.createElement("a"), {
      href: URL.createObjectURL(new Blob([text], { type: response.headers.get("Content-Type") ?? "text/plain" })),
      download: name,
    });
    link.click();
    URL.revokeObjectURL(link.href);
    form.closest("dialog")?.close();
  } catch {
    showToast("The download failed. Try again.");
  } finally {
    button?.classList.remove("btn-disabled");
  }
}

/** Replaces `form` with the one in `html`, which shows its errors. */
function showErrors(form, html) {
  const returnedForm = new DOMParser().parseFromString(html, "text/html").querySelector("form[data-decrypt]");
  if (!returnedForm) throw new Error("No form in the answer");
  form.replaceWith(returnedForm);
  htmx.process(returnedForm);
}

export function start() {
  document.addEventListener("submit", download);
}
