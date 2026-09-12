#!/bin/bash
# Daglig xgstat-hoesting fra Steins egen maskin.
#
# GitHub Actions kan ikke gjoere dette: Vercel Attack Challenge Mode svarer 429
# med X-Vercel-Mitigated: challenge paa runner-IPer. Verifisert 12.09.2026.
# Denne maskinen slipper gjennom uten utfordring.
#
# Startes av launchd, se ~/Library/LaunchAgents/com.steinim.xgstat.plist
# Logg: ~/src/fpl-data/.xgstat-local.log

set -uo pipefail

REPO="$HOME/src/fpl-data"
LOG="$REPO/.xgstat-local.log"
exec >>"$LOG" 2>&1

echo "=== $(date -u '+%Y-%m-%d %H:%M:%S UTC') ==="
cd "$REPO" || { echo "fant ikke $REPO"; exit 1; }

# To separate spoersmaal, og de maa ikke blandes.
#
# 1) Har maskinen nett i det hele tatt? launchd kan fyre foer wifi er oppe
#    etter oppvaakning. Da hopper vi over dagen uten aa braake.
if ! curl -sf -o /dev/null --max-time 10 https://github.com; then
  echo "ikke nett -- hopper over denne kjoeringen"
  exit 0
fi

# 2) Slipper HOESTEREN gjennom? Sjekken maa bruke noeyaktig samme klient som
#    jobben, ellers tester den noe annet enn det den later som.
#    Rettet 12.09.2026: her sto det en `curl -sf` mot robots.txt. Vercel
#    utfordrer curl-UA-en selv fra denne maskinen, saa sjekken meldte «ikke
#    naabar» om en kilde som fungerer utmerket via skriptets egne headere.
if ! python3 scripts/fetch_xgstat.py --diag; then
  echo "xgstat avviser hoesteren -- se utskriften over. Ingen filer roert."
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "arbeidstreet er skittent -- avbryter, rydd manuelt"
  git status --short
  exit 1
fi

git pull --rebase --quiet origin main || { echo "pull feilet"; exit 1; }

if ! python3 scripts/fetch_xgstat.py; then
  echo "hoesting feilet -- ingen filer skrevet, ingenting committet"
  exit 1
fi

python3 scripts/reconcile_xgstat.py || echo "(avstemming gikk ikke gjennom -- se over)"

git add data/xgstat_fpl.csv data/xgstat_players.csv
if git diff --staged --quiet; then
  echo "ingen endringer"
  exit 0
fi

git commit -qm "xgstat $(date -u '+%Y-%m-%d %H:%M UTC')"
for i in 1 2 3; do
  if git pull --rebase --quiet --autostash origin main && git push --quiet; then
    echo "pushet"
    exit 0
  fi
  echo "push forsok $i feilet"
  sleep 5
done
echo "push feilet etter 3 forsok -- commiten ligger lokalt"
exit 1
