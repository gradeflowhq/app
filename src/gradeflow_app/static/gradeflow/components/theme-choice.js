// Theme: system, light or dark, kept in this browser.

export const themeChoice = () => ({
  theme: "system",
  init() {
    this.theme = localStorage.getItem("theme") ?? "system";
  },
  choose(event) {
    this.theme = event.target.value;
    localStorage.setItem("theme", this.theme);
    if (this.theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = this.theme;
  },
});
