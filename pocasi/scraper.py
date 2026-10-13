#!/usr/bin/env python3
"""
Scraper počasí pro dashboard (GitHub Actions).

Stahuje aktuální data ze dvou lokálních stanic, které neumožňují načítání
přímo z prohlížeče (chybí CORS):
  - Praha 6, Silicon Hill (Strahov)  https://weather.sh.cvut.cz/weather/
  - Frýdlant v Čechách (Předměstí)   https://www.pocasi-frydlant.cz/frydlant.php

Výsledek zapisuje do data.json (aktuální hodnoty + 24h historie teploty)
a force-pushuje ho do větve `data` (jediný commit, historie neroste).
Kitzbühel (GeoSphere Austria) čte stránka přímo – API CORS povoluje.

Použití:
  python scraper.py --once              # jedno stažení, výpis JSON
  python scraper.py --loop 20 --push    # 20 minut, každých 60 s, push do větve data
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Prague")
UA = "pocasi-dashboard/1.0 (+https://github.com/zededez/test-se-Zdendou; osobni dashboard, 1 dotaz/min)"
SH_URL = "https://weather.sh.cvut.cz/weather/"
FR_URL = "https://www.pocasi-frydlant.cz/frydlant.php"
REPO = os.environ.get("GITHUB_REPOSITORY", "zededez/test-se-Zdendou")
DATA_BRANCH = "data"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{DATA_BRANCH}/data.json"
HISTORY_HOURS = 26
HISTORY_STEP_S = 5 * 60 - 20  # bod historie max. jednou za ~5 min


def fetch(url, encoding=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if encoding is None:
            m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""), re.I)
            encoding = m.group(1) if m else "utf-8"
    return raw.decode(encoding, errors="replace")


def to_text(h):
    """HTML -> čitelný text: tagy pryč, entity dekódované, mezery sjednocené."""
    h = re.sub(r"(?s)<!--.*?-->", " ", h)
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</(div|p|tr|td|li|h\d)>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h).replace("\xa0", " ")
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\s*\n\s*", "\n", h)
    return h


def num(s):
    if s is None:
        return None
    try:
        return float(s.replace(",", ".").strip())
    except ValueError:
        return None


def rx(pattern, text, group=1, flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(group) if m else None


# ── Praha – Silicon Hill ────────────────────────────────────────────────
def parse_sh(t):
    d = {}
    ts = rx(r"(20\d\d-\d\d-\d\d \d\d:\d\d:\d\d)", t)
    if ts:
        d["measured"] = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).isoformat()
    d["temp"] = num(rx(r"\bTeplota:\s*(-?[\d.,]+)\s*°C", t))
    d["humidity"] = num(rx(r"Relativní vlhkost:\s*([\d.,]+)\s*%", t))
    d["pressure"] = num(rx(r"Tlak vzduchu:\s*([\d.,]+)\s*hPa", t))
    d["dewpoint"] = num(rx(r"Rosný bod:\s*(-?[\d.,]+)\s*°C", t))
    wd = rx(r"Charakter větru:\s*([NESW]{1,3})\b", t, flags=0)
    d["wind_dir"] = wd.translate(str.maketrans("NESW", "SVJZ")) if wd else None
    ms = num(rx(r"Charakter větru:\s*[A-Z]{0,3}\s*([\d.,]+)\s*m/s", t))
    d["wind_kmh"] = round(ms * 3.6, 1) if ms is not None else None
    d["rain_today"] = num(rx(r"Dnešní srážky:\s*([\d.,]+)\s*mm", t))
    d["tmax"] = num(rx(r"Teplota \(maximum\):\s*(-?[\d.,]+)\s*°C", t))
    d["tmin"] = num(rx(r"Teplota \(minimum\):\s*(-?[\d.,]+)\s*°C", t))
    return d


# ── Frýdlant – pocasi-frydlant.cz ───────────────────────────────────────
def parse_fr(t):
    d = {}
    m = re.search(r"Poslední aktualizace:\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s+(\d{1,2}):(\d{2})", t)
    if m:
        dd, mm, yy, hh, mi = map(int, m.groups())
        d["measured"] = datetime(yy, mm, dd, hh, mi, tzinfo=TZ).isoformat()
    # hlavní blok: "9,8 °C\nTeplota" (hodnota před popiskem)
    d["temp"] = num(rx(r"(-?[\d.,]+)\s*°C\s*\n\s*Teplota\b", t))
    if d["temp"] is None:
        d["temp"] = num(rx(r"Aktuální teplota:\s*(-?[\d.,]+)", t))
    d["humidity"] = num(rx(r"([\d.,]+)\s*%\s*\n\s*Vlhkost vzduchu", t))
    d["pressure"] = num(rx(r"([\d.,]+)\s*\n\s*Tlak vzduchu v hPa", t))
    d["dewpoint"] = num(rx(r"Rosný bod:\s*(-?[\d.,]+)\s*°C", t))
    d["wind_kmh"] = num(rx(r"Rychlost větru \(aktuální\):\s*([\d.,]+)\s*km/h", t))
    d["gust_kmh"] = num(rx(r"([\d.,]+)\s*km/h\s*\n\s*Nárazy větru", t))
    d["wind_dir"] = rx(r"Směr:\s*([A-ZÁ-Ž]{1,4})\b", t, flags=0)
    d["rain_today"] = num(rx(r"([\d.,]+)\s*mm\s*\n\s*Úhrn srážek", t))
    mm_ = re.search(r"Dnešní MinT / MaxT:\s*(-?[\d.,]+)\s*°C\s*/\s*(-?[\d.,]+)\s*°C", t)
    if mm_:
        d["tmin"], d["tmax"] = num(mm_.group(1)), num(mm_.group(2))
    return d


def scrape_one(key, url, parser, encoding=None):
    try:
        d = parser(to_text(fetch(url, encoding)))
        d["ok"] = d.get("temp") is not None
        if not d["ok"]:
            d["error"] = "teplotu se nepodařilo najít (změnila se stránka?)"
    except Exception as e:  # síť, timeout, HTTP chyba
        d = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    d["fetched"] = datetime.now(TZ).isoformat(timespec="seconds")
    d["source"] = url
    return d


def load_previous():
    try:
        return json.loads(fetch(f"{RAW_URL}?t={int(time.time())}", "utf-8"))
    except Exception:
        return {}


def update(state):
    now = datetime.now(TZ)
    stations = state.setdefault("stations", {})
    history = state.setdefault("history", {})
    for key, url, parser, enc in (
        ("praha", SH_URL, parse_sh, None),
        ("frydlant", FR_URL, parse_fr, None),
    ):
        new = scrape_one(key, url, parser, enc)
        if new["ok"]:
            stations[key] = new
            h = history.setdefault(key, [])
            ts = new.get("measured") or new["fetched"]
            last_ts = datetime.fromisoformat(h[-1][0]) if h else None
            if last_ts is None or (datetime.fromisoformat(ts) - last_ts).total_seconds() >= HISTORY_STEP_S:
                h.append([ts, new["temp"]])
        else:
            # ponecháme poslední dobrá data, jen označíme chybu
            old = stations.get(key, {})
            old.update({"ok": False, "error": new["error"], "fetched_failed": new["fetched"]})
            stations[key] = old
        cutoff = now - timedelta(hours=HISTORY_HOURS)
        history[key] = [p for p in history.get(key, []) if datetime.fromisoformat(p[0]) >= cutoff]
    state["updated"] = now.isoformat(timespec="seconds")
    return state


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def push(state, workdir):
    workdir = Path(workdir)
    if not (workdir / ".git").exists():
        workdir.mkdir(parents=True, exist_ok=True)
        git("init", "-q", "-b", DATA_BRANCH, cwd=workdir)
        token = os.environ["GITHUB_TOKEN"]
        git("remote", "add", "origin", f"https://x-access-token:{token}@github.com/{REPO}.git", cwd=workdir)
        git("config", "user.name", "pocasi-bot", cwd=workdir)
        git("config", "user.email", "pocasi-bot@users.noreply.github.com", cwd=workdir)
    (workdir / "data.json").write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    git("add", "data.json", cwd=workdir)
    # vždy jediný commit -> větev data nebobtná
    subprocess.run(["git", "update-ref", "-d", "HEAD"], cwd=workdir, capture_output=True)
    git("commit", "-q", "-m", f"data {state['updated']}", cwd=workdir)
    git("push", "-q", "--force", "origin", f"HEAD:{DATA_BRANCH}", cwd=workdir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=float, default=0, help="minut běhu smyčky")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--workdir", default="/tmp/pocasi-data")
    a = ap.parse_args()

    state = load_previous()
    if a.once or not a.loop:
        state = update(state)
        print(json.dumps(state["stations"], ensure_ascii=False, indent=1))
        if a.push:
            push(state, a.workdir)
        return

    end = time.monotonic() + a.loop * 60
    while time.monotonic() < end:
        t0 = time.monotonic()
        state = update(state)
        s = state["stations"]
        print(state["updated"], {k: (v.get("temp"), v.get("ok")) for k, v in s.items()}, flush=True)
        if a.push:
            try:
                push(state, a.workdir)
            except subprocess.CalledProcessError as e:
                print("push selhal:", e.stderr, file=sys.stderr, flush=True)
        time.sleep(max(1, a.interval - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
