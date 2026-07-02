import json, os, time, urllib.request

key = os.environ["PURPLEAIR_API_KEY"]
headers = {"X-API-Key": key, "User-Agent": "hardrock-conditions/1.0"}

def get(url):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# Current sensor readings (bounding box covers entire course corridor)
sensors = get(
    "https://api.purpleair.com/v1/sensors"
    "?fields=name,latitude,longitude,pm2.5_atm,pm2.5_atm_a,pm2.5_atm_b,humidity,temperature,last_seen"
    "&location_type=0&nwlat=38.15&selat=37.75&nwlng=-108.05&selng=-107.20"
)

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
