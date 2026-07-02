#!/usr/bin/env python3
"""
Local server + EPA AirNow proxy for the Hardrock 100 Conditions Watch dashboard.

Why this exists:
  The AirNow API (airnowapi.org) requires an API key and does NOT send CORS
  headers, so a browser cannot call it directly. This script serves the
  dashboard over http://localhost and exposes a same-origin /airnow endpoint
  that forwards requests to AirNow with your key attached server-side (so the
  key never appears in the page) and adds the CORS header the browser needs.

Usage:
  1. Put this file in the same folder as hardrock100-conditions-watch.html
  2. Set your key and run:

       macOS / Linux:
         export AIRNOW_API_KEY="your-key-here"
         python3 serve.py

       Windows (PowerShell):
         $env:AIRNOW_API_KEY="your-key-here"
         python serve.py

  3. Open the printed URL, e.g. http://localhost:8000/hardrock100-conditions-watch.html

Notes:
  - Responses are cached ~10 min per location to stay well under AirNow's
    default 500 requests/hour limit.
  - No third-party packages required (standard library only).
"""
import os
import json
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ.get("AIRNOW_API_KEY", "").strip()
PORT = int(os.environ.get("PORT", "8000"))
CACHE = {}            # key -> (timestamp, bytes)
CACHE_TTL = 600       # seconds


class Handler(SimpleHTTPRequestHandler):
    def _send_json(self, status, body_bytes):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/airnow":
            return self._handle_airnow(parsed)
        return super().do_GET()

    def _handle_airnow(self, parsed):
        if not API_KEY:
            return self._send_json(200, b'{"error":"no_key"}')

        q = urllib.parse.parse_qs(parsed.query)
        lat = (q.get("lat", ["0"])[0])
        lon = (q.get("lon", ["0"])[0])
        dist = (q.get("distance", ["75"])[0])
        ckey = f"{lat},{lon},{dist}"
        now = time.time()

        if ckey in CACHE and (now - CACHE[ckey][0]) < CACHE_TTL:
            return self._send_json(200, CACHE[ckey][1])

        upstream = "https://www.airnowapi.org/aq/observation/latLong/current/?" + urllib.parse.urlencode({
            "format": "application/json",
            "latitude": lat,
            "longitude": lon,
            "distance": dist,
            "API_KEY": API_KEY,
        })
        try:
            req = urllib.request.Request(upstream, headers={"User-Agent": "hardrock-conditions/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read()
            # AirNow returns [] when no monitor is within range; still cache it.
            CACHE[ckey] = (now, body)
            return self._send_json(200, body)
        except Exception as e:
            return self._send_json(502, json.dumps({"error": str(e)}).encode())

    def log_message(self, fmt, *args):
        # Quieter logging; comment out to see every request.
        if "/airnow" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    state = "SET" if API_KEY else "NOT SET  (measured AirNow data will be unavailable)"
    print("=" * 64)
    print("  Hardrock 100 Conditions Watch — local server + AirNow proxy")
    print("=" * 64)
    print(f"  AIRNOW_API_KEY: {state}")
    print(f"  Serving:        http://localhost:{PORT}/hardrock100-conditions-watch.html")
    print(f"  AirNow proxy:   http://localhost:{PORT}/airnow?lat=37.81&lon=-107.66")
    print("  Press Ctrl+C to stop.")
    print("=" * 64)
    try:
        ThreadingHTTPServer(("", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
