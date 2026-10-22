"""Build a compact Delhi bus dataset for the Kidhar Hai web app from a GTFS folder.
Usage: python3 build_data.py <gtfs_dir> <out.json>
"""
import csv, collections, json, math, re, sys

src, out = sys.argv[1], sys.argv[2]

def hms(t):
    h, m, s = map(int, t.split(':')); return h * 3600 + m * 60 + s

stops_raw = list(csv.DictReader(open(f'{src}/stops.txt', encoding='utf-8-sig')))
sid_idx = {s['stop_id']: i for i, s in enumerate(stops_raw)}
def clean_name(n):
    n = re.sub(r'\s+', ' ', n.strip())
    return n
stops = [[clean_name(s['stop_name']), round(float(s['stop_lat']), 5), round(float(s['stop_lon']), 5)] for s in stops_raw]

routes_raw = {r['route_id']: r for r in csv.DictReader(open(f'{src}/routes.txt', encoding='utf-8-sig'))}
trip_route = {t['trip_id']: t['route_id'] for t in csv.DictReader(open(f'{src}/trips.txt', encoding='utf-8-sig'))}

trip_st = collections.defaultdict(list)
for r in csv.DictReader(open(f'{src}/stop_times.txt', encoding='utf-8-sig')):
    trip_st[r['trip_id']].append((int(r['stop_sequence']), sid_idx[r['stop_id']], hms(r['arrival_time'])))

by_route = collections.defaultdict(list)
for tid, st in trip_st.items():
    st.sort(); by_route[trip_route[tid]].append(st)

def display(name):
    m = re.match(r'^(.*?)(UP|DOWN)(\d*)$', name)
    base, d = (m.group(1), m.group(2)) if m else (name, '')
    base = re.sub(r'(STL|LNK|EXT)', r' \1', base).replace('  ', ' ').strip()
    return base, d

routes, maxdev = [], 0
for rid, trips in by_route.items():
    trips.sort(key=lambda s: s[0][2])
    ref = trips[0]
    seq = [x[1] for x in ref]
    offs = [x[2] - ref[0][2] for x in ref]
    for t in trips[1:]:
        o = [x[2] - t[0][2] for x in t]
        maxdev = max(maxdev, max(abs(a - b) for a, b in zip(o, offs)))
    starts = [t[0][2] // 60 for t in trips]
    name, d = display(routes_raw[rid]['route_long_name'])
    routes.append({'n': name, 'd': d, 'a': routes_raw[rid]['agency_id'],
                   's': seq, 'o': [round(x / 6) for x in offs],   # offsets in 0.1 min
                   't': starts})

# walking transfers between nearby stops (<= 300 m)
def dist(a, b):
    la = math.radians((a[1] + b[1]) / 2)
    dx = (b[2] - a[2]) * 111320 * math.cos(la); dy = (b[1] - a[1]) * 110540
    return math.hypot(dx, dy)
cell = collections.defaultdict(list)
for i, s in enumerate(stops): cell[(int(s[1] / 0.003), int(s[2] / 0.003))].append(i)
transfers = []
for i, s in enumerate(stops):
    cx, cy = int(s[1] / 0.003), int(s[2] / 0.003)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for j in cell[(cx + dx, cy + dy)]:
                if j > i:
                    d = dist(s, stops[j])
                    if d <= 300: transfers.append([i, j, max(1, round(d / 70))])  # minutes walking

data = {'stops': stops, 'routes': routes, 'transfers': transfers,
        'source': 'Delhi Open Transit Data (DTC + DIMTS) static GTFS, service 2022-2024'}
json.dump(data, open(out, 'w'), separators=(',', ':'))
print(f'stops {len(stops)} routes {len(routes)} trips {sum(len(r["t"]) for r in routes)} transfers {len(transfers)} max per-trip offset deviation {maxdev}s')
