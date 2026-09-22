const MAX_MIME_BYTES = 5 * 1024 * 1024;
const MAX_REQUEST_BYTES = 7 * 1024 * 1024;
const encoder = new TextEncoder();

export function createHandler(EmailMessage) {
  return async (request, env) => {
    if (!env.RELAY_TOKEN) return new Response("Unavailable", { status: 503 });
    if (request.headers.get("Authorization") !== `Bearer ${env.RELAY_TOKEN}`) {
      return new Response("Unauthorized", { status: 401 });
    }
    if (new URL(request.url).pathname !== "/send") return new Response("Not found", { status: 404 });
    if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
    if (Number(request.headers.get("Content-Length")) > MAX_REQUEST_BYTES) {
      return new Response("Too large", { status: 413 });
    }
    let payload;
    try {
      const body = await request.arrayBuffer();
      if (body.byteLength > MAX_REQUEST_BYTES) return new Response("Too large", { status: 413 });
      payload = JSON.parse(new TextDecoder().decode(body));
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }
    if (!payload || payload.from !== "noreply@growc.id" ||
        typeof payload.to !== "string" || !/^[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+$/.test(payload.to) ||
        typeof payload.raw !== "string" || !payload.raw) {
      return new Response("Invalid message", { status: 400 });
    }
    if (encoder.encode(payload.raw).byteLength > MAX_MIME_BYTES) {
      return new Response("Too large", { status: 413 });
    }
    try {
      await env.EMAIL.send(new EmailMessage(payload.from, payload.to, payload.raw));
      return Response.json({ success: true });
    } catch (error) {
      const code = error.code || "E_DELIVERY_FAILED";
      console.error("Email delivery failed", code, error.message);
      return Response.json({ success: false, error: code }, { status: 502 });
    }
  };
}
