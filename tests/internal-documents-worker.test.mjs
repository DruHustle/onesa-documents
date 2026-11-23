import assert from "node:assert/strict";
import test from "node:test";
import worker from "../deploy/cloudflare/internal-documents.mjs";

const root = "https://docs.internal.onesa.eu";

test("alternate origins and plaintext never reach the private service", async () => {
  let calls = 0;
  const service = { fetch() { ++calls; } };
  for (const url of ["https://preview.workers.dev/", "https://docs.onesa.eu/", "http://docs.internal.onesa.eu/"]) {
    const result = await worker.fetch(new Request(url), { DOCUMENTS_SERVICE: service });
    assert.equal(result.status, 404);
    assert.equal(result.headers.get("Cache-Control"), "private, no-store");
  }
  assert.equal(calls, 0);
});

test("streams POST content, keeps CSRF origin and only forwards Docs cookies", async () => {
  const body = "title=new&text=approved";
  const request = new Request(root + "/docs/operations?draft=1", {
    method: "POST", body,
    headers: {
      "content-type": "application/x-www-form-urlencoded", origin: root, authorization: "Bearer fixture",
      cookie: "unrelated=x; __Host-docs-session=abc; __Host-docs-challenge=def",
      host: "wrong.example", forwarded: "host=wrong.example", "x-forwarded-proto": "http",
    },
  });
  let received;
  const response = await worker.fetch(request, { DOCUMENTS_SERVICE: {
    async fetch(url, init) {
      received = new Request(url, init);
      const headers = new Headers([["Set-Cookie", "__Host-docs-session=abc; Secure; Path=/"],
        ["Set-Cookie", "__Host-docs-challenge=; Max-Age=0; Secure; Path=/"]]);
      headers.set("Location", "/docs/operations");
      return new Response(null, { status: 303, headers });
    },
  }});
  assert.equal(received.url, "http://docs.internal.onesa.eu/docs/operations?draft=1");
  assert.equal(received.method, "POST");
  assert.equal(await received.text(), body);
  assert.equal(received.redirect, "manual");
  assert.equal(received.headers.get("origin"), root);
  assert.equal(received.headers.get("authorization"), "Bearer fixture");
  assert.equal(received.headers.get("cookie"), "__Host-docs-session=abc; __Host-docs-challenge=def");
  assert.equal(received.headers.get("x-forwarded-proto"), "https");
  assert.equal(received.headers.has("forwarded"), false);
  assert.equal(received.headers.has("host"), false);
  assert.equal(response.status, 303);
  assert.equal(response.headers.get("location"), root + "/docs/operations");
  assert.deepEqual(response.headers.getSetCookie(), [
    "__Host-docs-session=abc; Secure; Path=/", "__Host-docs-challenge=; Max-Age=0; Secure; Path=/",
  ]);
});

test("returns identity redirects to the browser and upgrades same-host origin redirects", async () => {
  const start = "https://auth.onesa.eu/authorize?state=example";
  for (const [location, expected] of [[start, start], ["http://docs.internal.onesa.eu/docs/", root + "/docs/"]]) {
    const response = await worker.fetch(new Request(root + "/sign-in"), {
      DOCUMENTS_SERVICE: { fetch: () => new Response(null, { status: 303, headers: { Location: location } }) },
    });
    assert.equal(response.status, 303);
    assert.equal(response.headers.get("location"), expected);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
  }
});

test("passes the Onesa registration handoff and its one-use bookmark cookie", async () => {
  const registration = "https://account.onesa.eu/register?audience=user&product=internal-documents&platform=web";
  const cookie = "__Host-docs-registration=fixture";
  const service = {
    fetch(url, init) {
      if (new URL(url).pathname === "/register") {
        return new Response(null, { status: 303, headers: {
          Location: registration, "Set-Cookie": cookie + "; Path=/; Secure; HttpOnly; SameSite=Lax",
        } });
      }
      assert.equal(init.headers.get("cookie"), cookie);
      return Response.redirect(root + "/sign-in?next=%2Fdocs%2Fmy-bookmark", 303);
    },
  };
  const start = await worker.fetch(new Request(root + "/register?next=%2Fdocs%2Fmy-bookmark"),
    { DOCUMENTS_SERVICE: service });
  assert.equal(start.status, 303);
  assert.equal(start.headers.get("location"), registration);
  assert.match(start.headers.get("set-cookie"), /^__Host-docs-registration=fixture;/);

  const resumed = await worker.fetch(new Request(root + "/registration/return", {
    headers: { Cookie: "unrelated=value; " + cookie },
  }), { DOCUMENTS_SERVICE: service });
  assert.equal(resumed.status, 303);
  assert.equal(resumed.headers.get("location"), root + "/sign-in?next=%2Fdocs%2Fmy-bookmark");
});

test("blocks Account handoffs outside the Docs registration route and product", async () => {
  for (const [path, target] of [
    ["/callback", "register?audience=user&product=internal-documents&platform=web"],
    ["/register", "admin"],
    ["/register", "register?audience=user&product=portal&platform=web"],
    ["/register", "register?audience=user&product=internal-documents&platform=web&next=https://example.org"],
  ]) {
    const response = await worker.fetch(new Request(root + path), {
      DOCUMENTS_SERVICE: { fetch: () => Response.redirect("https://account.onesa.eu/" + target, 303) },
    });
    assert.equal(response.status, 503);
  }
});

test("denials and binary attachment bodies retain status, headers and bytes", async () => {
  const bytes = Uint8Array.of(0, 128, 255);
  for (const status of [200, 401, 403, 404]) {
    const response = await worker.fetch(new Request(root + "/api/attachments/example"), {
      DOCUMENTS_SERVICE: { fetch: () => new Response(bytes, {
        status, headers: { "Content-Type": "application/octet-stream", "Cache-Control": "public" },
      }) },
    });
    assert.equal(response.status, status);
    assert.equal(response.headers.get("content-type"), "application/octet-stream");
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
  }
});

test("missing, unavailable, failed or unexpectedly redirecting service fails closed", async () => {
  const request = new Request(root + "/callback?code=private");
  for (const env of [
    {}, { DOCUMENTS_SERVICE: { fetch() { throw Error("private details"); } } },
    { DOCUMENTS_SERVICE: { fetch: () => new Response("private details", { status: 502 }) } },
    { DOCUMENTS_SERVICE: { fetch: () => Response.redirect("https://example.org/elsewhere") } },
  ]) {
    const response = await worker.fetch(request, env);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(await response.text(), "Internal Documents is unavailable.\n");
  }
});
