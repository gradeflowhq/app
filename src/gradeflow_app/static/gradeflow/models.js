// The model service this browser brings for marking that uses AI: which of the server's AI services, its token, the
// chosen models, answers per request and how each model did when tried, kept on this device only. Every change
// carries all but the trials in headers, since a change may start marking that asks a language model; the model
// service's own requests carry the rest.

const MODELS_STORAGE_KEY = "gradeflow:models";
const STATE_HEADER = "X-Model-ModelServiceState";

function storedModelService() {
  try {
    const stored = JSON.parse(localStorage.getItem(MODELS_STORAGE_KEY));
    return stored?.provider && stored?.token ? stored : null;
  } catch {
    return null; // a service that can't be read is none
  }
}

const store = (state) => localStorage.setItem(MODELS_STORAGE_KEY, JSON.stringify(state));

const clear = () => localStorage.removeItem(MODELS_STORAGE_KEY);

export function modelHeaders() {
  const stored = storedModelService();
  if (!stored) return {};
  const { provider, fast_model = "", careful_model = "", answers_per_request = 12 } = stored;
  const state = { provider, fast_model, careful_model, answers_per_request };
  return { "X-Model-Token": stored.token, [STATE_HEADER]: JSON.stringify(state) };
}

// All the device knows of its model service but the token, for the model service's own requests.
export function modelState() {
  const { token, ...state } = storedModelService() ?? {};
  return JSON.stringify(state);
}

// The model service's responses say what the device now knows: the models chosen, and how each did when tried.
// One for a service the device has since left is dropped.
export function storeModelState(response) {
  const stateHeader = response?.headers.get(STATE_HEADER);
  const stored = storedModelService();
  if (!stateHeader || !stored) return;
  const state = JSON.parse(stateHeader);
  if (state.provider === stored.provider) store({ ...state, token: stored.token });
}

// The settings card and the answers column's notice: whether this device is connected, connecting it, and
// disconnecting it, which forgets it all here. The card comes back from the server with its models, or why it
// couldn't connect.
export const modelService = () => ({
  provider: "",
  token: "",
  connected: false,
  connecting: false,
  // The settings card forgets a service the server refused, or no longer offers.
  init() {
    if (this.$root.id === "model-service") {
      const offered = [...this.$root.querySelectorAll("#model-provider option")].map((option) => option.value);
      if (this.$root.dataset.refused || !offered.includes(storedModelService()?.provider)) clear();
    }
    const stored = storedModelService();
    this.connected = Boolean(stored);
    this.provider = stored?.provider ?? "";
  },
  // Connecting the same service again, with a new token, keeps its models. The form stays until the server has
  // tried the token.
  async connect() {
    const provider = this.$refs.provider.value;
    const stored = storedModelService();
    const token = this.token.trim();
    store(stored?.provider === provider ? { ...stored, token } : { provider, token });
    this.connecting = true;
    await htmx.ajax("POST", this.$root.dataset.connect, { source: this.$root, target: "#model-service", swap: "outerHTML" });
    this.connecting = false;
  },
  disconnect() {
    clear();
    this.connected = false;
  },
});
