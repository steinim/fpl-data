#!/usr/bin/env python3
"""
Henter FPL-data og skriver til data/.
Full JSON beholdes som siste snapshot; git-historikken er versjonsarkivet.
Slanke CSV-er skrives ved siden av for rask grep/pandas uten a parse 4 MB JSON.

Miljovariabler:
  FPL_ENTRY_ID  paakrevd, ditt manager-ID
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://fantasy.premierleague.com/api"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = "Mozilla/5.0 (compatible; fpl-snapshot/1.0; +https://github.com)"

ENTRY_ID = os.environ.get("FPL_ENTRY_ID", "").strip()
if not ENTRY_ID:
    sys.exit("FPL_ENTRY_ID er ikke satt")


def get(path, tries=4):
    url = f"{BASE}/{path}"
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            code = getattr(e, "code", None)
            if code == 404:
                print(f"  {path}: HTTP 404, gir opp")
                return None
            # 403 kan vaere forbigaaende Cloudflare-blokkering av datasenter-IP.
            print(f"  {path}: forsok {n + 1}/{tries} feilet ({code or e})")
            time.sleep(5 * (n + 1))
    print(f"  {path}: feilet etter {tries} forsok ({last})")
    return None


def write_json(obj, *parts):
    p = DATA.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"  skrev {p.relative_to(ROOT)}")


def write_csv(rows, fields, *parts):
    if not rows:
        return
    p = DATA.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  skrev {p.relative_to(ROOT)} ({len(rows)} rader)")


POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def main():
    print("bootstrap-static")
    boot = get("bootstrap-static/")
    if not boot:
        sys.exit("bootstrap-static feilet, avbryter")
    write_json(boot, "bootstrap-static.json")

    teams = {t["id"]: t["name"] for t in boot["teams"]}
    short = {t["id"]: t["short_name"] for t in boot["teams"]}
    elements = {e["id"]: e for e in boot["elements"]}

    # ---- players.csv: den slanke tabellen for daglig bruk
    players = []
    for e in boot["elements"]:
        players.append({
            "id": e["id"],
            "web_name": e["web_name"],
            "full_name": f'{e["first_name"]} {e["second_name"]}',
            "team": teams.get(e["team"], ""),
            "team_short": short.get(e["team"], ""),
            "pos": POS.get(e["element_type"], ""),
            "price": e["now_cost"] / 10,
            "cost_change_event": e.get("cost_change_event", 0) / 10,
            "cost_change_start": e.get("cost_change_start", 0) / 10,
            "selected_by_percent": e.get("selected_by_percent"),
            "total_points": e.get("total_points"),
            "form": e.get("form"),
            "points_per_game": e.get("points_per_game"),
            "minutes": e.get("minutes"),
            "starts": e.get("starts"),
            "goals_scored": e.get("goals_scored"),
            "assists": e.get("assists"),
            "clean_sheets": e.get("clean_sheets"),
            "saves": e.get("saves"),
            "bonus": e.get("bonus"),
            "bps": e.get("bps"),
            "defensive_contribution": e.get("defensive_contribution"),
            "expected_goals": e.get("expected_goals"),
            "expected_assists": e.get("expected_assists"),
            "expected_goals_conceded": e.get("expected_goals_conceded"),
            "ep_next": e.get("ep_next"),
            "status": e.get("status"),
            "chance_next": e.get("chance_of_playing_next_round"),
            "news": (e.get("news") or "").replace("\n", " "),
            "news_added": e.get("news_added"),
            "transfers_in_event": e.get("transfers_in_event"),
            "transfers_out_event": e.get("transfers_out_event"),
        })
    players.sort(key=lambda r: (-float(r["total_points"] or 0), r["web_name"]))
    write_csv(players, list(players[0].keys()), "players.csv")

    # ---- runder
    events = boot["events"]
    finished = [e["id"] for e in events if e.get("finished")]
    current = next((e["id"] for e in events if e.get("is_current")), max(finished) if finished else 0)
    nxt = next((e["id"] for e in events if e.get("is_next")), None)
    print(f"  ferdige runder: {finished or '-'} | current: {current} | next: {nxt}")

    write_csv([{
        "id": e["id"],
        "name": e["name"],
        "deadline_time": e["deadline_time"],
        "finished": e["finished"],
        "data_checked": e.get("data_checked"),
        "is_current": e.get("is_current"),
        "is_next": e.get("is_next"),
        "average_entry_score": e.get("average_entry_score"),
        "highest_score": e.get("highest_score"),
        "most_captained": e.get("most_captained"),
        "chip_plays": json.dumps(e.get("chip_plays", [])),
    } for e in events], ["id", "name", "deadline_time", "finished", "data_checked",
                          "is_current", "is_next", "average_entry_score", "highest_score",
                          "most_captained", "chip_plays"], "events.csv")

    print("fixtures")
    fixtures = get("fixtures/")
    if fixtures:
        write_json(fixtures, "fixtures.json")
        write_csv([{
            "id": f["id"],
            "event": f.get("event"),
            "kickoff_time": f.get("kickoff_time"),
            "home": teams.get(f["team_h"], ""),
            "away": teams.get(f["team_a"], ""),
            "home_score": f.get("team_h_score"),
            "away_score": f.get("team_a_score"),
            "finished": f.get("finished"),
            "fdr_home": f.get("team_h_difficulty"),
            "fdr_away": f.get("team_a_difficulty"),
        } for f in fixtures], ["id", "event", "kickoff_time", "home", "away", "home_score",
                               "away_score", "finished", "fdr_home", "fdr_away"], "fixtures.csv")

    # ---- manager
    print(f"entry {ENTRY_ID}")
    for path, name in [(f"entry/{ENTRY_ID}/", "entry.json"),
                       (f"entry/{ENTRY_ID}/history/", "history.json"),
                       (f"entry/{ENTRY_ID}/transfers/", "transfers.json")]:
        d = get(path)
        if d:
            write_json(d, "entry", name)
            if name == "history.json":
                write_csv([{
                    "gw": r["event"],
                    "points": r["points"],
                    "gw_rank": r["rank"],
                    "overall_rank": r["overall_rank"],
                    "total_points": r["total_points"],
                    "value": r["value"] / 10,
                    "bank": r["bank"] / 10,
                    "transfers": r["event_transfers"],
                    "hit": r["event_transfers_cost"],
                    "bench_points": r["points_on_bench"],
                } for r in d.get("current", [])],
                    ["gw", "points", "gw_rank", "overall_rank", "total_points", "value",
                     "bank", "transfers", "hit", "bench_points"], "entry", "history.csv")
            if name == "transfers.json":
                write_csv([{
                    "gw": t["event"],
                    "time": t["time"],
                    "in": elements.get(t["element_in"], {}).get("web_name", t["element_in"]),
                    "in_cost": t["element_in_cost"] / 10,
                    "out": elements.get(t["element_out"], {}).get("web_name", t["element_out"]),
                    "out_cost": t["element_out_cost"] / 10,
                } for t in d], ["gw", "time", "in", "in_cost", "out", "out_cost"],
                    "entry", "transfers.csv")

    # ---- per runde: live + picks
    targets = sorted(set(finished + ([current] if current else [])))
    for gw in targets:
        live_path = DATA / "live" / f"gw{gw}.json"
        refresh = gw >= current or not live_path.exists()
        if not refresh:
            continue
        print(f"runde {gw}")
        live = get(f"event/{gw}/live/")
        stats_by_id = {}
        if live:
            write_json(live, "live", f"gw{gw}.json")
            rows = []
            for el in live.get("elements", []):
                s = el.get("stats", {})
                stats_by_id[el["id"]] = s
                e = elements.get(el["id"], {})
                rows.append({
                    "id": el["id"],
                    "web_name": e.get("web_name", ""),
                    "team": teams.get(e.get("team"), ""),
                    "pos": POS.get(e.get("element_type"), ""),
                    "price": e.get("now_cost", 0) / 10,
                    "minutes": s.get("minutes"),
                    "goals": s.get("goals_scored"),
                    "assists": s.get("assists"),
                    "clean_sheets": s.get("clean_sheets"),
                    "goals_conceded": s.get("goals_conceded"),
                    "saves": s.get("saves"),
                    "defensive_contribution": s.get("defensive_contribution"),
                    "bonus": s.get("bonus"),
                    "bps": s.get("bps"),
                    "yellow_cards": s.get("yellow_cards"),
                    "red_cards": s.get("red_cards"),
                    "total_points": s.get("total_points"),
                })
            rows.sort(key=lambda r: -(r["total_points"] or 0))
            write_csv(rows, list(rows[0].keys()), "live", f"gw{gw}.csv")

        picks = get(f"entry/{ENTRY_ID}/event/{gw}/picks/")
        if picks:
            write_json(picks, "entry", "picks", f"gw{gw}.json")
            rows = []
            for p in picks.get("picks", []):
                e = elements.get(p["element"], {})
                s = stats_by_id.get(p["element"], {})
                pts = s.get("total_points")
                rows.append({
                    "slot": p["position"],
                    "web_name": e.get("web_name", p["element"]),
                    "team": teams.get(e.get("team"), ""),
                    "pos": POS.get(e.get("element_type"), ""),
                    "multiplier": p["multiplier"],
                    "captain": p["is_captain"],
                    "vice": p["is_vice_captain"],
                    "minutes": s.get("minutes"),
                    "raw_points": pts,
                    "effective_points": (pts * p["multiplier"]) if pts is not None else None,
                    "bps": s.get("bps"),
                    "bonus": s.get("bonus"),
                })
            write_csv(rows, list(rows[0].keys()), "entry", "picks", f"gw{gw}.csv")

    write_json({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entry_id": ENTRY_ID,
        "current_event": current,
        "next_event": nxt,
        "finished_events": finished,
    }, "snapshot.json")
    print("ferdig")


if __name__ == "__main__":
    main()
