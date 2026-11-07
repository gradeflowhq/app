// While a page opens, a loading bar runs, its link becomes current in its nav, and the region it replaces is marked
// busy, which styles.css dims after a moment. Live refreshes don't count. The bar sits outside body, which boosted
// swaps replace.

const isPageOpen = ({ sourceEvent, request }) =>
  sourceEvent?.isTrusted && request.headers["HX-Request-Type"] === "full";
const replacedRegion = ({ target }) => (target === document.body ? document.getElementById("main") : target);

// A page's request → the region it replaces, its link, and the link that was current before.
const openingPages = new WeakMap();
let latestPageRequest; // whose link goes back to the one current before if its page never arrives

export function start() {
  document.documentElement.insertAdjacentHTML("beforeend", '<div id="page-loading-bar" aria-hidden="true"></div>');
  const loadingBar = document.getElementById("page-loading-bar");

  document.addEventListener("htmx:before:request", (event) => {
    const { ctx } = event.detail;
    if (event.defaultPrevented || !isPageOpen(ctx)) return;
    delete loadingBar.dataset.state;
    loadingBar.offsetWidth; // restarts it from empty, even mid-fade
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
}
