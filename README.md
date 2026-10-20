# Teploty živě – Praha · Frýdlant · Kitzbühel

Stránka: `pocasi/index.html` (GitHub Pages → `https://zededez.github.io/test-se-Zdendou/pocasi/`)

| Lokalita | Zdroj | Jak často se mění |
|---|---|---|
| Praha 6 – Silicon Hill (Strahov) | https://weather.sh.cvut.cz/weather/ | ~1 min |
| Frýdlant v Čechách (Předměstí) | https://www.pocasi-frydlant.cz/frydlant.php | ~5 min |
| Kitzbühel (TAWES 11279) + Hahnenkamm (11135) | GeoSphere Austria Data Hub API | 10 min |

## Jak to funguje
- Praha a Frýdlant nepovolují čtení z prohlížeče (CORS) → `.github/workflows/pocasi.yml` spouští `pocasi/scraper.py`,
  který stahuje 1× za minutu a ukládá `data.json` do větve `data` (vždy jediný commit).
- GitHub cron reálně spouští jen jednou za 2–6 h, proto běh trvá ~5 h 40 min a na konci sám spustí další
  (řetěz přes `workflow_dispatch`); cron každých 30 min řetěz jen znovu nahodí, kdyby se přerušil.
- Kitzbühel čte stránka přímo z API GeoSphere.
- Stránka se obnovuje každých 60 s.

## Zapnutí
1. Settings → Pages → Source: *Deploy from a branch*, větev `main`, složka `/ (root)`.
2. Actions → *pocasi-scraper* → *Run workflow* (první běh; dál se řetězí sám).
