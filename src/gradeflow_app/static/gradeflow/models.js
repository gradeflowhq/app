// The user's AI service and token, kept in a secret (secrets.js). Every non-GET request sends them in headers, as any
// change may start marking that calls a model.

import { deleteSecret, readSecret, saveSecret } from "./secrets.js";

const SECRET = "ai-service";
const SERVICE_HEADER = "X-Model-Service";
const TOKEN_HEADER = "X-Model-Token";

let service = null; // { provider, token }, once read
const read = () => readSecret(SECRET).then((stored) => (service = stored?.provider && stored?.token ? stored : null));
const reading = read();

/** Empty until the secret is decrypted; `readModelHeaders` waits for it. */
export function modelHeaders() {
  return service ? { [SERVICE_HEADER]: service.provider, [TOKEN_HEADER]: service.token } : {};
}

export const readModelHeaders = () => reading.then(modelHeaders);

// The settings card and the answers column's notice.
export const modelService = () => ({
  provider: "",
  token: "",
  connected: false,
  loaded: false,
  connecting: false,
  // The settings card forgets a service the server refused, or no longer offers.
  async init() {
    await reading;
    if (this.$root.id === "model-service" && service) {
      const offered = [...this.$root.querySelectorAll("#model-provider option")].map((option) => option.value);
      if (this.$root.dataset.refused || !offered.includes(service.provider)) await this.forget();
    }
    Object.assign(this, { connected: Boolean(service), provider: service?.provider ?? "", loaded: true });
    const models = this.$root.querySelector("#model-service-models");
    if (models) htmx.trigger(models, "models-read");
  },
  // The form stays until the server has tried the token.
  async connect() {
    this.connecting = true;
    try {
      const connecting = { provider: this.$refs.provider.value, token: this.token.trim() };
      await saveSecret(SECRET, connecting);
      service = connecting;
      await htmx.ajax("POST", this.$root.dataset.connect, { source: this.$root, target: "#model-service", swap: "outerHTML" });
    } finally {
      this.connecting = false;
    }
  },
  async forget() {
    await deleteSecret(SECRET);
    service = null;
  },
  async disconnect() {
    await this.forget();
    this.connected = false;
  },
});
