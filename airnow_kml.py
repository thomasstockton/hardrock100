#!/usr/bin/env python3
"""
airnow_kml.py — Pull recent AirNow AQI data for a bounding box and save it as
KML, ready to import into CalTopo. Two modes:

  points   (default) — monitor-site placemarks via the data endpoint
  contours            — interpolated AQI bands via the contour-KML endpoint

Usage:
    export AIRNOW_API_KEY="2EFC3DBA-F8F6-41FE-8E95-9E9121199A2B"
    python3 airnow_kml.py                  # monitor points
    python3 airnow_kml.py --contours       # filled AQI bands (Combined)
    python3 airnow_kml.py --contours --type PM25
    python3 airnow_kml.py --bbox -109,37,-106,39

It figures out the current UTC hour for you, and if that hour has no data yet
(AirNow updates in the back half of each hour, contours lag further) it walks
backward up to MAX_LOOKBACK_HOURS until it finds features.

Output (per mode):
    ./airnow_out/airnow_points_<UTChour>.kml   /  airnow_points_latest.kml
    ./airnow_out/airnow_contours_<UTChour>.kml /  airnow_contours_latest.kml

No third-party dependencies — standard library only.
"""

import html
import os
import re
import sys
import time
import argparse
import datetime as dt
import urllib.parse
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Config — edit these if you want
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("AIRNOW_API_KEY", "")  # or paste your key here as a string

# minLon, minLat, maxLon, maxLat — this box wraps the clockwise Hardrock loop
BBOX = "-108.0,37.7,-107.25,38.1"

# --- points mode (monitor sites) -------------------------------------------
PARAMETERS = "PM25,OZONE"   # PM25 = smoke; also PM10, CO, NO2, SO2, OZONE
DATA_TYPE = "A"             # A = AQI, C = concentration, B = both
MONITOR_TYPE = "2"          # 0 = permanent, 1 = mobile, 2 = both
VERBOSE = "1"               # 1 = include site name/agency in output

# --- contours mode (interpolated bands) ------------------------------------
CONTOUR_TYPE = "Combined"   # Combined | PM25 | Ozone  (override with --type)

# AirNow's contour-KML endpoint. This is their documented structure, but the
# exact parameter spelling lives behind their login-gated docs. IF A CONTOUR
# CALL ERRORS: log in at airnowapi.org -> Web Services -> a contour service ->
# Query Tool, build it once, click "Generated URL", and paste it below with the
# date replaced by {DATE} and the key by {KEY}. Everything else keeps working.
CONTOUR_URL_TEMPLATE = (
    "https://www.airnowapi.org/aq/kml/{TYPE}/"
    "?DATE={DATE}"
    "&BBOX={BBOX}"
    "&SRS=EPSG:4326"
    "&format=application/vnd.google-earth.kml"
    "&API_KEY={KEY}"
)

OUTPUT_DIR = "airnow_out"
MAX_LOOKBACK_HOURS = 6      # contours can lag a few hours; give them room

DATA_ENDPOINT = "https://www.airnowapi.org/aq/data/"

# ---------------------------------------------------------------------------
# KML post-processing — makes AirNow output CalTopo-compatible
# ---------------------------------------------------------------------------

# AQI category → KML color (AABBGGRR format, alpha-blue-green-red)
_AQI_COLORS = {
    "Category0": "ff888888",  # no data — gray
    "Category1": "ff00e400",  # Good — green
    "Category2": "ff00ffff",  # Moderate — yellow
    "Category3": "ff007eff",  # Unhealthy for Sensitive Groups — orange
    "Category4": "ff0000ff",  # Unhealthy — red
    "Category5": "ff973f8f",  # Very Unhealthy — purple
    "Category6": "ff23007e",  # Hazardous — maroon
}


def _parse_airnow_html(encoded: str) -> dict:
    """Extract AQI fields from AirNow's HTML-encoded description blob."""
    s = html.unescape(encoded)

    h3s = re.findall(r'<h3[^>]*>\s*([^<]+?)\s*</h3>', s)
    site  = h3s[0].strip() if h3s else "Monitor"
    param = h3s[1].strip() if len(h3s) > 1 else ""

    # Agency: first <p> between the two <h3> tags
    if len(h3s) >= 2:
        i1 = s.find(f'>{h3s[0]}<') + len(h3s[0]) + 1
        i2 = s.find(f'>{h3s[1]}<')
        seg = s[i1:i2] if i2 > i1 else s[i1:]
    else:
        seg = s
    am = re.search(r'<p[^>]*>\s*([^<]+?)\s*</p>', seg)
    agency = am.group(1).strip() if am else ""

    aqi_m = re.search(r'<p\s+class="bold"\s*>(\d+)</p>', s)
    aqi   = aqi_m.group(1) if aqi_m else "?"

    cat_m = re.search(r'background-color:[^;]+;[^>]*>\s*<p[^>]*>\s*([^<]+?)\s*</p>', s)
    category = cat_m.group(1).strip() if cat_m else ""

    ts_m = re.search(r'class="footnote[^"]*"[^>]*>\s*<p[^>]*>\s*([^<]+?)\s*</p>', s)
    timestamp = ts_m.group(1).strip() if ts_m else ""

    return dict(site=site, agency=agency, param=param, aqi=aqi,
                category=category, timestamp=timestamp)


def postprocess_kml(body: str) -> str:
    """Rewrite AirNow's points KML to be CalTopo-compatible:
    - Replace remote icon URLs with inline colored styles.
    - Add a <name> to each Placemark.
    - Replace HTML description blobs with plain text.
    """
    # Replace <Style id="CategoryN">…</Style> blocks with inline color styles.
    def _repl_style(m: re.Match) -> str:
        cid   = m.group(1)
        color = _AQI_COLORS.get(cid, "ff888888")
        return (
            f'<Style id="{cid}">'
            f'<IconStyle><color>{color}</color><scale>1.2</scale></IconStyle>'
            f'<LabelStyle><color>{color}</color><scale>0.8</scale></LabelStyle>'
            f'</Style>'
        )
    body = re.sub(r'<Style id="(Category\d+)">.*?</Style>', _repl_style, body, flags=re.DOTALL)

    # For each Placemark: add <name> and replace HTML description with plain text.
    def _repl_placemark(m: re.Match) -> str:
        block = m.group(0)
        dm = re.search(r'<description>(.*?)</description>', block, re.DOTALL)
        if not dm:
            return block

        info = _parse_airnow_html(dm.group(1))
        cat_str = f" ({info['category']})" if info['category'] else ""
        name = (f"{info['site']} — {info['param']} AQI {info['aqi']}{cat_str}"
                if info['param'] else info['site'])

        lines = [info['agency']] if info['agency'] else []
        if info['param']:
            lines.append(f"{info['param']}: AQI {info['aqi']}{cat_str}")
        if info['timestamp']:
            lines.append(info['timestamp'])

        block = re.sub(
            r'<description>.*?</description>',
            f'<description>{html.escape(chr(10).join(lines))}</description>',
            block, flags=re.DOTALL,
        )
        if '<name>' not in block:
            block = block.replace('<Placemark>',
                                  f'<Placemark>\n                <name>{html.escape(name)}</name>', 1)
        return block

    body = re.sub(r'<Placemark>.*?</Placemark>', _repl_placemark, body, flags=re.DOTALL)
    return body


def build_points_url(hour_utc: dt.datetime) -> str:
    """Data endpoint URL for one UTC hour, KML output."""
    stamp = hour_utc.strftime("%Y-%m-%dT%H")
    params = {
        "startDate": stamp,
        "endDate": stamp,
        "parameters": PARAMETERS,
        "BBOX": BBOX,
        "dataType": DATA_TYPE,
        "format": "application/vnd.google-earth.kml",
        "verbose": VERBOSE,
        "monitorType": MONITOR_TYPE,
        "API_KEY": API_KEY,
    }
    return DATA_ENDPOINT + "?" + urllib.parse.urlencode(params)


def build_contour_url(hour_utc: dt.datetime) -> str:
    """Contour-KML endpoint URL for one UTC hour."""
    return CONTOUR_URL_TEMPLATE.format(
        TYPE=CONTOUR_TYPE,
        DATE=hour_utc.strftime("%Y-%m-%dT%H"),
        BBOX=BBOX,
        KEY=API_KEY,
    )


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "airnow-kml/1.1"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def feature_count(body: str) -> int:
    """Points show up as <Placemark>; contour bands as <Polygon>. Count either."""
    return body.count("<Placemark") + body.count("<Polygon")


def _contour_cache_ok(latest_path: str, max_age_minutes: int = 55) -> bool:
    """Return True if the latest contour KML is fresh enough to reuse."""
    try:
        age = dt.datetime.now(dt.timezone.utc).timestamp() - os.path.getmtime(latest_path)
        return age < max_age_minutes * 60
    except OSError:
        return False


def run(mode: str) -> int:
    if not API_KEY:
        sys.exit("ERROR: set AIRNOW_API_KEY (env var) or paste your key into the script.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    build = build_contour_url if mode == "contours" else build_points_url
    label = f"contours ({CONTOUR_TYPE})" if mode == "contours" else "points"

    # Reuse a cached contour file rather than burning API quota on every run.
    if mode == "contours":
        latest_path = os.path.join(OUTPUT_DIR, f"airnow_{mode}_latest.kml")
        if _contour_cache_ok(latest_path):
            age_min = int((dt.datetime.now(dt.timezone.utc).timestamp()
                           - os.path.getmtime(latest_path)) / 60)
            print(f"Using cached contour KML ({age_min}m old) — AirNow rate-limits this endpoint.")
            print(f"  -> {latest_path}")
            print(f"\nImport airnow_{mode}_latest.kml into CalTopo: Add -> Import "
                  "(or drag onto the map).")
            return 0

    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)

    for back in range(MAX_LOOKBACK_HOURS + 1):
        hour = now - dt.timedelta(hours=back)
        url = build(hour)
        try:
            body = fetch(url)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            # 400 = "no data for this hour" — contours return this for hours
            # not yet published; walk back and try an earlier hour.
            if e.code == 400 and "could not be found" in detail:
                print(f"No {label} data for UTC hour {hour:%Y-%m-%dT%H}, trying an earlier hour...")
                if mode == "contours":
                    time.sleep(2)
                continue
            if e.code == 429:
                sys.exit(
                    "ERROR: AirNow rate limit exceeded.\n"
                    "The contour endpoint has a strict hourly quota. "
                    "Wait an hour before trying again."
                )
            hint = ""
            if mode == "contours":
                hint = ("\nHINT: the contour endpoint params may differ for your account — "
                        "grab the 'Generated URL' from the Query Tool and update "
                        "CONTOUR_URL_TEMPLATE (use {DATE} and {KEY}).")
            sys.exit(f"ERROR: AirNow returned HTTP {e.code} for {mode}.{hint}\n{detail[:500]}")
        except urllib.error.URLError as e:
            sys.exit(f"ERROR: could not reach AirNow ({e.reason}).")

        if feature_count(body) > 0:
            if mode == "points":
                body = postprocess_kml(body)
            stamp = hour.strftime("%Y-%m-%dT%HZ")
            snapshot = os.path.join(OUTPUT_DIR, f"airnow_{mode}_{stamp}.kml")
            latest = os.path.join(OUTPUT_DIR, f"airnow_{mode}_latest.kml")
            for path in (snapshot, latest):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
            print(f"Found {feature_count(body)} {label} feature(s) for UTC hour {stamp}")
            print(f"  -> {snapshot}")
            print(f"  -> {latest}")
            print(f"\nImport airnow_{mode}_latest.kml into CalTopo: Add -> Import "
                  "(or drag onto the map).")
            return 0

        print(f"No {label} data for UTC hour {hour:%Y-%m-%dT%H}, trying an earlier hour...")

    extra = ("" if mode == "contours" else
             " The box may have no active monitors right now — try --contours, "
             "or widen --bbox.")
    sys.exit(f"No {label} features found in the last {MAX_LOOKBACK_HOURS} hours "
             f"for BBOX {BBOX}.{extra}")


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch AirNow AQI KML for CalTopo.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--points", action="store_const", dest="mode", const="points",
                   help="monitor-site placemarks (default)")
    g.add_argument("--contours", action="store_const", dest="mode", const="contours",
                   help="interpolated AQI bands")
    p.add_argument("--type", choices=["Combined", "PM25", "Ozone"],
                   help="contour pollutant type (contours mode)")
    p.add_argument("--bbox", help="override BBOX: minLon,minLat,maxLon,maxLat")
    p.set_defaults(mode="points")
    args = p.parse_args()

    global CONTOUR_TYPE, BBOX
    if args.type:
        CONTOUR_TYPE = args.type
    if args.bbox:
        BBOX = args.bbox

    return run(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
