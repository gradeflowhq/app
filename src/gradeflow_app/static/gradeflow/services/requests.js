// Requests the page makes itself, outside htmx.

// Signing in rotates the CSRF token, and boosted pages keep the first page's <body>, so each request reads the
// token from its cookie.
export const csrfToken = () => document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] ?? "";

/** Goes to sign in, then back to this page. */
export const redirectToSignIn = () =>
  location.assign(`/accounts/login/?${new URLSearchParams({ next: location.pathname + location.search })}`);

/** Fetches `url`; signed out, goes to sign in and never settles. */
export async function request(url, init) {
  const response = await fetch(url, init);
  if (response.status !== 401) return response;
  redirectToSignIn();
  return new Promise(() => {});
}

/** Sends `values` as a form; rejects on a failure other than one of `allowed` statuses. */
export async function send(url, { method = "POST", values = {}, allowed = [] } = {}) {
  const response = await request(url, {
    method,
    headers: { "X-CSRFToken": csrfToken() },
    body: method === "DELETE" ? null : new URLSearchParams(values),
  });
  if (!response.ok && !allowed.includes(response.status)) throw new Error(response.statusText);
  return response;
}
