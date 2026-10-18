"""
Blighty VA Logger - Single file version
Tracks BLY* flights on VATSIM
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
import aiosqlite

# ------------------- CONFIG -------------------
AIRLINE_PREFIX = os.getenv("AIRLINE_PREFIX", "BLY").upper()
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "45"))
DATABASE = "flights.db"
VATSIM_URL = "https://data.vatsim.net/v3/vatsim-data.json"

# ------------------- DATABASE -------------------
def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cid INTEGER NOT NULL,
            callsign TEXT NOT NULL,
            airline_icao TEXT,
            aircraft_icao TEXT,
            departure TEXT,
            arrival TEXT,
            logon_time TEXT NOT NULL,
            logoff_time TEXT,
            total_minutes INTEGER,
            status TEXT DEFAULT 'online',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_callsign ON flights(callsign)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON flights(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logon ON flights(logon_time)")
    conn.commit()
    conn.close()


# ------------------- POLLER -------------------
online_cache: dict[int, dict] = {}


async def fetch_vatsim() -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(VATSIM_URL)
        r.raise_for_status()
        data = r.json()
        return data.get("pilots", [])


def parse_aircraft(fp: dict | None) -> str:
    if not fp:
        return ""
    short = fp.get("aircraft_short") or ""
    if short:
        return short.upper()
    full = fp.get("aircraft") or ""
    return full.split("/")[0].upper() if full else ""


async def process_pilots(pilots: list[dict]):
    global online_cache
    now = datetime.now(timezone.utc)
    current_cids = set()

    for p in pilots:
        callsign = (p.get("callsign") or "").upper()
        if not callsign.startswith(AIRLINE_PREFIX):
            continue

        cid = p.get("cid")
        if not cid:
            continue

        current_cids.add(cid)
        fp = p.get("flight_plan") or {}

        if cid not in online_cache:
            logon = p.get("logon_time") or now.isoformat()
            aircraft = parse_aircraft(fp)
            dep = (fp.get("departure") or "").upper()
            arr = (fp.get("arrival") or "").upper()

            online_cache[cid] = {
                "callsign": callsign,
                "logon_time": logon,
                "aircraft": aircraft,
                "departure": dep,
                "arrival": arr,
            }

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    INSERT INTO flights
                    (cid, callsign, airline_icao, aircraft_icao, departure, arrival, logon_time, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'online')
                    """,
                    (cid, callsign, AIRLINE_PREFIX, aircraft, dep, arr, logon),
                )
                await db.commit()
            print(f"[+] ONLINE  {callsign} (CID {cid})  {dep} → {arr}")
        else:
            cache = online_cache[cid]
            aircraft = parse_aircraft(fp)
            dep = (fp.get("departure") or "").upper()
            arr = (fp.get("arrival") or "").upper()
            if aircraft and not cache.get("aircraft"):
                cache["aircraft"] = aircraft
            if dep and not cache.get("departure"):
                cache["departure"] = dep
            if arr and not cache.get("arrival"):
                cache["arrival"] = arr

    for cid in list(online_cache.keys()):
        if cid not in current_cids:
            data = online_cache.pop(cid)
            logoff = now.isoformat()
            try:
                logon_dt = datetime.fromisoformat(data["logon_time"].replace("Z", "+00:00"))
                minutes = int((now - logon_dt).total_seconds() / 60)
            except Exception:
                minutes = 0

            async with aiosqlite.connect(DATABASE) as db:
                await db.execute(
                    """
                    UPDATE flights
                    SET logoff_time = ?, total_minutes = ?, status = 'completed',
                        aircraft_icao = COALESCE(NULLIF(aircraft_icao, ''), ?),
                        departure = COALESCE(NULLIF(departure, ''), ?),
                        arrival = COALESCE(NULLIF(arrival, ''), ?)
                    WHERE cid = ? AND status = 'online'
                    """,
                    (logoff, minutes, data.get("aircraft", ""), data.get("departure", ""), data.get("arrival", ""), cid),
                )
                await db.commit()
            print(f"[-] OFFLINE {data['callsign']} (CID {cid})  duration {minutes} min")


async def poller_loop():
    print(f"Blighty VA Logger started — watching prefix {AIRLINE_PREFIX}*")
    while True:
        try:
            pilots = await fetch_vatsim()
            await process_pilots(pilots)
        except Exception as e:
            print(f"Poll error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


# ------------------- APP -------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(poller_loop())
    yield
    task.cancel()


app = FastAPI(title="Blighty VA Logger", lifespan=lifespan)


# ------------------- HTML PAGE -------------------
HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Blighty VA Logger</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', system-ui, sans-serif; }
    .mono { font-family: 'JetBrains Mono', monospace; }
    .card { background: #0f172a; border: 1px solid #1e293b; }
    .accent { color: #38bdf8; }
    .badge-online { background: #065f46; color: #6ee7b7; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen">
  <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-10">
    <div class="max-w-7xl mx-auto px-4 py-4 flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold tracking-tight">
          <span class="accent">Blighty</span> VA Logger
        </h1>
        <p class="text-slate-400 text-sm mt-0.5">Watching callsign prefix <span class="mono accent font-medium">BLY*</span></p>
      </div>
      <div class="flex items-center gap-3">
        <a href="/export.csv?days=90"
           class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium transition">
          Export 90-day CSV
        </a>
        <button onclick="location.reload()"
                class="px-4 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm font-medium transition">
          Refresh
        </button>
      </div>
    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 py-8 space-y-8">
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <div class="card rounded-xl p-5">
        <p class="text-slate-400 text-sm">Currently Online</p>
        <p class="text-3xl font-bold mt-1 mono accent">{{ online_count }}</p>
      </div>
      <div class="card rounded-xl p-5">
        <p class="text-slate-400 text-sm">Completed Flights (90 days)</p>
        <p class="text-3xl font-bold mt-1 mono">{{ total_flights }}</p>
      </div>
      <div class="card rounded-xl p-5">
        <p class="text-slate-400 text-sm">Unique Pilots (90 days)</p>
        <p class="text-3xl font-bold mt-1 mono {% if unique_pilots >= 15 %}text-emerald-400{% else %}text-amber-400{% endif %}">
          {{ unique_pilots }} <span class="text-sm font-normal text-slate-400">/ 15 needed</span>
        </p>
      </div>
    </div>

    <section>
      <h2 class="text-lg font-semibold mb-3 flex items-center gap-2">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        Live Flights
      </h2>
      {% if online %}
      <div class="card rounded-xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="bg-slate-800/60 text-slate-400 text-left">
              <tr>
                <th class="px-4 py-3 font-medium">Callsign</th>
                <th class="px-4 py-3 font-medium">CID</th>
                <th class="px-4 py-3 font-medium">Route</th>
                <th class="px-4 py-3 font-medium">Aircraft</th>
                <th class="px-4 py-3 font-medium">Logon (Z)</th>
                <th class="px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-800">
              {% for f in online %}
              <tr class="hover:bg-slate-800/40">
                <td class="px-4 py-3 mono font-medium accent">{{ f.callsign }}</td>
                <td class="px-4 py-3 mono">{{ f.cid }}</td>
                <td class="px-4 py-3 mono">{{ f.departure or "----" }} <span class="text-slate-500">→</span> {{ f.arrival or "----" }}</td>
                <td class="px-4 py-3 mono">{{ f.aircraft_icao or "—" }}</td>
                <td class="px-4 py-3 mono text-slate-300">{{ f.logon_time[:19] if f.logon_time else "—" }}Z</td>
                <td class="px-4 py-3"><span class="badge-online text-xs px-2 py-0.5 rounded-full font-medium">ONLINE</span></td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      {% else %}
      <div class="card rounded-xl p-8 text-center text-slate-500">
        No BLY flights currently online on VATSIM
      </div>
      {% endif %}
    </section>

    <section>
      <h2 class="text-lg font-semibold mb-3">Recent Completed Flights</h2>
      {% if completed %}
      <div class="card rounded-xl overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead class="bg-slate-800/60 text-slate-400 text-left">
              <tr>
                <th class="px-4 py-3 font-medium">Callsign</th>
                <th class="px-4 py-3 font-medium">CID</th>
                <th class="px-4 py-3 font-medium">Route</th>
                <th class="px-4 py-3 font-medium">Aircraft</th>
                <th class="px-4 py-3 font-medium">Logon (Z)</th>
                <th class="px-4 py-3 font-medium">Logoff (Z)</th>
                <th class="px-4 py-3 font-medium">Duration</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-800">
              {% for f in completed %}
              <tr class="hover:bg-slate-800/40">
                <td class="px-4 py-3 mono font-medium">{{ f.callsign }}</td>
                <td class="px-4 py-3 mono">{{ f.cid }}</td>
                <td class="px-4 py-3 mono">{{ f.departure or "----" }} <span class="text-slate-500">→</span> {{ f.arrival or "----" }}</td>
                <td class="px-4 py-3 mono">{{ f.aircraft_icao or "—" }}</td>
                <td class="px-4 py-3 mono text-slate-300">{{ f.logon_time[:19] if f.logon_time else "—" }}Z</td>
                <td class="px-4 py-3 mono text-slate-300">{{ f.logoff_time[:19] if f.logoff_time else "—" }}Z</td>
                <td class="px-4 py-3 mono">
                  {% if f.total_minutes %}{{ (f.total_minutes // 60) }}h {{ (f.total_minutes % 60) }}m{% else %}—{% endif %}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      {% else %}
      <div class="card rounded-xl p-8 text-center text-slate-500">
        No completed flights yet. The logger is watching…
      </div>
      {% endif %}
    </section>

    <footer class="text-center text-slate-600 text-xs pt-4 pb-8">
      Blighty VA Logger • Data from VATSIM public feed • Auto-refreshes every 60 seconds<br>
      For VATSIM Partner audits: use the Export button above
    </footer>
  </main>
  <script>setTimeout(() => location.reload(), 60000);</script>
</body>
</html>
"""


from jinja2 import Template

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row

        online = await db.execute_fetchall(
            "SELECT * FROM flights WHERE status = 'online' ORDER BY logon_time DESC"
        )
        completed = await db.execute_fetchall(
            "SELECT * FROM flights WHERE status = 'completed' ORDER BY logoff_time DESC LIMIT 50"
        )

        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        stats = await db.execute_fetchall(
            """
            SELECT COUNT(*) as total_flights, COUNT(DISTINCT cid) as unique_pilots
            FROM flights WHERE logon_time >= ? AND status = 'completed'
            """,
            (cutoff,),
        )
        total_flights = stats[0]["total_flights"] if stats else 0
        unique_pilots = stats[0]["unique_pilots"] if stats else 0

    template = Template(HTML_PAGE)
    html = template.render(
        online=online,
        completed=completed,
        online_count=len(online),
        total_flights=total_flights,
        unique_pilots=unique_pilots,
    )
    return HTMLResponse(html)


@app.get("/export.csv")
async def export_csv(days: int = Query(90, ge=1, le=365)):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT callsign, cid, departure, arrival, logon_time, logoff_time,
                   aircraft_icao, total_minutes
            FROM flights
            WHERE logon_time >= ? AND status = 'completed'
            ORDER BY logon_time DESC
            """,
            (cutoff,),
        )

    def generate():
        yield "Callsign,CID,Departure,Arrival,Logon (Z),Logoff (Z),Aircraft,Duration (min)\n"
        for r in rows:
            yield f"{r['callsign']},{r['cid']},{r['departure'] or ''},{r['arrival'] or ''},"
            yield f"{r['logon_time']},{r['logoff_time'] or ''},{r['aircraft_icao'] or ''},{r['total_minutes'] or 0}\n"

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=blighty_va_flights_{days}d.csv"},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "prefix": AIRLINE_PREFIX, "online_tracked": len(online_cache)}
