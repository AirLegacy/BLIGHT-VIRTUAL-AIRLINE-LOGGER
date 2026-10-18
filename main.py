"""
Blighty VA Logger
Tracks BLY* flights on VATSIM and stores them for VA Partner audits.
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
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
online_cache: dict[int, dict] = {}  # cid -> flight data


async def fetch_vatsim() -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(VATSIM_URL)
        r.raise_for_status()
        data = r.json()
        return data.get("pilots", [])


def parse_aircraft(fp: Optional[dict]) -> str:
    if not fp:
        return ""
    # Prefer aircraft_short, otherwise take first part before /
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

        # New connection
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
            # Update aircraft / airports if they appeared later
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

    # Detect disconnects
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
templates = Jinja2Templates(directory="templates")


# ------------------- API / PAGES -------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row

        # Currently online
        online = await db.execute_fetchall(
            "SELECT * FROM flights WHERE status = 'online' ORDER BY logon_time DESC"
        )

        # Recent completed (last 50)
        completed = await db.execute_fetchall(
            "SELECT * FROM flights WHERE status = 'completed' ORDER BY logoff_time DESC LIMIT 50"
        )

        # Stats for last 90 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        stats = await db.execute_fetchall(
            """
            SELECT
                COUNT(*) as total_flights,
                COUNT(DISTINCT cid) as unique_pilots
            FROM flights
            WHERE logon_time >= ? AND status = 'completed'
            """,
            (cutoff,),
        )
        total_flights = stats[0]["total_flights"] if stats else 0
        unique_pilots = stats[0]["unique_pilots"] if stats else 0

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "online": online,
            "completed": completed,
            "total_flights": total_flights,
            "unique_pilots": unique_pilots,
            "prefix": AIRLINE_PREFIX,
            "logger_name": "Blighty VA Logger",
        },
    )


@app.get("/export.csv")
async def export_csv(
    days: int = Query(90, ge=1, le=365),
):
    """CSV export suitable for VATSIM VA Partner audits"""
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


@app.get("/api/online")
async def api_online():
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM flights WHERE status = 'online' ORDER BY logon_time DESC"
        )
        return [dict(r) for r in rows]


@app.get("/health")
async def health():
    return {"status": "ok", "prefix": AIRLINE_PREFIX, "online_tracked": len(online_cache)}
