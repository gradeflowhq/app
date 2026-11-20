// The landing page's script, run before main.js: its demos' components.

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";
import { landingDemo, methodDemos } from "./components/landing/demo.js";

document.addEventListener("alpine:init", () => {
  Alpine.data("landingDemo", landingDemo);
  Alpine.data("methodDemos", methodDemos);
});
