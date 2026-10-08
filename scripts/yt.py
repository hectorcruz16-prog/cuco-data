"""Guarda los videos del canal oficial de la NBA en YouTube (el RSS solo trae los últimos 15).
Corre cada ~10 min y acumula 10 días en data/yt.json para que la app tenga los resúmenes de cada juego."""
import json, os, re
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
    x = requests.get(FEED, headers=UA, timeout=30).text
    fresh = []
    for e in x.split("<entry>")[1:]:
        vid = re.search(r"<yt:videoId>([^<]+)<", e)
        t = re.search(r"<title>([^<]+)<", e)
        at = re.search(r"<published>([^<]+)<", e)
        if vid and t:
            fresh.append({"id": vid.group(1), "t": unesc(t.group(1)), "at": at.group(1) if at else ""})
    have = {v["id"] for v in old}
    add = [v for v in fresh if v["id"] not in have]
    cut = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    items = sorted(add + old, key=lambda v: v["at"], reverse=True)
    items = [v for v in items if v["at"] >= cut][:800]
    print(f"leídos {len(fresh)}, nuevos {len(add)}, total {len(items)}")
    if add or not os.path.exists(OUT):
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "items": items}, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
