/*
 * Cloudflare Worker — PurpleAir proxy for the Hardrock 100 Conditions Watch dashboard.
 *
 * Use this when the dashboard is HOSTED (e.g. GitHub Pages), where the local
 * serve.py proxy can't run. It holds your PurpleAir key as a secret (never
 * exposed to the browser) and returns CORS-enabled JSON the page can read.
 *
 * ── Deploy (dashboard, ~2 min) ────────────────────────────────────────────
 *   1. dash.cloudflare.com → Workers & Pages → Create application → Start with Hello World! → Deploy.
 *   2. Edit code → paste this whole file → Deploy.
 *   3. Worker → Settings → Variables and Secrets → Add:
 *        Type: Secret,  Name: PURPLEAIR_API_KEY,  Value: <your PurpleAir read key>
 *      (Deploy again if prompted.)
 *   4. Copy the Worker URL, e.g. https://purpleair-proxy.yourname.workers.dev
 *   5. In hardrock100-conditions-watch.html set:
 *        const PURPLEAIR_PROXY = "https://purpleair-proxy.yourname.workers.dev";
 *
 * ── Deploy (Wrangler CLI, alternative) ────────────────────────────────────
 *        npx wrangler deploy purpleair-worker.js
 *        npx wrangler secret put PURPLEAIR_API_KEY
 *
 * ── Request format ────────────────────────────────────────────────────────
 *   GET <worker-url>?lat=37.8&lon=-107.6&distance=50
 *   distance is in miles, default 50. Returns PurpleAir sensor JSON.
 *
 * Security note: set ALLOW_ORIGIN below to your exact site origin
 * (e.g. "https://yourname.github.io") once it works.
 */

const ALLOW_ORIGIN = "*";
const CACHE_TTL = 300; // 5 min — PurpleAir updates sensors every 2 min

// Fields returned per sensor
const FIELDS = "name,latitude,longitude,pm2.5_atm,pm2.5_atm_a,pm2.5_atm_b,humidity,temperature,last_seen";

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
    const lat = parseFloat(url.searchParams.get("lat"));
    const lon = parseFloat(url.searchParams.get("lon"));
    const distance = parseFloat(url.searchParams.get("distance") || "50");

    if (!env.PURPLEAIR_API_KEY) return json({ error: "no_key" }, cors, 200);
    if (isNaN(lat) || isNaN(lon)) return json({ error: "missing lat/lon" }, cors, 400);

    // Convert miles radius → bounding box for PurpleAir API
    const deltaLat = distance / 69;
    const deltaLon = distance / (69 * Math.cos(lat * Math.PI / 180));
    const nwlat = lat + deltaLat;
    const selat = lat - deltaLat;
    const nwlng = lon - deltaLon;
    const selng = lon + deltaLon;

    const cacheKey = new Request(
      "https://purpleair-cache/" + encodeURIComponent(`${lat},${lon},${distance}`),
      { method: "GET" }
    );
    const cache = caches.default;
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const upstream =
      "https://api.purpleair.com/v1/sensors?" +
      new URLSearchParams({
        fields: FIELDS,
        location_type: "0", // outdoor only
        nwlat: nwlat.toFixed(6),
        selat: selat.toFixed(6),
        nwlng: nwlng.toFixed(6),
        selng: selng.toFixed(6),
      }).toString();

    let data;
    try {
      const resp = await fetch(upstream, {
        headers: {
          "X-API-Key": env.PURPLEAIR_API_KEY,
          "User-Agent": "hardrock-conditions/1.0",
        },
      });
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
