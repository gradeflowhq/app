// The settings page's AI service card, and the notice that marking needs one.

import { forgetModelService, readModelService, saveModelService } from "../services/model-service.js";

export const modelServiceCard = () => ({
  provider: "",
  token: "",
  connected: false,
  loaded: false,
  connecting: false,

  // Forgets a service the server refused, or no longer offers.
  async init() {
    let service = await readModelService();
    const offered = [...this.$root.querySelectorAll("#model-provider option")].map((option) => option.value);
    if (service && (this.$root.dataset.refused || !offered.includes(service.provider))) {
      await forgetModelService();
      service = null;
    }
    Object.assign(this, { connected: Boolean(service), provider: service?.provider ?? "", loaded: true });
    const models = this.$root.querySelector("#model-service-models");
    if (models) htmx.trigger(models, "models-read");
  },
  // The form stays until the server has tried the token.
  async connect() {
    this.connecting = true;
    try {
      await saveModelService(this.$refs.provider.value, this.token.trim());
      await htmx.ajax("POST", this.$root.dataset.connect, {
        source: this.$root,
        target: "#model-service",
        swap: "outerHTML",
      });
    } finally {
      this.connecting = false;
    }
  },
  async disconnect() {
    await forgetModelService();
    this.connected = false;
  },
});

export const modelServiceNotice = () => ({
  connected: false,
  loaded: false,
  async init() {
    this.connected = Boolean(await readModelService());
    this.loaded = true;
  },
});
