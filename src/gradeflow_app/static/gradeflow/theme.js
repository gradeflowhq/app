// Theme: system, light or dark, kept in this browser.

import { local } from "./storage.js";

export const themeChoice = () => ({
  theme: "system",
  init() {
    this.theme = local.get("theme") ?? "system";
  },
  choose(event) {
    this.theme = event.target.value;
    local.set("theme", this.theme);
    if (this.theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = this.theme;
  },
  isTheme(theme) {
    return theme === this.theme;
  },
});
