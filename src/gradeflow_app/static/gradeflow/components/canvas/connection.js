// The Canvas connection, with the address and token kept in a secret (secrets.js), and the Integrations page's
// Canvas card.

import { canvasClient } from "../../lib/canvas-client.js";
import { deleteSecret, readSecret, saveSecret } from "../../services/secrets.js";
import { showToast } from "../../services/toasts.js";

const SECRET = "canvas";
const TOKEN_REFUSED = "Canvas didn't accept the access token.";

const errorMessage = (error, host) => {
  if (error.status === 401) return TOKEN_REFUSED;
  if (error.proxy)
    return `GradeFlow can't reach ${host} through its Canvas proxy. Check the Canvas address, or disconnect and connect again.`;
  if (error instanceof TypeError) return "Canvas can't be reached. Check your connection and try again.";
  if (error.name === "TimeoutError") return "Canvas didn't answer in time. Try again.";
  return error.message || "Canvas can't be reached.";
};

/** A component from mixins, their getters kept. */
export const mergeMixins = (...mixins) =>
  Object.defineProperties({}, Object.assign({}, ...mixins.map(Object.getOwnPropertyDescriptors)));

/** A component's `data-config`. */
export const readConfig = (element) => JSON.parse(element.dataset.config);

/** Connecting to Canvas, mixed into every Canvas component. Each component's `load` fetches what it shows, which
 * also tests a new connection. */
export const connectionMixin = () => ({
  config: {},
  canvasConnection: null,
  loaded: false,
  checking: false,
  baseUrl: "",
  token: "",
  busyLabel: "",
  error: "",

  get client() {
    return canvasClient({ ...this.canvasConnection, proxyUrl: this.config.proxy });
  },
  get isBusy() {
    return Boolean(this.busyLabel);
  },
  /** Once Canvas has accepted the token, not while it's being checked. */
  get connected() {
    return Boolean(this.canvasConnection) && !this.checking;
  },
  get canvasHost() {
    return this.canvasConnection ? new URL(this.canvasConnection.baseUrl).host : "";
  },

  async loadConnection() {
    this.canvasConnection = await readSecret(SECRET);
    this.loaded = true;
  },
  async connect() {
    this.error = "";
    const baseUrl = URL.parse(this.baseUrl.trim())?.origin;
    if (!baseUrl) {
      this.error = "Enter your Canvas address.";
      return;
    }
    if (!this.token.trim()) {
      this.error = "Paste an access token from Canvas.";
      return;
    }
    Object.assign(this, { canvasConnection: { baseUrl, token: this.token.trim() }, checking: true });
    const accepted = await this.load();
    this.checking = false;
    if (accepted) {
      await saveSecret(SECRET, this.canvasConnection);
      this.token = "";
      showToast(`Connected to ${this.canvasHost}.`, "success");
    } else {
      this.canvasConnection = null;
      if (this.error !== TOKEN_REFUSED) this.error = `Couldn't connect to Canvas at ${baseUrl}. Check the address.`;
    }
  },
  async disconnect() {
    await deleteSecret(SECRET);
    this.canvasConnection = null;
  },
  /** Runs `work` busy with `label`; null, showing why, if it fails. A refused token disconnects. */
  async run(label, work) {
    this.busyLabel = label;
    this.error = "";
    try {
      return await work();
    } catch (error) {
      if (error.status === 401 && this.canvasConnection && !this.checking) this.disconnect();
      this.error = errorMessage(error, this.canvasHost);
      return null;
    } finally {
      this.busyLabel = "";
    }
  },
});

export const canvasConnection = () =>
  mergeMixins(connectionMixin(), {
    init() {
      this.config = readConfig(this.$el);
      return this.loadConnection();
    },
    load() {
      return this.run("Connecting…", async () => {
        await this.client.courses();
        return true;
      });
    },
  });
