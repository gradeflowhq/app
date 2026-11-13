// The workspace's switch between its answers and its marking below 768 px. The side shown stays for the tab's next
// pages, until a reload, and each side comes back where it was scrolled to.

const root = document.documentElement;

export const workspaceSwitch = () => ({
  scrolls: {},

  init() {
    this.$el.querySelector(`[value="${this.shown}"]`).checked = true;
  },
  get shown() {
    return root.hasAttribute("data-marking-shown") ? "marking" : "answers";
  },
  remember() {
    this.scrolls[this.shown] = window.scrollY;
  },
  show(event) {
    root.toggleAttribute("data-marking-shown", event.target.value === "marking");
    window.scrollTo(0, this.scrolls[this.shown] ?? 0);
  },
});
