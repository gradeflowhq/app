// The model service this browser brings for marking that uses AI: which of the server's AI services, its token, the
// chosen models, answers per request and how each model did when tried, kept on this device only. Every change
// carries all but the trials in headers, since a change may start marking that asks a language model; the model
// service's own requests carry the rest.

import { local } from "./storage.js";

const KEY = "gradeflow:models";
const STATE_HEADER = "X-Model-State";

function connection() {
  const found = local.json(KEY);
  return found?.provider && found?.token ? found : null;
}

const keep = (found) => local.set(KEY, JSON.stringify(found));

const forget = () => local.remove(KEY);

export function modelHeaders() {
  const found = connection();
  if (!found) return {};
  const { provider, decision_model = "", reasoning_model = "", answers_per_request = 12 } = found;
  const state = { provider, decision_model, reasoning_model, answers_per_request };
  return { "X-Model-Token": found.token, [STATE_HEADER]: JSON.stringify(state) };
}

// All the device knows of its model service but the token, for the model service's own requests.
export function modelState() {
  const { token, ...state } = connection() ?? {};
  return JSON.stringify(state);
}

// The model service's responses say what the device now knows: the models chosen, and how each did when tried.
// One for a service the device has since left is dropped.
export function keepModelState(response) {
  const sent = response?.headers.get(STATE_HEADER);
  const found = connection();
  if (!sent || !found) return;
  const state = JSON.parse(sent);
  if (state.provider === found.provider) keep({ ...state, token: found.token });
}

// The settings card and the answers column's notice: whether this device is connected, connecting it, and
// disconnecting it, which forgets it all here. The card comes back from the server with its models, or why it
// couldn't connect.
export const modelService = () => ({
  provider: "",
  token: "",
  connected: false,
  // The settings card forgets a service the server refused, or no longer offers.
  init() {
    if (this.$root.id === "model-service") {
      const offered = [...this.$root.querySelectorAll("#model-provider option")].map((option) => option.value);
      if (this.$root.dataset.refused || !offered.includes(connection()?.provider)) forget();
    }
    const found = connection();
    this.connected = Boolean(found);
    this.provider = found?.provider ?? "";
  },
  get disconnected() {
    return !this.connected;
  },
  // Connecting the same service again, with a new token, keeps its models.
  connect() {
    const provider = this.$refs.provider.value;
    const found = connection();
    const token = this.token.trim();
    keep(found?.provider === provider ? { ...found, token } : { provider, token });
    this.provider = provider;
    this.connected = true;
    this.token = "";
    this.send(this.$root.dataset.connect);
  },
  disconnect() {
    forget();
    this.connected = false;
  },
  // The card comes back in place of this one. Whether this component or a new one holds it, a refused connection is
  // forgotten and its form shown again with the reason.
  async send(url) {
    await htmx.ajax("POST", url, { source: this.$root, target: "#model-service", swap: "outerHTML" });
    if (document.getElementById("model-service")?.dataset.refused) {
      forget();
      this.connected = false;
    }
  },
});
