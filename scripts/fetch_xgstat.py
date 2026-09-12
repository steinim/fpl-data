#!/usr/bin/env python3
"""
Henter spillerstatistikk fra xgstat.com og skriver til data/.

xgstat har ikke noe offentlig data-API. Sidene er Next.js App Router med
ISR-prerender (x-nextjs-prerender: 1, stale-time 300s), saa hele tabellen
ligger ferdig i HTML-en. Vi parser HTML direkte -- ingen JS-kjoring noedvendig.
robots.txt tillater alt utenom /api/, /sign-in, /sign-up og /account/.

To tabeller:
  fpl      /competitions/{comp}/fpl      -- FPL-vinklet (pris, eierskap, defcon, setpieces)
  players  /competitions/{comp}/players  -- xG-vinklet (xG, xA, xGI, over/underpresentasjon)

Begge er haardt begrenset til 500 spillere (10 sider x 50). perPage over 50
ignoreres av serveren. Sidegrensene overlapper med én rad, saa vi dedupliserer
paa data-row-id -- forvent 498, ikke 500, unike spillere.

Join mot FPL-data: xgstat eksponerer ingen FPL element-id. Bruk slug
(/players/cody-gakpo) eller row_id som noekkel, og map mot players.csv
paa navn + lag ved foerste kjoering.

Miljovariabler:
  XGSTAT_COMPETITION  default "premier-league"
  XGSTAT_SEASON       valgfri, f.eks. "2026-2027". Utelatt = inneverende sesong.
  XGSTAT_UA           overstyr User-Agent
  XGSTAT_PAUSE        sekunder mellom sider, default 2

  python3 scripts/fetch_xgstat.py --diag   ett kall, skriver ut status,
                                           proxy-env og blokkeringsaarsak
"""

import csv
import gzip
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://www.xgstat.com"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Vercel-kanten svarer 429 paa forespoersler som ikke ser ut som en nettleser --
# umiddelbart, ikke etter volum. 12 raske kall fra samme IP med nettleser-
# identitet gikk gjennom med 200 og cache HIT. Send derfor et komplett
# nettleser-headersett. Overstyr med XGSTAT_UA hvis det slutter aa virke.
UA = os.environ.get("XGSTAT_UA", "").strip() or (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9,nb;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

COMPETITION = os.environ.get("XGSTAT_COMPETITION", "premier-league").strip()
SEASON = os.environ.get("XGSTAT_SEASON", "").strip()
PAUSE = float(os.environ.get("XGSTAT_PAUSE", "2"))    # sekunder mellom sider

PER_PAGE = 50          # serveren kapper alt over dette
MAX_PAGES = 12         # 10 sider med data i 26/27; slakk hvis de utvider
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


# ---------------------------------------------------------------- HTML-parsing

class TableParser(HTMLParser):
    """Plukker ut alle tbody-rader med celletekst, lenker og bilde-alt."""

    VOID = {"img", "br", "input", "hr", "meta", "link", "source", "path", "rect", "use"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._tbody = 0
        self._row = None
        self._cell = None
        self._a = None
        self._depth = 0        # nivaa inne i gjeldende <a>
        self._span = None      # tekst for span paa nivaa 1 i <a>

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tbody":
            self._tbody += 1
        elif tag == "tr" and self._tbody:
            self._row = {"id": a.get("data-row-id", ""), "cells": []}
        elif tag == "td" and self._row is not None:
            self._cell = {"text": [], "links": []}
        elif self._cell is not None:
            if tag == "a" and self._a is None:
                self._a = {"href": a.get("href", ""), "parts": [], "spans": []}
                self._depth = 0
            elif self._a is not None and tag not in self.VOID:
                self._depth += 1
                if self._depth == 1 and tag == "span":
                    self._span = []

    def handle_startendtag(self, tag, attrs):
        if tag not in self.VOID:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self._a is not None and self._cell is not None:
            if tag == "a" and self._depth == 0:
                self._a["text"] = " ".join(self._a["parts"])
                self._cell["links"].append(self._a)
                self._a = None
                return
            if self._depth >= 1:
                if self._depth == 1 and tag == "span" and self._span is not None:
                    self._a["spans"].append("".join(self._span).strip())
                    self._span = None
                self._depth -= 1
                return
        if tag == "td" and self._cell is not None:
            self._cell["text"] = re.sub(r"\s+", " ", "".join(self._cell["text"])).strip()
            self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row["cells"]:
                self.rows.append(self._row)
            self._row = None
        elif tag == "tbody" and self._tbody:
            self._tbody -= 1

    def handle_data(self, d):
        if self._a is not None and d.strip():
            self._a["parts"].append(d.strip())
        if self._span is not None:
            self._span.append(d)
        if self._cell is not None:
            self._cell["text"].append(d)


def _body(resp):
    raw = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").strip().lower()
    if "gzip" in enc:
        raw = gzip.decompress(raw)
    elif "deflate" in enc:
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    elif enc and enc != "identity":
        # Vi ber aldri om br. Faar vi det likevel, skal det smelle her og ikke
        # forplante seg som «tom tabell» tre steg lenger nede.
        raise RuntimeError(f"ukjent Content-Encoding: {enc!r}")
    return raw.decode("utf-8", "replace")


def _explain(e):
    """Skriver ut nok til aa se HVA som blokkerer, ikke bare at noe gjorde det."""
    hdrs = getattr(e, "headers", None) or {}
    interesting = {k: v for k, v in dict(hdrs).items()
                   if k.lower().startswith(("retry-after", "x-vercel", "x-ratelimit",
                                            "cf-", "server", "x-matched-path"))}
    print(f"    respons-headere: {interesting or '(ingen)'}")
    try:
        print(f"    kropp: {e.read()[:300].decode('utf-8', 'replace')!r}")
    except Exception:
        pass


def _challenged(e):
    """Vercel Attack Challenge Mode -- en JS-utfordring, ikke en koe.

    Verifisert 12. september 2026 fra en GitHub-runner (pdx1): foerste kall ga
    429 med X-Vercel-Mitigated: challenge og «Vercel Security Checkpoint» i
    kroppen. Den loeser seg ikke opp av seg selv, saa gjentatte forsoek er bare
    stoey mot en side som allerede har sagt nei. Avbryt umiddelbart.
    """
    h = getattr(e, "headers", None) or {}
    return bool(h.get("X-Vercel-Mitigated") or h.get("X-Vercel-Challenge-Token"))


def get(url, tries=5):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=45) as r:
                return _body(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                print(f"  {url}: HTTP 404, gir opp")
                return None
            if _challenged(e):
                print(f"  {url}: HTTP {e.code} -- Vercel-utfordring "
                      f"(X-Vercel-Mitigated: {(e.headers or {}).get('X-Vercel-Mitigated')})")
                print("    Denne IP-en maa loese en JS-utfordring. Det gjoer ikke dette")
                print("    skriptet. Kjoer hoestingen fra en maskin som slipper gjennom.")
                return None
            print(f"  {url}: forsok {n + 1}/{tries} feilet (HTTP {e.code})")
            if n == 0:
                _explain(e)
            if e.code == 429:
                ra = (e.headers.get("Retry-After") or "").strip()
                wait = int(ra) if ra.isdigit() else min(15 * 2 ** n, 120)
                print(f"    429 -- venter {wait}s")
                time.sleep(wait)
                continue
            time.sleep(5 * (n + 1))
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            print(f"  {url}: forsok {n + 1}/{tries} feilet ({e})")
            time.sleep(5 * (n + 1))
    print(f"  {url}: feilet etter {tries} forsok ({last})")
    return None


def diag():
    """Ett kall, full utskrift. Returnerer 0 bare naar kallet faktisk lyktes.

    Rettet 12. september: denne returnerte tidligere None uansett utfall, saa
    Diagnose-steget i workflowen ble groent selv naar kallet ble blokkert.
    Et diagnosesteg som ikke kan feile er verre enn ingen -- det gir falsk ro.
    """
    url = f"{BASE}/competitions/{COMPETITION}/fpl?perPage={PER_PAGE}&page=1"
    proxies = {k: v for k, v in os.environ.items() if k.lower().endswith("_proxy")}
    print(f"proxy-env: {proxies or '(ingen)'}")
    print(f"UA: {UA}")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=45) as r:
            html = _body(r)
            status, cache = r.status, r.headers.get("x-vercel-cache")
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}")
        _explain(e)
        return 1
    except Exception as e:
        print(f"❌ feil: {type(e).__name__}: {e}")
        return 1
    rows = html.count("data-row-id")
    print(f"HTTP {status}, {len(html)} tegn, x-vercel-cache={cache}, rader={rows}")
    if rows == 0:
        print("❌ 200 OK, men ingen rader i markupen -- siden kan ha endret seg")
        return 1
    return 0


# ------------------------------------------------------------------ normalisering

def num(s):
    """'£7.2m' -> 7.2, '273\\'' -> 273, '13.0%' -> 13.0, '-' -> ''."""
    if s is None:
        return ""
    s = s.strip().replace("−", "-")
    if s in ("", "-", "–", "—", "N/A"):
        return ""
    s = s.replace("£", "").replace("%", "").replace("'", "").replace("m", "")
    s = s.replace(",", "").replace("+", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return ""
    return s


def fixtures(cell):
    """Next 3 -> [(venue, motstander, dato), ...] fra lenkene i cella."""
    out = []
    for link in cell["links"]:
        parts = link["text"].split()
        venue = parts[0] if parts else ""
        opp = parts[1] if len(parts) > 1 else ""
        m = DATE_RE.search(link["href"] or "")
        out.append((venue, opp, m.group(1) if m else ""))
    while len(out) < 3:
        out.append(("", "", ""))
    return out[:3]


def identity(row):
    """row_id, slug, navn og skadestatus fra navnecella.

    Navnecella er <a href=/players/slug>[<img>|<div>initialer</div>]<span>Navn</span></a>
    etterfulgt av en fritt staaende <span>Inj|Dbt|...</span>. Navnet er siste
    direkte span-barn i lenka -- ikke celletekst, som limer initialene foran.
    """
    cell = row["cells"][1]
    slug, name = "", ""
    for link in cell["links"]:
        if (link["href"] or "").startswith("/players/"):
            slug = link["href"].split("/players/", 1)[1].strip("/")
            name = link["spans"][-1] if link["spans"] else link["text"]
            break
    raw = cell["text"]
    status = raw.rsplit(name, 1)[1].strip() if name and name in raw else ""
    return row["id"], slug, name, status


def team_slug(row):
    for link in row["cells"][2]["links"]:
        if (link["href"] or "").startswith("/teams/"):
            return link["href"].split("/teams/", 1)[1].strip("/")
    return ""


# ------------------------------------------------------------------ tabelldefinisjoner

# (kolonnenavn, celleindeks, numerisk?) -- indeksene er verifisert mot 26/27-oppsettet.
FPL_COLS = [
    ("team", 2, False), ("pos", 3, False),
    ("mins", 5, True), ("price", 6, True), ("price_change_season", 7, True),
    ("owned_pct", 8, True), ("pts", 9, True), ("pts_per_game", 10, True),
    ("bonus", 11, True), ("xgi_l5", 12, True), ("start_pct", 13, True),
    ("goals", 14, True), ("assists", 15, True), ("cs", 16, True),
    ("defcon", 17, True), ("pens", 18, False), ("corners", 19, False),
    ("fk", 20, False), ("play_pct", 21, True),
]
FPL_FIXTURE_CELL = 4

# Kolonne 7 (Form) og 17-19 (Goals vs xG, Assists vs xA, G+A vs xGI) er rene
# SVG-grafikk uten tekst -- de kan ikke leses ut. Regn dem selv: goals - xg osv.
PLAYERS_COLS = [
    ("team", 2, False), ("pos", 3, False),
    ("mins", 4, True), ("rating", 5, True), ("start_pct", 6, True),
    ("xgi_l5", 8, True), ("xgi", 9, True),
    ("goals", 11, True), ("assists", 12, True), ("g_a", 13, True),
    ("xg", 14, True), ("xa", 15, True), ("def_actions", 16, True),
    ("price", 20, True), ("owned_pct", 21, True),
]
PLAYERS_FIXTURE_CELL = 10

TABLES = {
    "fpl": (FPL_COLS, FPL_FIXTURE_CELL),
    "players": (PLAYERS_COLS, PLAYERS_FIXTURE_CELL),
}


def scrape(table):
    cols, fx_cell = TABLES[table]
    path = f"/competitions/{COMPETITION}/{SEASON}/{table}" if SEASON else f"/competitions/{COMPETITION}/{table}"
    seen, rows, expected = set(), [], None

    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}{path}?perPage={PER_PAGE}&page={page}"
        html = get(url)
        if html is None:
            # Skriv ALDRI en delvis tabell. En avkortet fil med riktige felter
            # er farligere enn ingen fil -- den ser komplett ut.
            print(f"  {table}: side {page} kunne ikke hentes. Avbryter uten aa skrive fil "
                  f"({len(rows)} rader ville blitt kastet bort).")
            return None
        p = TableParser()
        p.feed(html)
        if not p.rows:
            if page == 1:
                print(f"  {table}: side 1 ga 0 rader -- markupen kan ha endret seg")
                return None
            break      # normal slutt paa pagineringen
        if expected is None:
            expected = len(p.rows[0]["cells"])
        for row in p.rows:
            if len(row["cells"]) != expected:
                continue
            row_id, slug, name, status = identity(row)
            key = row_id or slug
            if not key or key in seen:
                continue      # sidegrensene overlapper med én rad
            seen.add(key)
            rec = {
                "row_id": row_id, "slug": slug, "name": name, "status": status,
                "team_slug": team_slug(row), "rank": row["cells"][0]["text"],
            }
            for col, idx, is_num in cols:
                raw = row["cells"][idx]["text"]
                rec[col] = num(raw) if is_num else raw
            for i, (venue, opp, date) in enumerate(fixtures(row["cells"][fx_cell]), 1):
                rec[f"next{i}_venue"], rec[f"next{i}_opp"], rec[f"next{i}_date"] = venue, opp, date
            rows.append(rec)
        print(f"  {table} side {page}: {len(p.rows)} rader, {len(rows)} unike totalt")
        time.sleep(PAUSE)

    if not rows:
        print(f"  {table}: ingen rader -- markupen kan ha endret seg")
        return None

    fields = ["row_id", "slug", "name", "status", "team_slug", "rank"] + [c for c, _, _ in cols]
    fields += [f"next{i}_{k}" for i in (1, 2, 3) for k in ("venue", "opp", "date")]
    out = DATA / f"xgstat_{table}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  skrev {out.relative_to(ROOT)} ({len(rows)} spillere)")
    return len(rows)


def main():
    if "--diag" in sys.argv[1:]:
        return diag()
    failed = []
    for table in ("fpl", "players"):
        print(f"{table}:")
        if not scrape(table):
            failed.append(table)
    if failed:
        print(f"\n❌ feilet: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
