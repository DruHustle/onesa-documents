const origin = "https://docs.internal.onesa.eu";
const cachePolicy = "private, no-store";

function unavailable() {
  return new Response("Internal Documents is unavailable.\n", {
    status: 503,
    headers: { "Cache-Control": cachePolicy, "Content-Type": "text/plain; charset=utf-8" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.origin !== origin) {
      return new Response(null, { status: 404, headers: { "Cache-Control": cachePolicy } });
    }
    if (!env.DOCUMENTS_SERVICE) return unavailable();

    const headers = new Headers();
    for (const name of ["Accept", "Accept-Language", "Authorization", "Content-Type", "Origin", "Range"]) {
      if (request.headers.has(name)) headers.set(name, request.headers.get(name));
    }
    const cookies = (request.headers.get("Cookie") || "").split(";").map(value => value.trim())
      .filter(value => /^__Host-docs-(session|challenge)=/.test(value));
    if (cookies.length) headers.set("Cookie", cookies.join("; "));
    headers.set("X-Forwarded-Proto", "https");

    // The service binding fixes the destination and port. The URL supplies the
    // canonical Host header and selects HTTP on the connector's loopback hop.
    url.protocol = "http:";
    try {
      const upstream = await env.DOCUMENTS_SERVICE.fetch(url.href, {
        method: request.method, headers, body: request.body, duplex: "half", redirect: "manual",
      });
      if (upstream.status >= 500) return unavailable();
      // Copy the response's header list as-is, including each separate Set-Cookie.
      const response = new Response(upstream.body, upstream);
      response.headers.set("Cache-Control", cachePolicy);
      const location = response.headers.get("Location");
      if (location && response.status >= 300 && response.status < 400) {
        const destination = new URL(location, request.url);
        if (destination.origin === url.origin) destination.protocol = "https:";
        if (request.method === "POST" && url.pathname === "/logout" &&
            destination.origin === "https://auth.onesa.eu" && destination.pathname === "/oidc/logout" &&
            !destination.username && !destination.password && !destination.hash &&
            destination.searchParams.size === 2 && destination.searchParams.get("client_id") &&
            destination.searchParams.get("post_logout_redirect_uri") === origin + "/signed-out") {
          response.headers.set("Location", destination.href);
          return response;
        }
        const login = destination.origin === "https://auth.onesa.eu" && destination.pathname === "/authorize";
        if (destination.username || destination.password || (destination.origin !== origin && !login)) {
          return unavailable();
        }
        response.headers.set("Location", destination.href);
      }
      return response;
    } catch {
      return unavailable();
    }
  },
};
