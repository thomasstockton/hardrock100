/*
 * Cloudflare Worker — EPA AirNow proxy for the Hardrock 100 Conditions Watch dashboard.
 *
 * Use this when the dashboard is HOSTED (e.g. GitHub Pages), where the local
 * serve.py proxy can't run. It holds your AirNow key as a secret (never exposed
 * to the browser) and returns CORS-enabled JSON that the page can read.
 *
 * ── Deploy (dashboard, ~2 min) ────────────────────────────────────────────
 *   1. dash.cloudflare.com → Workers & Pages → Create application → Worker → Deploy.
 *   2. Edit code → paste this whole file → Deploy.
 *   3. Worker → Settings → Variables and Secrets → Add:
 *        Type: Secret,  Name: AIRNOW_API_KEY,  Value: <your AirNow key>
 *      (Deploy again if prompted.)
 *   4. Copy the Worker URL, e.g. https://airnow-proxy.yourname.workers.dev
 *   5. In hardrock100-conditions-watch.html set:
 *        const AIRNOW_PROXY = "https://airnow-proxy.yourname.workers.dev";
 *
 * ── Deploy (Wrangler CLI, alternative) ────────────────────────────────────
 *        npx wrangler deploy airnow-worker.js
 *        npx wrangler secret put AIRNOW_API_KEY
 *
 * Security note: set ALLOW_ORIGIN below to your exact site origin
 * (e.g. "https://yourname.github.io") once it works, so only your dashboard
 * can use the proxy. "*" is fine to start.
 */

const ALLOW_ORIGIN = "*";
const CACHE_TTL = 600; // seconds — matches AirNow's hourly cadence, respects rate limits

export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": ALLOW_ORIGIN,
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "*",
      "Vary": "Origin",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }

    const url = new URL(request.url);
    const lat = url.searchParams.get("lat");
    const lon = url.searchParams.get("lon");
    const distance = url.searchParams.get("distance") || "75";

    if (!env.AIRNOW_API_KEY) return json({ error: "no_key" }, cors, 200);
    if (!lat || !lon) return json({ error: "missing lat/lon" }, cors, 400);

    // Edge cache to stay well under AirNow's rate limit.
    const cacheKey = new Request(
      "https://airnow-cache/" + encodeURIComponent(`${lat},${lon},${distance}`),
      { method: "GET" }
    );
    const cache = caches.default;
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const upstream =
      "https://www.airnowapi.org/aq/observation/latLong/current/?" +
      new URLSearchParams({
        format: "application/json",
        latitude: lat,
        longitude: lon,
        distance,
        API_KEY: env.AIRNOW_API_KEY,
      }).toString();

    let data;
    try {
      const resp = await fetch(upstream, { headers: { "User-Agent": "hardrock-conditions/1.0" } });
      data = await resp.json();
    } catch (e) {
      return json({ error: String(e) }, cors, 502);
    }

    const out = new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": `max-age=${CACHE_TTL}`, ...cors },
    });
    try { await cache.put(cacheKey, out.clone()); } catch (e) { /* ignore cache errors */ }
    return out;
  },
};

function json(obj, cors, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: { "Content-Type": "application/json", ...cors },
  });
}
