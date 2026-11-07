// An edit on the method page sends the revision the page shows when the edit goes. htmx reads fields when a request
// is triggered, so an edit queued behind another (`hx-sync: queue`) would carry the revision from before the first
// one answered, and be refused as if another tab sent it.

export function start() {
  document.addEventListener("htmx:before:request", (event) => {
    const { body } = event.detail.ctx.request;
    const revisionField = document.querySelector("#method-state [name=revision]");
    if (revisionField && body?.has?.("revision")) body.set("revision", revisionField.value);
  });
}
