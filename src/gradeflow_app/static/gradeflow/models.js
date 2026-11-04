// The AI service this browser brings for marking that uses AI: which of the server's services, and its token, kept on
// this device only. Every change carries both in headers, since a change may start marking that asks a model.

const MODELS_STORAGE_KEY = "gradeflow:models";
const SERVICE_HEADER = "X-Model-Service";
const TOKEN_HEADER = "X-Model-Token";

function storedModelService() {
  try {
    const { provider, token } = JSON.parse(localStorage.getItem(MODELS_STORAGE_KEY)) ?? {};
    return provider && token ? { provider, token } : null;
  } catch {
    return null; // a service that can't be read is none
  }
}

const store = (service) => localStorage.setItem(MODELS_STORAGE_KEY, JSON.stringify(service));

const clear = () => localStorage.removeItem(MODELS_STORAGE_KEY);

export function modelHeaders() {
  const stored = storedModelService();
  return stored ? { [SERVICE_HEADER]: stored.provider, [TOKEN_HEADER]: stored.token } : {};
}

// The settings card and the answers column's notice: whether this device is connected, connecting it, and
// disconnecting it, which forgets the token here. The card comes back from the server with its models, or why it
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
    if (stored) store(stored); // drops what earlier versions kept here
    this.connected = Boolean(stored);
    this.provider = stored?.provider ?? "";
  },
  // The form stays until the server has tried the token.
  async connect() {
    store({ provider: this.$refs.provider.value, token: this.token.trim() });
    this.connecting = true;
    await htmx.ajax("POST", this.$root.dataset.connect, { source: this.$root, target: "#model-service", swap: "outerHTML" });
    this.connecting = false;
  },
  disconnect() {
    clear();
    this.connected = false;
  },
});
