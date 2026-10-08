"""Robot de datos de Cuco Fantasy.

Corre en GitHub Actions (gratis) y guarda data/experts.json con:
  sources: estado de cada fuente (ok, cuándo, cuántos)
  lists:   rankings [[nombre, puesto], ...] por fuente  -> la app los mezcla en el consenso de expertos
  proj:    proyección por juego de Hashtag Basketball {nombre: {pts, reb, ...}}
  last:    promedios reales de la temporada pasada (Basketball-Reference) {nombre: {gp, min, pts, ...}}

Si una fuente falla, se queda la última lectura buena del archivo anterior (con su fecha real).
"""
import json, os, re, sys, time, unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "experts.json")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
STARS = ["nikola jokic", "shai gilgeousalexander", "victor wembanyama", "luka doncic", "giannis antetokounmpo",
         "anthony davis", "tyrese haliburton", "karlanthony towns", "anthony edwards", "jayson tatum"]
NOW = datetime.now(timezone.utc)
# temporada NBA: de octubre en adelante es la nueva (2026-27 => "2027" en Basketball-Reference)
SEASON_END = NOW.year + 1 if NOW.month >= 9 else NOW.year


def nm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


DBG = {}


def log(*a):
    print(*a, flush=True)
    DBG.setdefault("log", []).append(" ".join(str(x) for x in a)[:400])


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=40, **kw)
    r.raise_for_status()
    return r


def valid_list(lst):
    if not lst or len(lst) < 100:
        return f"pocos jugadores ({len(lst or [])})"
    top = {nm(n).replace(" ", "") for n, r in lst if r <= 40}
    hits = sum(1 for s in STARS if s.replace(" ", "") in top)
    if hits < 4:
        return "las estrellas no aparecen arriba (lectura dudosa)"
    return None


def num(x):
    x = str(x).strip().replace(",", "")
    m = re.match(r"^-?(\d+(\.\d+)?|\.\d+)", x)
    return float(m.group(0)) if m else None


def clean_name(s):
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\(.*$", "", s)  # "Nikola Jokic (DEN - C)"
    s = re.sub(r"\s+[A-Z][a-zA-Z'.]*\.[A-Z][\w'.-]*(\s+(Jr|Sr|II|III|IV)\.?)?$", "", s)  # Hashtag: "Nikola Jokic N.Jokic"
    return s.strip()


# ------------------------------------------------------------------ ASP.NET (Hashtag)
def aspnet_max(url):
    """Hashtag es ASP.NET: el selector 'cuántos jugadores' hace un postback. Pedimos el máximo."""
    s = requests.Session()
    s.headers.update(UA)
    r = s.get(url, timeout=40)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    form = soup.find("form")
    if not form:
        return r.text
    DBG.setdefault("forms", {})[url] = {
        "selects": {sel.get("name"): {"opts": [((o.get("value") or "") + "=" + o.text.strip())[:30] for o in sel.find_all("option")][:15],
                                      "sel": (sel.find("option", selected=True) or {}).get("value") if sel.find("option", selected=True) else None} for sel in form.find_all("select")},
        "inputs": [(i.get("name"), i.get("type"), (i.get("value") or "")[:30]) for i in form.find_all("input") if not (i.get("name") or "").startswith("__")][:30],
        "postbacks": sorted(set(re.findall(r"__doPostBack\(&#39;([^&]+)&#39;|__doPostBack\('([^']+)'", r.text) and [a or b for a, b in re.findall(r"__doPostBack\(&#39;([^&]+)&#39;|__doPostBack\('([^']+)'", r.text)]))[:30],
        "text": [t for t in re.findall(r">([^<>]{3,60})<", r.text) if re.search(r"(?i)show|all players|more|page", t)][:20],
    }
    data = {}
    for inp in form.find_all("input"):
        if inp.get("name") and inp.get("type", "text") in ("hidden", "text"):
            data[inp["name"]] = inp.get("value", "")
        elif inp.get("name") and inp.get("type") == "checkbox" and inp.has_attr("checked"):
            data[inp["name"]] = inp.get("value") or "on"  # las categorías marcadas (sin esto el TOTAL sale 0)
    target = None
    for sel in form.find_all("select"):
        name = sel.get("name")
        opts = [(o.get("value") or o.text).strip() for o in sel.find_all("option")]
        chosen = sel.find("option", selected=True)
        data[name] = (chosen.get("value") if chosen else (opts[0] if opts else ""))
        nums = [o for o in opts if re.fullmatch(r"\d+", o)]
        # solo el selector de "cuántos jugadores" (DDSHOW); los demás se quedan como vienen
        if name and name.upper().endswith("SHOW") and nums and max(map(int, nums)) >= 200:
            best = max(nums, key=int)
            log(f"  selector {name}: {opts} -> {best}")
            data[name] = best
            target = name
    if not target:
        log("  no encontré selector de cantidad; uso la página tal cual")
        return r.text
    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = ""
    action = form.get("action") or url
    if action.startswith("./") or not action.startswith("http"):
        action = requests.compat.urljoin(url, action)
    r2 = s.post(action, data=data, timeout=60, headers={"Referer": url, "Content-Type": "application/x-www-form-urlencoded"})
    r2.raise_for_status()
    return r2.text


def parse_table(html, need=("PLAYER",)):
    """Devuelve filas como dicts usando los encabezados de la tabla más grande que tenga PLAYER."""
    soup = BeautifulSoup(html, "lxml")
    best = []
    DBG.setdefault("alltables", []).append([[c.get_text(" ", strip=True)[:20] for c in (t.find("tr") or t).find_all(["th", "td"])][:20] + [len(t.find_all("tr"))] for t in soup.find_all("table")][:8])
    for t in soup.find_all("table"):
        heads = None
        rows = []
        for tr in t.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not cells:
                continue
            up = [c.upper() for c in cells]
            if all(n in up for n in need):
                heads = up  # Hashtag repite el encabezado cada 10 filas
                continue
            if heads and len(cells) == len(heads):
                rows.append(dict(zip(heads, cells)))
        if len(rows) > len(best):
            best = rows
            DBG.setdefault("tables", []).append({"heads": heads, "n": len(rows), "sample": rows[:3]})
    return best


def hashtag_rankings():
    url = "https://hashtagbasketball.com/fantasy-basketball-rankings"
    rows = parse_table(aspnet_max(url))
    out = []
    for r in rows:
        rk = num(r.get("R#") or r.get("RANK") or r.get("#") or "")
        n = clean_name(r.get("PLAYER", ""))
        if rk and n and re.search(r"[A-Za-z]", n):
            out.append([n, int(rk)])
    return url, out, None


HH_CATS = {"PTS": "pts", "TREB": "reb", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk", "3PM": "tpm",
           "TO": "to", "FG%": "fgp", "FT%": "ftp", "MPG": "min", "GP": "gp"}


def hashtag_projections():
    url = "https://hashtagbasketball.com/fantasy-basketball-projections"
    rows = parse_table(aspnet_max(url))
    out, proj = [], {}
    for r in rows:
        rk = num(r.get("R#") or r.get("RANK") or "")
        n = clean_name(r.get("PLAYER", ""))
        if not (n and re.search(r"[A-Za-z]", n)):
            continue
        p = {}
        for h, k in HH_CATS.items():
            if h in r and k not in p:
                v = num(r[h])
                if v is not None:
                    p[k] = v
        if p:
            proj[n] = p
        if rk:
            out.append([n, int(rk)])
    return url, out, proj


# ------------------------------------------------------------------ FantasyPros
def fantasypros(url):
    html = get(url).text
    m = re.search(r"var\s+ecrData\s*=\s*(\{.*?\});", html, re.S)
    if m:
        d = json.loads(m.group(1))
        return [[p["player_name"], int(float(p["rank_ecr"]))] for p in d.get("players", []) if p.get("player_name") and p.get("rank_ecr")]
    out = []
    for r in parse_table(html, need=("PLAYER",)):
        rk = num(r.get("RANK") or r.get("#") or "")
        n = clean_name(re.sub(r"\s+(PG|SG|SF|PF|C|G|F)(,|\s|$).*$", "", r.get("PLAYER", "")))
        if rk and n:
            out.append([n, int(rk)])
    return out


def fp_rankings():
    url = "https://www.fantasypros.com/nba/rankings/overall.php"
    return url, fantasypros(url), None


def fp_adp():
    url = "https://www.fantasypros.com/nba/adp/overall.php"
    html = get(url).text
    out = []
    for r in parse_table(html, need=("PLAYER",)):
        rk = num(r.get("RANK") or r.get("#") or "")
        avg = num(r.get("AVG") or r.get("AVERAGE") or "")
        n = clean_name(re.sub(r"\s+(PG|SG|SF|PF|C|G|F)(,|\s|$).*$", "", r.get("PLAYER", "")))
        if n and (rk or avg):
            out.append([n, int(rk or round(avg))])
    if not out:
        out = fantasypros(url)
    return url, out, None


# ------------------------------------------------------------------ Basketball-Reference (temporada real)
def bbref(season_end):
    url = f"https://www.basketball-reference.com/leagues/NBA_{season_end}_per_game.html"
    r = get(url)
    r.encoding = "utf-8"
    html = r.text
    soup = BeautifulSoup(html, "lxml")
    t = soup.find("table", id="per_game_stats")
    if not t:
        raise RuntimeError("no encontré la tabla per_game_stats")
    out = {}
    for tr in t.find("tbody").find_all("tr"):
        if "thead" in (tr.get("class") or []):
            continue
        def g(k, tr=tr):
            c = tr.find(["td", "th"], {"data-stat": k})
            return c.get_text(strip=True) if c else ""
        n = g("name_display") or g("player")
        if not n:
            continue
        team = g("team_name_abbr") or g("team_id")
        # jugadores cambiados salen varias veces: la primera fila es el total (2TM/TOT)
        if n in out:
            continue
        row = {"team": team, "gp": num(g("games")), "min": num(g("mp_per_g")), "pts": num(g("pts_per_g")),
               "reb": num(g("trb_per_g")), "ast": num(g("ast_per_g")), "stl": num(g("stl_per_g")),
               "blk": num(g("blk_per_g")), "tpm": num(g("fg3_per_g")), "to": num(g("tov_per_g")),
               "fgp": num(g("fg_pct")), "ftp": num(g("ft_pct"))}
        out[n] = {k: v for k, v in row.items() if v not in (None, "")}
    return url, out


# ------------------------------------------------------------------ Basketball-Reference: avanzadas
def bbref_adv(season_end):
    url = f"https://www.basketball-reference.com/leagues/NBA_{season_end}_advanced.html"
    r = get(url)
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    t = soup.find("table", id="advanced") or soup.find("table", id="advanced_stats")
    if not t:
        raise RuntimeError("no encontré la tabla advanced")
    out = {}
    for tr in t.find("tbody").find_all("tr"):
        def g(k, tr=tr):
            c = tr.find(["td", "th"], {"data-stat": k})
            return c.get_text(strip=True) if c else ""
        n = g("name_display") or g("player")
        if not n or n in out:
            continue
        row = {"usg": num(g("usg_pct")), "per": num(g("per")), "ts": num(g("ts_pct")), "bpm": num(g("bpm")),
               "ws48": num(g("ws_per_48")), "mp": num(g("mp"))}
        out[n] = {k: v for k, v in row.items() if v is not None}
    return url, out


# ------------------------------------------------------------------ Depth charts
def probe(url, **kw):
    try:
        r = requests.get(url, headers=kw.get("h", UA), timeout=30)
        DBG.setdefault("probe", []).append({"url": url, "status": r.status_code, "len": len(r.text), "head": r.text[:300]})
        return r
    except Exception as e:
        DBG.setdefault("probe", []).append({"url": url, "error": str(e)[:200]})
        return None


def shape(o, d=0):
    if d > 5:
        return "…"
    if isinstance(o, dict):
        return {k: shape(v, d + 1) for k, v in list(o.items())[:12]}
    if isinstance(o, list):
        return [shape(o[0], d + 1), f"len={len(o)}"] if o else []
    return o if not isinstance(o, str) else o[:40]


def espn_depth():
    """Depth chart de ESPN por equipo: puesto 1 = titular en esa posición."""
    out = {}
    for tid in range(1, 31):
        try:
            r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{tid}/depthcharts", headers=UA, timeout=30)
            r.raise_for_status()
            j = r.json()
            ab = (j.get("team") or {}).get("abbreviation", "")
            for dc in j.get("depthchart") or []:
                for pos, blk in (dc.get("positions") or {}).items():
                    for i, a in enumerate(blk.get("athletes") or []):
                        n = a.get("displayName") or a.get("fullName")
                        if not n:
                            continue
                        cur = out.get(n)
                        if not cur or i + 1 < cur["rank"]:
                            out[n] = {"team": ab, "pos": pos.upper(), "rank": i + 1}
        except Exception as e:
            log(f"  depth equipo {tid}: {e}")
        time.sleep(0.3)
    return out


ESPN_SB = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


def espn_games(prev_games):
    """Box scores de ESPN de los últimos ~40 días (los juegos ya leídos se guardan y no se vuelven a pedir)."""
    from datetime import timedelta
    games = {g: v for g, v in (prev_games or {}).items() if v.get("d", "") >= (NOW - timedelta(days=60)).strftime("%Y%m%d")}
    new = 0
    for back in range(0, 40):
        day = (NOW - timedelta(days=back)).strftime("%Y%m%d")
        try:
            sb = requests.get(f"{ESPN_SB}/scoreboard", params={"dates": day}, headers=UA, timeout=30).json()
        except Exception as e:
            log(f"  scoreboard {day}: {e}")
            continue
        for ev in sb.get("events") or []:
            gid = str(ev.get("id"))
            st = ((ev.get("status") or {}).get("type") or {})
            if gid in games or not st.get("completed"):
                continue
            stype = ((ev.get("season") or {}).get("type"))
            try:
                sm = requests.get(f"{ESPN_SB}/summary", params={"event": gid}, headers=UA, timeout=30).json()
            except Exception as e:
                log(f"  summary {gid}: {e}")
                continue
            rows = []
            for tb in (sm.get("boxscore") or {}).get("players") or []:
                ab = (tb.get("team") or {}).get("abbreviation", "")
                for stt in tb.get("statistics") or []:
                    keys = [k.upper() for k in (stt.get("labels") or stt.get("names") or [])]
                    for a in stt.get("athletes") or []:
                        if a.get("didNotPlay") or not a.get("stats"):
                            continue
                        v = dict(zip(keys, a["stats"]))
                        def sp(x):
                            try:
                                m, t = str(x).split("-")
                                return int(m), int(t)
                            except Exception:
                                return 0, 0
                        fgm, fga = sp(v.get("FG"))
                        tpm, _ = sp(v.get("3PT"))
                        ftm, fta = sp(v.get("FT"))
                        rows.append([(a.get("athlete") or {}).get("displayName", ""), ab, 1 if a.get("starter") else 0,
                                     int(num(v.get("MIN")) or 0), int(num(v.get("PTS")) or 0), int(num(v.get("REB")) or 0),
                                     int(num(v.get("AST")) or 0), int(num(v.get("STL")) or 0), int(num(v.get("BLK")) or 0),
                                     tpm, int(num(v.get("TO")) or 0), fgm, fga, ftm, fta])
            if rows:
                games[gid] = {"d": day, "t": stype, "p": rows}
                new += 1
            time.sleep(0.25)
        time.sleep(0.2)
    log(f"  juegos guardados {len(games)} (nuevos {new})")
    return games


def last15(games):
    """Promedio de los últimos 15 juegos de cada jugador (temporada regular si ya hay; si no, pretemporada)."""
    reg = [g for g in games.values() if g.get("t") == 2]
    use, label = (reg, "temporada regular") if len(reg) >= 30 else ([g for g in games.values() if g.get("t") in (1, 2)], "pretemporada")
    by = {}
    for g in sorted(use, key=lambda x: x["d"], reverse=True):
        for r in g["p"]:
            lst = by.setdefault(r[0], [])
            if len(lst) < 15:
                lst.append(r)
    out = {}
    for n, rs in by.items():
        k = len(rs)
        S = lambda i: sum(x[i] for x in rs)
        fga, fta = S(12), S(14)
        out[n] = {"team": rs[0][1], "gp": k, "gs": S(2), "min": round(S(3) / k, 1), "pts": round(S(4) / k, 1), "reb": round(S(5) / k, 1),
                  "ast": round(S(6) / k, 1), "stl": round(S(7) / k, 1), "blk": round(S(8) / k, 1), "tpm": round(S(9) / k, 1),
                  "to": round(S(10) / k, 1), "fgp": round(S(11) / fga, 3) if fga else None, "ftp": round(S(13) / fta, 3) if fta else None}
        out[n] = {a: b for a, b in out[n].items() if b is not None}
    return label, out


# ------------------------------------------------------------------ main
def main():
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
        except Exception:
            prev = {}
    res = {"updated": NOW.isoformat(timespec="seconds"), "sources": {}, "lists": {}, "proj": {}, "last": {}, "cur": {}}
    at = NOW.isoformat(timespec="seconds")

    def keep_prev(k, err, name, url):
        ps = (prev.get("sources") or {}).get(k) or {}
        if k in (prev.get("lists") or {}):
            res["lists"][k] = prev["lists"][k]
        res["sources"][k] = {"name": name, "url": url, "ok": bool(ps.get("ok")) and k in res["lists"], "at": ps.get("at"),
                             "count": ps.get("count", 0), "fresh": False, "error": err}

    jobs = [
        ("hh", "Hashtag Basketball (ranking 9-cat temporada pasada)", hashtag_rankings),
        ("hhp", "Hashtag · proyecciones 2026-27 (top 30 gratis)", hashtag_projections),
        ("fp", "FantasyPros", fp_rankings),
        ("fpa", "FantasyPros · ADP (ESPN/Yahoo/CBS)", fp_adp),
    ]
    for k, name, fn in jobs:
        url = None
        try:
            log(f"[{k}] {name}")
            url, lst, proj = fn()
            log(f"  leídos {len(lst or [])}: {(lst or [])[:6]}")
            err = None if k == "hhp" else valid_list(lst)
            if err:
                raise RuntimeError(err)
            if k == "hhp":  # mismo ranking que "hh": solo se guardan las proyecciones por categoría
                if not proj or len(proj) < 25:  # gratis Hashtag solo enseña el top 30 proyectado
                    raise RuntimeError(f"pocas proyecciones ({len(proj or {})})")
                res["proj"] = proj
                res["sources"][k] = {"name": name, "url": url, "ok": True, "at": at, "count": len(proj), "fresh": True, "kind": "proj"}
                log(f"  ok: {len(proj)} proyecciones · ej: {next(iter(proj.items()))}")
                continue
            res["lists"][k] = lst
            res["sources"][k] = {"name": name, "url": url, "ok": True, "at": at, "count": len(lst), "fresh": True}
            log(f"  ok: {len(lst)} jugadores · top: {lst[:3]}")
        except Exception as e:
            log(f"  FALLÓ: {e}")
            if k == "hhp":
                ps = (prev.get("sources") or {}).get(k) or {}
                res["proj"] = prev.get("proj") or {}
                res["sources"][k] = {**ps, "name": name, "url": url or ps.get("url"), "ok": bool(res["proj"]), "fresh": False, "error": str(e)[:160], "kind": "proj"}
            else:
                keep_prev(k, str(e)[:160], name, url or (prev.get("sources", {}).get(k, {}).get("url")))
        time.sleep(2)

    # temporada pasada (base de minutos/juegos reales) + la actual si ya empezó
    for season, key in ((SEASON_END - 1, "last"), (SEASON_END, "cur")):
        try:
            log(f"[bbref] {season}")
            url, d = bbref(season)
            if len(d) < 50:
                raise RuntimeError(f"pocos jugadores ({len(d)})")
            res[key] = d
            res["sources"]["bb" + key] = {"name": f"Basketball-Reference {season - 1}-{str(season)[2:]}", "url": url, "ok": True,
                                         "at": at, "count": len(d), "fresh": True, "kind": "stats"}
            log(f"  ok: {len(d)} jugadores")
        except Exception as e:
            log(f"  FALLÓ: {e}")
            if prev.get(key):
                res[key] = prev[key]
                ps = prev.get("sources", {}).get("bb" + key, {})
                res["sources"]["bb" + key] = {**ps, "fresh": False, "error": str(e)[:160]}
        time.sleep(4)

    # juegos jugados de las últimas 3 temporadas (las viejas no cambian: se leen una vez y se guardan)
    gph_prev = prev.get("gph") or {}
    seasons_done = set((prev.get("sources", {}).get("gph", {}) or {}).get("seasons") or [])
    gph = {n: dict(v) for n, v in gph_prev.items()}
    got = set(seasons_done)
    for season in (SEASON_END - 3, SEASON_END - 2):
        if str(season) in seasons_done:
            continue
        try:
            log(f"[gph] {season}")
            _, d = bbref(season)
            for n, v in d.items():
                if v.get("gp") is not None:
                    gph.setdefault(n, {})[str(season)] = v["gp"]
            got.add(str(season))
            log(f"  ok: {len(d)}")
        except Exception as e:
            log(f"  FALLÓ: {e}")
        time.sleep(4)
    for n, v in (res.get("last") or {}).items():
        if v.get("gp") is not None:
            gph.setdefault(n, {})[str(SEASON_END - 1)] = v["gp"]
    if gph:
        res["gph"] = gph
        res["sources"]["gph"] = {"name": "Basketball-Reference · juegos jugados (3 temporadas)", "url": "https://www.basketball-reference.com/",
                                 "ok": True, "at": at, "count": len(gph), "fresh": True, "kind": "stats", "seasons": sorted(got)}

    # avanzadas temporada pasada (uso, PER, TS%)
    try:
        log(f"[adv] {SEASON_END - 1}")
        url, d = bbref_adv(SEASON_END - 1)
        if len(d) < 50:
            raise RuntimeError(f"pocos jugadores ({len(d)})")
        res["adv"] = d
        res["sources"]["adv"] = {"name": f"Basketball-Reference · avanzadas {SEASON_END - 2}-{str(SEASON_END - 1)[2:]}", "url": url,
                                 "ok": True, "at": at, "count": len(d), "fresh": True, "kind": "stats"}
        log(f"  ok: {len(d)} · ej: {next(iter(d.items()))}")
    except Exception as e:
        log(f"  FALLÓ: {e}")
        if prev.get("adv"):
            res["adv"] = prev["adv"]
            res["sources"]["adv"] = {**prev.get("sources", {}).get("adv", {}), "fresh": False, "error": str(e)[:160]}

    # últimos 15 juegos (box scores de ESPN; NBA.com bloquea a GitHub)
    try:
        log("[l15] ESPN box scores")
        gpath = os.path.join(os.path.dirname(OUT), "games.json")
        pg = json.load(open(gpath)) if os.path.exists(gpath) else {}
        games = espn_games(pg)
        json.dump(games, open(gpath, "w"), separators=(",", ":"))
        label, d = last15(games)
        if len(d) < 30:
            raise RuntimeError(f"pocos jugadores ({len(d)})")
        res["l15"] = d
        res["sources"]["l15"] = {"name": f"ESPN · últimos 15 juegos ({label})", "url": "https://www.espn.com/nba/scoreboard",
                                 "ok": True, "at": at, "count": len(d), "fresh": True, "kind": "stats", "pre": label == "pretemporada"}
        log(f"  ok: {label} {len(d)} · ej: {next(iter(d.items()))}")
    except Exception as e:
        log(f"  FALLÓ: {e}")
        if prev.get("l15"):
            res["l15"] = prev["l15"]
            res["sources"]["l15"] = {**prev.get("sources", {}).get("l15", {}), "fresh": False, "error": str(e)[:160]}

    # depth charts de ESPN
    try:
        log("[depth] ESPN")
        d = espn_depth()
        if len(d) < 150:
            raise RuntimeError(f"pocos jugadores ({len(d)})")
        res["depth"] = d
        res["sources"]["depth"] = {"name": "ESPN · depth charts (titulares)", "url": "https://www.espn.com/nba/depth",
                                   "ok": True, "at": at, "count": len(d), "fresh": True, "kind": "stats"}
        log(f"  ok: {len(d)} · titulares: {sum(1 for v in d.values() if v['rank'] == 1)} · ej: {list(d.items())[:3]}")
    except Exception as e:
        log(f"  FALLÓ: {e}")
        if prev.get("depth"):
            res["depth"] = prev["depth"]
            res["sources"]["depth"] = {**prev.get("sources", {}).get("depth", {}), "fresh": False, "error": str(e)[:160]}

    # recortar: solo jugadores que siguen activos (temporada pasada, depth chart, juegos recientes o rankings)
    alive = set(nm(n) for k in ("last", "cur", "depth", "l15") for n in (res.get(k) or {}))
    alive |= set(nm(n) for l in res["lists"].values() for n, _ in l)
    for k in ("gph", "adv"):
        if res.get(k):
            res[k] = {n: v for n, v in res[k].items() if nm(n) in alive}
    for v in (res.get("adv") or {}).values():
        v.pop("mp", None); v.pop("ws48", None)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(os.path.dirname(OUT), "debug.json"), "w") as f:
        json.dump(DBG, f, ensure_ascii=False, indent=1)
    oks = [k for k, s in res["sources"].items() if s.get("fresh")]
    log(f"\nlisto: {len(oks)} fuentes frescas: {oks}")
    if not oks and not prev:
        sys.exit(1)


if __name__ == "__main__":
    main()
