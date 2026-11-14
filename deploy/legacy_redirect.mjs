// Attach only to the previous custom hostname, after the protected replacement passes.
const previous = 'docs.engineering.onesa.eu';
const destination = 'https://docs.internal.onesa.eu';

export function redirectDocument(request) {
  const input = new URL(request.url);
  const headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'};
  if (input.hostname !== previous) return new Response(null, {status: 404, headers});
  if (!['GET', 'HEAD'].includes(request.method)) {
    return new Response(null, {status: 405, headers: {...headers, Allow: 'GET, HEAD'}});
  }
  // Credentials, OIDC state and legacy sign-in transactions may never move hosts.
  if (/^\/(?:callback|authorize|oauth|sign-in|engineering\/sign-in|cdn-cgi)(?:\/|$)/.test(input.pathname)) {
    return new Response(null, {status: 410, headers});
  }
  const next = new URL(destination);
  next.pathname = input.pathname;
  for (const key of ['view', 'revision', 'page']) {
    const value = input.searchParams.get(key);
    if (value && /^[A-Za-z0-9_.-]{1,96}$/.test(value)) next.searchParams.set(key, value);
  }
  return new Response(null, {status: 308, headers: {...headers, Location: next.href}});
}

export default {fetch: redirectDocument};
