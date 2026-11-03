// Long content shortened to the height `collapsible` sets, with a button to show it all.

export const collapsible = () => ({
  overflows: false,
  expanded: false,
  init() {
    // Read once: a swap can remove the refs before the observer's last call.
    const { content, inner } = this.$refs;
    this.observer = new ResizeObserver(() => {
      if (!this.expanded) this.overflows = inner.offsetHeight > content.clientHeight;
    });
    this.observer.observe(inner);
  },
  destroy() {
    this.observer.disconnect();
  },
  get label() {
    return this.expanded ? "Show less" : "Show all";
  },
  toggle() {
    this.expanded = !this.expanded;
  },
});
