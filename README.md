# fpl-data

Automatisk speil av FPL-API-et for sesongen 2026/27. Kjører som GitHub Action og
committer siste snapshot til `data/`. Git-historikken er versjonsarkivet.

## Oppsett

1. Opprett repoet som **public**. Rå-filene må være tilgjengelige uten auth.
2. Settings → Secrets and variables → Actions → Variables → New repository variable:
   `FPL_ENTRY_ID` = `1561629`
3. Settings → Actions → General → Workflow permissions → **Read and write permissions**.
4. Actions-fanen → FPL snapshot → Run workflow (verifiser at første kjøring går grønt).

## Filer

| Sti | Innhold |
|---|---|
| `data/players.csv` | Alle spillere: pris, eierandel, form, BPS, xG/xA, status, nyhetsfelt |
| `data/events.csv` | Runder, deadlines, snitt, høyeste score, chip-bruk |
| `data/fixtures.csv` | Alle kamper med resultat og FDR |
| `data/live/gwN.csv` | Poeng, BPS, bonus, DefCon per spiller for runde N |
| `data/entry/history.csv` | Rundepoeng, rank, overall rank, lagverdi, bank, hits |
| `data/entry/transfers.csv` | Alle bytter med kjøps- og salgspris |
| `data/entry/picks/gwN.csv` | Egen tropp runde N med multiplier og effektive poeng |
| `data/snapshot.json` | Tidspunkt for siste henting, current/next runde |

Full JSON ligger ved siden av: `bootstrap-static.json`, `fixtures.json`,
`live/gwN.json`, `entry/*.json`.

## Rå-URL-er

```
https://raw.githubusercontent.com/BRUKER/fpl-data/main/data/players.csv
https://raw.githubusercontent.com/BRUKER/fpl-data/main/data/entry/history.csv
https://raw.githubusercontent.com/BRUKER/fpl-data/main/data/entry/picks/gw2.csv
https://raw.githubusercontent.com/BRUKER/fpl-data/main/data/live/gw2.csv
```

Bytt `BRUKER` med GitHub-brukernavnet ditt. `raw.githubusercontent.com` cacher
i noen minutter — ikke sanntid under kamp, men presist til rundeoppgjør.

## Kjøreplan

Fire faste kjøringer daglig (01:45, 07:45, 13:45, 19:45 UTC) pluss fem ekstra
fredag–mandag under kampvinduet. Live-data for gjeldende runde overskrives ved
hver kjøring; ferdige runder hentes bare én gang.
