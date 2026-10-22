"""
Kidhar Hai - live feed worker (Phase 0)

Pulls Delhi's live bus positions (GTFS-Realtime) once every 10 seconds,
cleans them, and serves one cached JSON snapshot to every app user.
Phones never call the government API directly.

    Delhi OTD feed --(every 10s, once)--> this worker --> /buses.json --> all phones

Setup:
    pip install requests gtfs-realtime-bindings fastapi uvicorn
    export OTD_KEY=your_key_from_otd.delhi.gov.in
    uvicorn feed:app --port 8000

Then open http://localhost:8000/buses.json
"""

import asyncio
import math
import os
import time

import requests
from fastapi import FastAPI
from google.transit import gtfs_realtime_pb2

FEED_URL = "https://otd.delhi.gov.in/api/realtime/VehiclePositions.pb"
API_KEY = os.environ.get("OTD_KEY", "")   # stays on the server, never in the app
POLL_SECONDS = 10
MAX_SPEED_KMH = 90                         # anything faster is a GPS glitch
STALE_AFTER_S = 180                        # no update for 3 min = "last seen"
STUCK_AFTER_S = 15 * 60                    # not moved for 15 min = possible breakdown

DELHI_BOX = (28.40, 28.90, 76.80, 77.40)   # lat_min, lat_max, lon_min, lon_max

app = FastAPI(title="Kidhar Hai feed")

state = {
    "buses": {},          # vehicle_id -> cleaned record
    "updated_at": 0,      # last successful fetch
    "feed_ok": False,
    "error": None,
}


def km_between(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def in_delhi(lat, lon):
    return DELHI_BOX[0] <= lat <= DELHI_BOX[1] and DELHI_BOX[2] <= lon <= DELHI_BOX[3]


def fetch_once():
    resp = requests.get(FEED_URL, params={"key": API_KEY}, timeout=8)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def clean(feed):
    now = time.time()
    seen = 0
    for ent in feed.entity:
        if not ent.HasField("vehicle"):
            continue
        v = ent.vehicle
        vid = v.vehicle.id or ent.id
        lat, lon = v.position.latitude, v.position.longitude
        ts = v.timestamp or int(now)

        if not in_delhi(lat, lon):          # (0,0) and junk coordinates
            continue

        prev = state["buses"].get(vid)
        if prev and ts > prev["ts"]:
            dt_h = (ts - prev["ts"]) / 3600
            if dt_h > 0 and km_between(prev["lat"], prev["lon"], lat, lon) / dt_h > MAX_SPEED_KMH:
                continue                    # jump: ignore this point, keep last good one

        moved = not prev or km_between(prev["lat"], prev["lon"], lat, lon) > 0.05
        state["buses"][vid] = {
            "id": vid,
            "route": v.trip.route_id or None,
            "trip": v.trip.trip_id or None,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "speed_kmh": round(v.position.speed * 3.6, 1) if v.position.HasField("speed") else None,
            "ts": ts,
            "last_moved": ts if moved else prev["last_moved"],
        }
        seen += 1
    return seen


async def poll_forever():
    while True:
        try:
            feed = await asyncio.to_thread(fetch_once)
            n = clean(feed)
            state.update(updated_at=time.time(), feed_ok=True, error=None)
            print(f"[{time.strftime('%H:%M:%S')}] {n} buses")
        except Exception as e:              # feed down: keep serving last good data
            state.update(feed_ok=False, error=str(e)[:200])
            print(f"[{time.strftime('%H:%M:%S')}] feed error: {e}")
        await asyncio.sleep(POLL_SECONDS)


@app.on_event("startup")
async def start():
    if not API_KEY:
        print("OTD_KEY not set. Register at https://otd.delhi.gov.in to get one.")
    asyncio.create_task(poll_forever())


@app.get("/buses.json")
def buses():
    now = time.time()
    out = []
    for b in state["buses"].values():
        age = now - b["ts"]
        out.append({
            **b,
            "stale": age > STALE_AFTER_S,
            "possible_breakdown": (now - b["last_moved"]) > STUCK_AFTER_S and age < STALE_AFTER_S,
            "age_s": int(age),
        })
    return {
        "feed_ok": state["feed_ok"],
        "data_age_s": int(now - state["updated_at"]) if state["updated_at"] else None,
        "error": state["error"],
        "count": len(out),
        "buses": out,
    }


@app.get("/health")
def health():
    fresh = state["updated_at"] and time.time() - state["updated_at"] < 60
    return {"ok": bool(fresh), "feed_ok": state["feed_ok"]}
