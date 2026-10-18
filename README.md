# Blighty VA Logger

A simple, always-on flight logger for **Blighty VA** (`BLY*` callsigns) on VATSIM.

It automatically records:
- Callsign
- Pilot CID
- Logon & Logoff times (Zulu)
- Departure & Arrival airports
- Aircraft ICAO
- Airline ICAO (BLY)
- Total flight time

Perfect for VATSIM Virtual Airline Partner audits.

---

## Quick Start (No coding knowledge needed)

### Step 1 – Create a free GitHub account
1. Go to https://github.com and sign up (free).
2. Remember your username and password.

### Step 2 – Create a new repository
1. After logging in, click the **+** (top right) → **New repository**.
2. Name it: `blighty-va-logger`
3. Make sure it is set to **Public**.
4. Click **Create repository**.

### Step 3 – Upload the files
1. On the new empty repository page, click **uploading an existing file**.
2. Drag **all** the files from the `blighty-va-logger` folder into the browser window:
   - `main.py`
   - `requirements.txt`
   - `.env.example`
   - the whole `templates` folder
3. Scroll down and click **Commit changes**.

### Step 4 – Deploy (easiest free options)

#### Option A – Botkeep (recommended for beginners, truly 24/7 free)
1. Go to https://botkeep.cloud
2. Sign up (no credit card needed).
3. Create a new **Python** workload.
4. Connect your GitHub account and select the `blighty-va-logger` repository.
5. Set the start command to:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
6. Click Deploy / Start.
7. Once it is running you will get a public URL – that is your logger!

#### Option B – Railway (also good)
1. Go to https://railway.app and sign up with GitHub.
2. Click **New Project** → **Deploy from GitHub repo**.
3. Select `blighty-va-logger`.
4. Railway will detect it is a Python app.  
   Add a variable if you want: `AIRLINE_PREFIX=BLY` (already default).
5. After deploy finishes, open the public URL Railway gives you.

#### Option C – Other free always-on hosts
- Silly Development (https://sillydev.co.uk)
- Wispbyte
- bot-hosting.net

Same idea: connect GitHub → set start command to `uvicorn main:app --host 0.0.0.0 --port $PORT`

---

## Using the Logger

Once deployed, open the website URL you received.

You will see:
- How many BLY flights are currently online
- How many completed flights and unique pilots in the last 90 days
- Live table of online flights
- Recent completed flights
- Big **Export 90-day CSV** button (exactly what VATSIM auditors need)

The page auto-refreshes every 60 seconds.

---

## Important Notes

- The logger only starts tracking flights **after** it is deployed. Old flights before that are not recorded.
- It needs to stay running 24/7 – that is why we use the free always-on hosts above.
- Data is stored in a file called `flights.db` on the server. Most free hosts keep this between restarts, but for maximum safety you can later upgrade to a paid plan with persistent storage.

---

## Need help?

Just reply in the chat and tell me:
- Which hosting you chose
- Any error message you see

I will guide you step by step.
