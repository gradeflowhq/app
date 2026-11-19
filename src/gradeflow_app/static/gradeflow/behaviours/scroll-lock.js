// While a modal dialog is open, the page under it doesn't scroll (styles.css). A page too short to scroll stays as it
// is: locking it would add a scrollbar gutter. Dialogs don't bubble `toggle`, and a swap can remove an open dialog
// without closing it.

export function start() {
  const root = document.documentElement;
  const updateScrollLock = () =>
    root.toggleAttribute(
      "data-scroll-locked",
      document.querySelector("dialog:modal") !== null && root.scrollHeight > root.clientHeight,
    );
  document.addEventListener(
    "toggle",
    (event) => {
      if (event.target.localName === "dialog") updateScrollLock();
    },
    true,
  );
  document.addEventListener("htmx:after:swap", updateScrollLock);
}
