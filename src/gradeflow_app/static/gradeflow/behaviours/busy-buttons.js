// A pressed button is busy until its request ends and ignores presses meanwhile, which htmx would queue; a pressed
// method choice blocks the other choices in its form too. The spinner (styles.css) shows after SPINNER_DELAY_MS and
// stays at least SPINNER_SHOWN_MS.

const SPINNER_DELAY_MS = 300;
const SPINNER_SHOWN_MS = 200;
const BUSY = '.btn[aria-busy="true"], form:has(.method-choice[aria-busy="true"]) .method-choice';

const busyButtons = new WeakMap(); // a busy button → the function that ends it
const requestEnds = new WeakMap(); // a request → the function that ends its button's busy state

/** Marks `button` busy, if it isn't already; the function returned ends it. */
export function markBusy(button) {
  if (!busyButtons.has(button)) {
    button.setAttribute("aria-busy", "true");
    let shownAt; // undefined until the spinner shows
    const timer = setTimeout(() => {
      button.dataset.spinner = "";
      shownAt = performance.now();
    }, SPINNER_DELAY_MS);
    busyButtons.set(button, () => {
      busyButtons.delete(button);
      clearTimeout(timer);
      const end = () => {
        button.removeAttribute("aria-busy");
        delete button.dataset.spinner;
      };
      const remainingMs = shownAt + SPINNER_SHOWN_MS - performance.now(); // NaN if it never showed
      if (remainingMs > 0) setTimeout(end, remainingMs);
      else end();
    });
  }
  return busyButtons.get(button);
}

/** The button a click or form submission pressed. */
const pressedButton = ({ sourceEvent, sourceElement }) => {
  const button = sourceEvent?.submitter ?? sourceElement;
  return ["click", "submit"].includes(sourceEvent?.type) && button.matches("button:is(.btn, .method-choice)")
    ? button
    : null;
};

export function start() {
  document.addEventListener("htmx:before:request", (event) => {
    const button = event.defaultPrevented ? null : pressedButton(event.detail.ctx);
    if (button) requestEnds.set(event.detail.ctx, markBusy(button));
  });
  document.addEventListener("htmx:finally:request", (event) => requestEnds.get(event.detail.ctx)?.());
  addEventListener(
    "click",
    (event) => {
      if (!event.target.closest?.(BUSY)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
    },
    true,
  );
}
