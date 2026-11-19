// The marking column's width: dragged on its resizer or moved with its arrow keys, kept in this browser. The stylesheet
// holds its limits, 20rem and half the workspace, which `aria-valuemin` and `aria-valuemax` repeat.

const KEY = "marking-width";
const KEY_STEP = 16;
const root = document.documentElement;

export const markingResizer = () => ({
  startX: 0,
  startWidth: 0,
  width: 0,
  minWidth: 0,
  maxWidth: 0,

  init() {
    this.measure();
  },
  get marking() {
    return this.$el.nextElementSibling;
  },
  measure() {
    const columns = this.$el.closest(".workspace-columns");
    const { paddingLeft, paddingRight } = getComputedStyle(columns);
    this.minWidth = 20 * Number.parseFloat(getComputedStyle(root).fontSize);
    this.maxWidth = (columns.clientWidth - Number.parseFloat(paddingLeft) - Number.parseFloat(paddingRight)) / 2;
    this.width = this.marking.offsetWidth;
  },
  resize(width) {
    root.style.setProperty("--marking-width", `${width}px`);
  },
  start(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    this.$el.setPointerCapture(event.pointerId);
    this.startX = event.clientX;
    this.startWidth = this.marking.getBoundingClientRect().width;
  },
  drag(event) {
    if (this.$el.hasPointerCapture(event.pointerId)) this.resize(this.startWidth + this.startX - event.clientX);
  },
  widen() {
    this.resize(this.marking.offsetWidth + KEY_STEP);
    this.keep();
  },
  narrow() {
    this.resize(this.marking.offsetWidth - KEY_STEP);
    this.keep();
  },
  keep() {
    this.measure();
    this.resize(this.width);
    localStorage.setItem(KEY, this.width);
  },
  reset() {
    root.style.removeProperty("--marking-width");
    localStorage.removeItem(KEY);
    this.measure();
  },
});
