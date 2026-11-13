// The marking workspace's question list, collapsed to its keys or not, kept in this browser.

export const questionsCollapse = () => ({
  collapsed: false,
  init() {
    this.collapsed = document.documentElement.hasAttribute("data-questions-collapsed");
  },
  toggle() {
    this.collapsed = !this.collapsed;
    document.documentElement.toggleAttribute("data-questions-collapsed", this.collapsed);
    if (this.collapsed) localStorage.setItem("questions-collapsed", "1");
    else localStorage.removeItem("questions-collapsed");
  },
});
