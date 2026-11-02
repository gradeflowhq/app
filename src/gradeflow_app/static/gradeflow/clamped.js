// Long content cut to the height `clamped` sets, with a button to show it all.

export const clamped = () => ({
  long: false,
  open: false,
  init() {
    // Read once: a swap can remove the refs before the observer's last call.
    const { content, inner } = this.$refs;
    this.observer = new ResizeObserver(() => {
      if (!this.open) this.long = inner.offsetHeight > content.clientHeight;
    });
    this.observer.observe(inner);
  },
  destroy() {
    this.observer.disconnect();
  },
  get label() {
    return this.open ? "Show less" : "Show all";
  },
  toggle() {
    this.open = !this.open;
  },
});
