import json, os, time, urllib.request

key = os.environ["PURPLEAIR_API_KEY"]
headers = {"X-API-Key": key, "User-Agent": "hardrock-conditions/1.0"}

def get(url):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# Current sensor readings
# If PURPLEAIR_SENSOR_ID is set (comma-separated), fetch only those sensors
sensor_ids_env = os.environ.get("PURPLEAIR_SENSOR_ID")
bbox_env = os.environ.get("PURPLEAIR_BBOX")
if sensor_ids_env:
    ids = [s.strip() for s in sensor_ids_env.split(",") if s.strip()]
    sensors = {"fields": ["sensor_index", "name", "latitude", "longitude", "pm2.5_atm", "pm2.5_atm_a", "pm2.5_atm_b", "humidity", "temperature", "last_seen"], "data": []}
    for sid in ids:
        try:
            s = get(
                f"https://api.purpleair.com/v1/sensors/{sid}?fields=name,latitude,longitude,pm2.5_atm,pm2.5_atm_a,pm2.5_atm_b,humidity,temperature,last_seen"
            )
            sensors["data"].append([
                s.get("sensor_index") or int(sid),
                s.get("name") or f"sensor-{sid}",
                s.get("latitude"),
                s.get("longitude"),
                s.get("pm2.5_atm"),
                s.get("pm2.5_atm_a"),
                s.get("pm2.5_atm_b"),
                s.get("humidity"),
                s.get("temperature"),
                s.get("last_seen")
            ])
        except Exception:
            sensors["data"].append([int(sid), f"sensor-{sid}", None, None, None, None, None, None, None, None])
else:
    # bounding box covers entire course corridor by default, but can be overridden
    # by setting PURPLEAIR_BBOX as "nwlat,selat,nwlng,selng" in the environment.
    if bbox_env:
        try:
            parts = [p.strip() for p in bbox_env.split(",")]
            nwlat, selat, nwlng, selng = parts
        except Exception:
            nwlat, selat, nwlng, selng = "38.15", "37.75", "-108.05", "-107.20"
    else:
        nwlat, selat, nwlng, selng = "38.15", "37.75", "-108.05", "-107.20"

    url = (
        f"https://api.purpleair.com/v1/sensors"
        f"?fields=name,latitude,longitude,pm2.5_atm,pm2.5_atm_a,pm2.5_atm_b,humidity,temperature,last_seen"
        f"&location_type=0&nwlat={nwlat}&selat={selat}&nwlng={nwlng}&selng={selng}"
    )
    sensors = get(url)

# 72h history per sensor at 30-min averages
now = int(time.time())
start = now - 72 * 3600
name_idx = sensors["fields"].index("name")

history = []
for row in sensors["data"]:
    sensor_index = row[0]
    name = row[name_idx]
    try:
        hist = get(
            f"https://api.purpleair.com/v1/sensors/{sensor_index}/history"
            f"?start_timestamp={start}&end_timestamp={now}&average=30"
            f"&fields=pm2.5_atm_a,pm2.5_atm_b"
        )
        history.append({
            "sensor_index": sensor_index,
            "name": name,
            "fields": hist.get("fields", []),
            "data": hist.get("data", [])
        })
        print(f"  {name}: {len(hist.get('data', []))} points")
    except Exception as e:
        print(f"  {name}: history failed ({e})")
        history.append({"sensor_index": sensor_index, "name": name, "fields": [], "data": []})

sensors["history"] = history
os.makedirs("data", exist_ok=True)
with open("data/purpleair.json", "w") as f:
    json.dump(sensors, f)

total = sum(len(h["data"]) for h in history)
print(f"Done: {len(sensors['data'])} sensors, {total} history points")
