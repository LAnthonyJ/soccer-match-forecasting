export async function onRequest(context) {
  const apiOrigin = (context.env.API_ORIGIN || "").trim().replace(/\/+$/, "");
  if (!apiOrigin) {
    return Response.json(
      {
        ok: false,
        error: {
          code: "proxy_not_configured",
          message: "API_ORIGIN is not configured",
          details: {},
        },
        meta: {
          request_id: context.request.headers.get("cf-ray") || "pages-proxy",
        },
      },
      { status: 500 },
    );
  }

  const requestUrl = new URL(context.request.url);
  const targetUrl = new URL(`${apiOrigin}${requestUrl.pathname}${requestUrl.search}`);
  const headers = new Headers(context.request.headers);
  headers.set("host", targetUrl.host);
  headers.set("x-forwarded-host", requestUrl.host);
  headers.set("x-forwarded-proto", requestUrl.protocol.replace(":", ""));
  headers.delete("content-length");

  const init = {
    method: context.request.method,
    headers,
    redirect: "manual",
  };

  if (context.request.method !== "GET" && context.request.method !== "HEAD") {
    init.body = await context.request.arrayBuffer();
  }

  return fetch(targetUrl.toString(), init);
}
