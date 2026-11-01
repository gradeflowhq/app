// A form marked `data-decrypt` downloads its file with every student ID envelope replaced by its plaintext.

import { decryptText, ENVELOPES } from "./crypto.js";
import { passphraseFor } from "./passphrase.js";
import { report } from "./toasts.js";

document.addEventListener("submit", async (event) => {
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
    const envelope = text.match(ENVELOPES)?.[0];
    if (envelope) {
      const passphrase = await passphraseFor(form.dataset.decrypt, envelope);
      if (!passphrase) return report("Not downloaded: the student IDs need the passphrase.");
      text = await decryptText(text, passphrase);
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
    report("The download failed. Try again.");
  } finally {
    button?.classList.remove("btn-disabled");
  }
});

function showErrors(form, page) {
  const answered = new DOMParser().parseFromString(page, "text/html").querySelector("form[data-decrypt]");
  if (!answered) throw new Error("No form in the answer");
  form.replaceWith(answered);
  htmx.process(answered);
}
