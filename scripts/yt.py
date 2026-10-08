"""Guarda los videos del canal oficial de la NBA en YouTube (el RSS solo trae los últimos 15).
Corre cada ~10 min y acumula 10 días en data/yt.json para que la app tenga los resúmenes de cada juego."""
import json, os, re, time
from datetime import datetime, timedelta, timezone
import requests

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "yt.json")
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=UCWJ2lWNubArHWmf3FIHbfcQ"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}


def unesc(t):
    return t.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")


def main():
    old = []
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT)).get("items", [])
        except Exception:
            old = []
    fresh, diag = [], []
    # 1) RSS (a veces YouTube responde 404/500: se reintenta)
    x = ""
    for i in range(4):
        try:
            r = requests.get(FEED, headers=UA, timeout=30)
            diag.append(f"rss {r.status_code} {len(r.text)}")
            if r.ok and "<entry>" in r.text:
                x = r.text
                break
        except Exception as e:
            diag.append(f"rss err {e}")
        time.sleep(3)
    for e in x.split("<entry>")[1:]:
        vid = re.search(r"<yt:videoId>([^<]+)<", e)
        t = re.search(r"<title>([^<]+)<", e)
        at = re.search(r"<published>([^<]+)<", e)
        if vid and t:
            fresh.append({"id": vid.group(1), "t": unesc(t.group(1)), "at": at.group(1) if at else ""})
    # 2) página del canal (los ~30 más nuevos) por si el RSS falla
    try:
        r = requests.get("https://www.youtube.com/@NBA/videos", headers={**UA, "Accept-Language": "en-US,en;q=0.9"}, cookies={"CONSENT": "YES+1"}, timeout=30)
        diag.append(f"page {r.status_code} {len(r.text)}")
        T = r.text
        now = datetime.now(timezone.utc)
        ids = {v["id"] for v in fresh}
        n_ok = 0
        for ch in T.split('"lockupViewModel":{')[1:]:
            vid = re.search(r'"contentId":"([\w-]{11})"', ch) or re.search(r'"videoId":"([\w-]{11})"', ch)
            tt = re.search(r'"lockupMetadataViewModel":\{"title":\{"content":"((?:[^"\\]|\\.)*)"', ch)
            ago = re.search(r'"(?:accessibilityLabel|content)":"(\d+) (second|minute|hour|day|week|month)s? ago"', ch)
            if not (vid and tt) or vid.group(1) in ids:
                continue
            unit = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000}[ago.group(2)] if ago else 0
            at = (now - timedelta(seconds=int(ago.group(1)) * unit)).isoformat(timespec="seconds") if ago else now.isoformat(timespec="seconds")
            fresh.append({"id": vid.group(1), "t": json.loads('"' + tt.group(1) + '"'), "at": at})
            ids.add(vid.group(1))
            n_ok += 1
        diag.append(f"page videos {n_ok}")
    except Exception as e:
        diag.append(f"page err {e}")
    have = {v["id"] for v in old}
    add = [v for v in fresh if v["id"] not in have]
    cut = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    items = sorted(add + old, key=lambda v: v["at"], reverse=True)
    items = [v for v in items if v["at"] >= cut][:800]
    print(f"leídos {len(fresh)}, nuevos {len(add)}, total {len(items)}", diag)
    if add or not os.path.exists(OUT) or not items:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "diag": diag, "items": items}, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
