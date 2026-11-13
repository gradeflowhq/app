// The marking column's width: dragged on its resizer, kept in this browser. The stylesheet holds its limits.

const KEY = "marking-width";
const root = document.documentElement;

export const markingResizer = () => ({
  right: 0, // the workspace's right edge while dragging

  start(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    this.$el.setPointerCapture(event.pointerId);
    this.right = this.$el.closest(".workspace-columns").getBoundingClientRect().right;
  },
  drag(event) {
    if (this.$el.hasPointerCapture(event.pointerId))
      root.style.setProperty("--marking-width", `${this.right - event.clientX}px`);
  },
  keep() {
    localStorage.setItem(KEY, this.$el.nextElementSibling.offsetWidth);
  },
  reset() {
    root.style.removeProperty("--marking-width");
    localStorage.removeItem(KEY);
  },
});
