// The landing page's script, run before main.js: its demos' components.

import Alpine from "../vendor/alpinejs-csp-3.17.4/module.esm.min.js";
import { landingDemo, methodDemos } from "./components/landing/demo.js";

// The demos' made-up student IDs aren't encrypted: shown as they are, before main.js would try to decrypt them.
for (const id of document.querySelectorAll("[data-student-id]")) {
  id.textContent = id.dataset.studentId;
  id.dataset.studentIdState = "shown";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("landingDemo", landingDemo);
  Alpine.data("methodDemos", methodDemos);
});
