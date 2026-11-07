// The person's AI service and token, kept in a secret (secrets.js). Requests that change something carry them in
// headers: any change can start marking that calls a model.

import { deleteSecret, readSecret, saveSecret } from "./secrets.js";

const SECRET = "ai-service";
const SERVICE_HEADER = "X-Model-Service";
const TOKEN_HEADER = "X-Model-Token";

let service = null; // { provider, token }
let reading = null;

/** The service, once its secret is decrypted; null without one. */
export function readModelService() {
  reading ??= readSecret(SECRET).then((stored) => {
    service = stored?.provider && stored?.token ? stored : null;
  });
  return reading.then(() => service);
}

/** Empty until `readModelService` has resolved. */
export const modelHeaders = () =>
  service ? { [SERVICE_HEADER]: service.provider, [TOKEN_HEADER]: service.token } : {};

export const readModelHeaders = () => readModelService().then(modelHeaders);

export async function saveModelService(provider, token) {
  const saved = { provider, token };
  await saveSecret(SECRET, saved);
  service = saved;
}

export async function forgetModelService() {
  await deleteSecret(SECRET);
  service = null;
}
