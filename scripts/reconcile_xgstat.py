#!/usr/bin/env python3
"""
Avstemmer xgstat mot FPL-APIet.

RUNDEPORTEN gaar paa `pts`, ikke paa minutter.

  Feil antakelse, rettet 12. september 2026: minutter kan ikke brukes til aa
  avgjoere om to kilder staar paa samme runde. xgstat teller faktisk spilletid
  inkludert tilleggstid (Wyscout), FPL kapper paa 90 per kamp. Forholdet ligger
  stabilt mellom 1.043 og 1.122 -- de blir aldri like, uansett hvor ferske de er.

  Riktig anker er et felt som FPL DEFINERER og xgstat bare gjengir: `pts`,
  `bonus`, `goals`. Er de like, staar filene paa samme runde.

Bruk:
  python3 scripts/reconcile_xgstat.py
  python3 scripts/reconcile_xgstat.py --force    # hopp over porten, kun feilsoek
"""

import csv
import statistics
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

GATE_FIELD = ("pts", "total_points")
GATE_MIN = 0.98

# Felt som begge kilder maaler selv, og som derfor IKKE kan brukes som anker.
# (xgstat-felt, FPL-felt, hvorfor de avviker)
INDEPENDENT = [
    ("mins", "minutes", "xgstat teller tilleggstid, FPL kapper paa 90/kamp"),
    ("assists", "assists", "FPL har egne assist-regler (retur, vunnet straffe)"),
]


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum())


def tokens(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return {t for t in "".join(c if c.isalnum() else " " for c in s).split() if t}


def read(path):
    if not path.exists():
        sys.exit(f"mangler {path.relative_to(ROOT)} -- kjoer hoestingen foerst")
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_index(fpl):
    by_name, by_tokens = {}, defaultdict(list)
    for r in fpl:
        team = (r.get("team_short") or "").upper()
        by_name.setdefault((team, norm(r.get("web_name"))), []).append(r)
        by_tokens[team].append((tokens(r.get("full_name")), r))
    return by_name, by_tokens


def match(xg_rows, fpl):
    """Tre trinn: eksakt web_name, slugtokens delmengde av full_name, to felles tokens."""
    by_name, by_tokens = build_index(fpl)
    pairs, ambiguous, unmatched = [], [], []
    for x in xg_rows:
        team = (x.get("team") or "").upper()
        hits = by_name.get((team, norm(x.get("name"))), [])
        how = "web_name"
        if len(hits) != 1:
            slug = tokens((x.get("slug") or "").replace("-", " "))
            cand = [r for toks, r in by_tokens.get(team, []) if slug and slug <= toks]
            if len(cand) != 1:
                cand = [r for toks, r in by_tokens.get(team, [])
                        if slug and len(slug & toks) >= 2]
            hits, how = cand, "slug"
        (pairs.append((x, hits[0], how)) if len(hits) == 1
         else ambiguous.append(x) if len(hits) > 1 else unmatched.append(x))
    return pairs, ambiguous, unmatched


def i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def agree(pairs, fx, ff):
    rows = [(x, f) for x, f, _ in pairs if i(x.get(fx)) is not None and i(f.get(ff)) is not None]
    eq = [(x, f) for x, f in rows if i(x[fx]) == i(f[ff])]
    return rows, eq


def main():
    force = "--force" in sys.argv[1:]
    xg, fpl = read(DATA / "xgstat_fpl.csv"), read(DATA / "players.csv")
    print(f"xgstat_fpl.csv: {len(xg)} rader    players.csv: {len(fpl)} rader\n")

    pairs, ambiguous, unmatched = match(xg, fpl)
    print(f"matchet:   {len(pairs)}  ({sum(1 for _,_,h in pairs if h=='web_name')} paa web_name, "
          f"{sum(1 for _,_,h in pairs if h=='slug')} paa slug)")
    print(f"tvetydig:  {len(ambiguous)}    umatchet: {len(unmatched)}")
    if unmatched[:5]:
        print("  umatchet f.eks. " + ", ".join(f"{u['name']} ({u['team']})" for u in unmatched[:5]))

    # ---- Rundeporten: felt FPL definerer og xgstat gjengir -----------------
    print("\nRUNDEPORTEN -- felt FPL definerer:")
    for fx, ff in (GATE_FIELD, ("bonus", "bonus"), ("goals", "goals_scored")):
        rows, eq = agree(pairs, fx, ff)
        flag = "  <-- port" if (fx, ff) == GATE_FIELD else ""
        print(f"  {fx:<8} vs {ff:<14} {len(eq)}/{len(rows)} ({len(eq)/len(rows):.1%}){flag}")

    rows, eq = agree(pairs, *GATE_FIELD)
    rate = len(eq) / len(rows) if rows else 0.0
    if rate < GATE_MIN and not force:
        print(f"\n❌ PORTEN ER STENGT ({rate:.1%} < {GATE_MIN:.0%}). Filene staar paa ulike runder.")
        for x, f in [(x, f) for x, f in rows if i(x[GATE_FIELD[0]]) != i(f[GATE_FIELD[1]])][:8]:
            print(f"    {f['web_name']:<16} FPL {f['total_points']:>4}  xgstat {x['pts']:>4}")
        return 1
    print(f"\n✅ Porten er aapen. Samme runde.")

    # ---- Felt som ikke kan sammenlignes -----------------------------------
    print("\nFelt begge kilder maaler selv -- sammenlign dem aldri:")
    for fx, ff, why in INDEPENDENT:
        rows_i, eq_i = agree(pairs, fx, ff)
        print(f"  {fx:<8} vs {ff:<10} {len(eq_i)}/{len(rows_i)} like ({len(eq_i)/len(rows_i):.1%}) -- {why}")
    ratio = sorted(i(x["mins"]) / i(f["minutes"]) for x, f, _ in pairs
                   if i(f.get("minutes")) and i(f["minutes"]) >= 180 and i(x.get("mins")))
    if ratio:
        print(f"  minuttforhold xgstat/FPL (>=180 min, n={len(ratio)}): "
              f"{ratio[0]:.3f} / {statistics.median(ratio):.3f} / {ratio[-1]:.3f}  (min/median/maks)")

    # ---- Kjente defekter i xgstats minuttkolonne --------------------------
    zero = [(x, f) for x, f, _ in pairs
            if i(x.get("mins")) == 0 and (i(f.get("minutes")) or 0) > 0]
    if zero:
        print(f"\n⚠️  {len(zero)} spillere har 0 minutter hos xgstat men minutter hos FPL:")
        for x, f in zero[:8]:
            print(f"    {f['web_name']:<16} {f['team_short']}  FPL {i(f['minutes']):>4} min, "
                  f"{f['goals_scored']} maal  -- xgstat 0")
        print("    xgstats per-spiller-rater (xGI/90 o.l.) er verdiloese for disse.")

    # ---- DefCon ------------------------------------------------------------
    aligned = [(x, f) for x, f in eq]
    d_rows = [(x, f) for x, f in aligned
              if i(x.get("defcon")) is not None and i(f.get("defensive_contribution")) is not None]
    if not d_rows:
        print("\ningen sammenlignbare defcon-verdier")
        return 0
    d_eq = [(x, f) for x, f in d_rows if i(x["defcon"]) == i(f["defensive_contribution"])]
    diffs = [i(x["defcon"]) - i(f["defensive_contribution"]) for x, f in d_rows]
    share = len(d_eq) / len(d_rows)
    print(f"\nDEFCON: {len(d_eq)}/{len(d_rows)} identiske ({share:.1%})  "
          f"snitt {sum(diffs)/len(diffs):+.2f}  spenn {min(diffs):+} til {max(diffs):+}")
    if share >= 0.98:
        print("✅ SAMME FELT. xgstats `defcon` er FPLs `defensive_contribution`.")
        print("   Kolonnen er redundant. Bruk players.csv, som er nivaa 0.")
    else:
        print("⚠️  EGEN DEFINISJON. Bruk aldri xgstats `defcon` til aa anslaa DefCon-poeng.")
        for x, f in sorted(d_rows, key=lambda p: -abs(i(p[0]["defcon"]) - i(p[1]["defensive_contribution"])))[:8]:
            print(f"    {f['web_name']:<16} FPL {i(f['defensive_contribution']):>4}  xgstat {i(x['defcon']):>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
