#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rebuilds index.html for the Otemachi Kitchen Car Finder by live-scraping:
  - Mellow SHOP STOP market pages (schedule.mellow.jp)
  - Tokyo Sankei Building's own food-truck page (metrosquare.jp)
  - Otemachi Kawabata Food Garden's vendor list (otemachi-foodgarden.com)

This is a best-effort scraper against three third-party sites with no public
API. Every extraction step has a fallback so one broken selector doesn't take
down the whole page -- worst case a location shows "no data" instead of
crashing the whole run. If Mellow/Sankei/Kawabata change their markup, the
regexes below are the first place to look.

Run: python scripts/scraper.py
Writes: index.html (repo root)
"""
import re
import sys
import json
import html
import datetime
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; OtemachiKitchenCarBot/1.0; +https://github.com/)"
TIMEOUT = 20
JST = datetime.timezone(datetime.timedelta(hours=9))

# ---------------------------------------------------------------------------
# Static metadata that doesn't change day to day (addresses, distances, and
# which live source feeds which location).
# ---------------------------------------------------------------------------

MELLOW_MARKETS = [
    {"name": "Otemachi Place", "addr": "2-3-1 \u014ctemachi, Chiyoda-ku, Tokyo",
     "dist": "~110 m", "url": "https://schedule.mellow.jp/ss_web/markets/G7TyQW", "maps": "35.6868348,139.7676822"},
    {"name": "TOKYO TORCH Park", "addr": "2-6-4 \u014ctemachi, Chiyoda-ku, Tokyo",
     "dist": "~330 m", "url": "https://schedule.mellow.jp/ss_web/markets/1362", "maps": "35.684055,139.770587"},
    {"name": "Otemachi Park Building (Ascott side)", "addr": "1-1-1 \u014ctemachi, Chiyoda-ku, Tokyo",
     "dist": "~390 m", "url": "https://schedule.mellow.jp/ss_web/markets/xVTjJY", "maps": "35.686639,139.763404"},
    {"name": "Otemachi One", "addr": "1-2-1 \u014ctemachi, Chiyoda-ku, Tokyo",
     "dist": "~430 m", "url": "https://schedule.mellow.jp/ss_web/markets/r8Tmnd", "maps": "35.68742,139.76185"},
    {"name": "Hotoria Square", "addr": "1-1-1 \u014ctemachi (Otemachi Park Building), Chiyoda-ku, Tokyo",
     "dist": "~490 m", "url": "https://schedule.mellow.jp/ss_web/markets/MmTd7k", "maps": "35.686607,139.762823"},
    {"name": "Otemachi Godo Chosha (Bldg 3)", "addr": "1-3-3 \u014ctemachi, Chiyoda-ku, Tokyo",
     "dist": "~570 m", "url": "https://schedule.mellow.jp/ss_web/markets/aPTpMb", "maps": "35.68959,139.762732"},
]

SANKEI = {
    "name": "Neo Yataimura (Tokyo Sankei Bldg)",
    "addr": "Tokyo Sankei Building, 1-7-2 \u014ctemachi, Chiyoda-ku, Tokyo",
    "dist": "~150 m",
    "maps": "35.6869391,139.7659595",
    "url": "https://www.metrosquare.jp/foodtruck/index",
}

KAWABATA = {
    "name": "Otemachi Kawabata Food Garden",
    "addr": "\u014ctemachi Kawabata Promenade, 1-9 \u014ctemachi, Chiyoda-ku, Tokyo",
    "dist": "~290 m",
    "maps": "35.688493,139.7663448",
    "url": "https://otemachi-foodgarden.com/list",
    "note": "This site publishes a \u201cusual weekday lineup\u201d rather than confirming exact "
            "trucks per date, and most days have several more vendors beyond what's listed "
            "(\u4ed6\u591a\u6570).",
}

WEEKDAY_JP = {"\u6708": 0, "\u706b": 1, "\u6c34": 2, "\u6728": 3, "\u91d1": 4}  # Mon..Fri
WEEKDAY_JP_FULL = {0: "\u6708\u66dc\u65e5", 1: "\u706b\u66dc\u65e5", 2: "\u6c34\u66dc\u65e5",
                    3: "\u6728\u66dc\u65e5", 4: "\u91d1\u66dc\u65e5"}
SANKEI_DAY_CODE = {0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI"}

session = requests.Session()
session.headers.update({"User-Agent": UA})


def fetch(url, retries=2):
    last_err = None
    for _ in range(retries + 1):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5)
    print(f"[warn] failed to fetch {url}: {last_err}", file=sys.stderr)
    return None


def target_dates(n=5):
    """Today (JST) plus following weekdays, weekends skipped, n total."""
    d = datetime.datetime.now(JST).date()
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def fmt_date(d):
    return f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d.weekday()]} {d.month}/{d.day}"


# ---------------------------------------------------------------------------
# Mellow SHOP STOP market page parsing
# ---------------------------------------------------------------------------

ENTRY_RE = re.compile(
    r"\u6bce\u9031(?P<wd>[\u6708\u706b\u6c34\u6728\u91d1])\u66dc\u65e5"
    r"(?P<rest>.+?)"
    r"(?P<start>\d{1,2}:\d{2})\s*[\u301c~\uff5e]\s*(?P<end>\d{1,2}:\d{2})"
    r".*?\u6b21\u56de\u51fa\u5e97\s*(?P<mon>\d{1,2})\u6708(?P<day>\d{1,2})\u65e5",
    re.S,
)


def parse_mellow_market(url):
    """Returns list of dicts: weekday(0-4), shop_id, raw_text, next_date(date)."""
    htmltxt = fetch(url)
    if not htmltxt:
        return []
    soup = BeautifulSoup(htmltxt, "html.parser")
    entries = []
    seen_hrefs = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/ss_web/shops/([A-Za-z0-9]+)/?$", href)
        if not m:
            continue
        shop_id = m.group(1)
        text = a.get_text(" ", strip=True)
        em = ENTRY_RE.search(text)
        if not em:
            continue
        key = (shop_id, em.group("mon"), em.group("day"))
        if key in seen_hrefs:
            continue
        seen_hrefs.add(key)
        try:
            year = datetime.datetime.now(JST).year
            nd = datetime.date(year, int(em.group("mon")), int(em.group("day")))
            if nd < datetime.datetime.now(JST).date() - datetime.timedelta(days=3):
                nd = datetime.date(year + 1, int(em.group("mon")), int(em.group("day")))
        except ValueError:
            continue
        entries.append({
            "weekday": WEEKDAY_JP.get(em.group("wd")),
            "shop_id": shop_id,
            "shop_url": urljoin(url, f"/ss_web/shops/{shop_id}"),
            "raw": em.group("rest").strip(),
            "next_date": nd,
        })
    return entries


SHOP_CACHE = {}


def parse_mellow_shop(shop_url):
    """Fetch a shop's own page for canonical name / photo / bio.

    The bio (owner-written blurb) reliably sits on the single text line
    immediately before the fixed "add to favorites" CTA
    ("SHOP STOPアプリでお気に入りに追加する"), regardless of what's above it
    (an "SNS・HP" block with icon links, or "登録がありません" when the truck
    has no social links registered). Anchoring on that fixed CTA instead of
    on "登録がありません" is what makes this reliable for trucks with and
    without social links alike -- the earlier version only anchored on the
    latter, so any truck *with* a registered SNS link silently got no bio.
    """
    if shop_url in SHOP_CACHE:
        return SHOP_CACHE[shop_url]
    htmltxt = fetch(shop_url)
    info = {"name": None, "img": None, "bio": None}
    if htmltxt:
        soup = BeautifulSoup(htmltxt, "html.parser")
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            info["name"] = og_title["content"].split("|")[0].strip()
        og_img = soup.find("meta", attrs={"property": "og:image"})
        if og_img and og_img.get("content"):
            info["img"] = og_img["content"]

        lines = [ln for ln in soup.get_text("\n", strip=True).split("\n") if ln.strip()]
        CTA = "SHOP STOP\u30a2\u30d7\u30ea\u3067\u304a\u6c17\u306b\u5165\u308a\u306b\u8ffd\u52a0\u3059\u308b"
        for i, ln in enumerate(lines):
            if CTA in ln and i > 0:
                candidate = lines[i - 1].strip()
                # sanity checks: a real bio is reasonably long prose, not a
                # landmark line, a lone SNS icon caption, or a menu/price row
                if (candidate
                        and candidate != "\u767b\u9332\u304c\u3042\u308a\u307e\u305b\u3093"
                        and len(candidate) >= 15
                        and "\u5186" not in candidate[-3:]):
                    info["bio"] = candidate[:200]
                break
    SHOP_CACHE[shop_url] = info
    return info


def dish_from_raw(raw_text, canonical_name):
    """Market-page text is 'Name Dish'; strip the known name off the front."""
    s = raw_text.strip()
    if canonical_name and s.startswith(canonical_name):
        s = s[len(canonical_name):].strip()
    else:
        # fallback: assume first token(s) before 2+ spaces are the name
        parts = re.split(r"\s{2,}", s, maxsplit=1)
        s = parts[1].strip() if len(parts) > 1 else s
    return s or "Menu on-site"


# ---------------------------------------------------------------------------
# Tokyo Sankei Building (Neo Yataimura) parsing
# ---------------------------------------------------------------------------

def parse_sankei(url, dates):
    """metrosquare.jp publishes the current Mon-Fri only; we map by weekday."""
    htmltxt = fetch(url)
    if not htmltxt:
        return {}, {}
    soup = BeautifulSoup(htmltxt, "html.parser")
    text = soup.get_text("\n", strip=True)

    day_positions = [(m.start(), m.group(1)) for m in
                      re.finditer(r"\d{2}/\d{2}\s+(MON|TUE|WED|THU|FRI)", text)]
    day_positions.sort()
    blocks = {}
    for i, (pos, code) in enumerate(day_positions):
        end = day_positions[i + 1][0] if i + 1 < len(day_positions) else len(text)
        blocks[code] = text[pos:end]

    trucks_by_date = {}
    truck_info = {}
    for d in dates:
        code = SANKEI_DAY_CODE.get(d.weekday())
        if code is None or code not in blocks:
            continue
        block = blocks[code]
        for img in soup.find_all("img", src=re.compile("car_photo_thum")):
            alt = (img.get("alt") or "").strip()
            if not alt or alt not in block:
                continue
            pos = block.find(alt)
            snippet = block[pos:pos + 600]
            mm = re.search(r"\u3010\u30e1\u30cb\u30e5\u30fc\uff1a(.+?)\u3011(.*?)(?=$|\n\n)",
                            snippet, re.S)
            dish = mm.group(1).split("/")[0].strip() if mm else "Menu on-site"
            trucks_by_date.setdefault(d, []).append(alt)
            if alt not in truck_info:
                truck_info[alt] = {
                    "name": alt, "dish": dish,
                    "desc": "Tokyo Sankei Building kitchen car.",
                    "img": urljoin(url, img["src"]),
                    "link": f"{url}#{code}",
                }
    return trucks_by_date, truck_info


# ---------------------------------------------------------------------------
# Otemachi Kawabata Food Garden parsing (weekday pattern, not date-specific)
# ---------------------------------------------------------------------------

DAY_MARKERS = [
    (0, "\u6708\u66dc\u65e5", "monday"),
    (1, "\u706b\u66dc\u65e5", "tuesday"),
    (2, "\u6c34\u66dc\u65e5", "wednesday"),
    (3, "\u6728\u66dc\u65e5", "thursday"),
    (4, "\u91d1\u66dc\u65e5", "friday"),
]


def parse_kawabata(list_url, dates):
    """Walk the page in document order rather than regexing raw HTML, so a
    tag sitting between the kanji day-name and its English label (which
    broke the previous version -- it matched against str(soup), and any
    markup in between meant zero matches) doesn't break detection.
    """
    htmltxt = fetch(list_url)
    if not htmltxt:
        return {}, {}
    soup = BeautifulSoup(htmltxt, "html.parser")

    # 1) find every tag whose own (recursive) text is a short day-header
    #    like "\u6708\u66dc\u65e5monday", tolerating any nesting/whitespace in between.
    candidates = []
    for tag in soup.find_all(True):
        txt = tag.get_text(strip=True)
        if not txt or len(txt) > 60:
            continue
        for wd, kanji, eng in DAY_MARKERS:
            if kanji in txt and eng in txt.lower():
                candidates.append((tag, wd))
                break

    # 2) keep only the innermost match per header (drop wrapping containers
    #    that also "contain" the same short text via a matching descendant).
    cand_ids = {id(t) for t, _ in candidates}
    leaf_ids = {}
    for tag, wd in candidates:
        if not any(id(d) in cand_ids for d in tag.find_all(True)):
            leaf_ids[id(tag)] = wd

    # 3) single pass over the document: track "current weekday" as we cross
    #    a day-header leaf, and bucket any /list/NNN link seen after it.
    trucks_by_wd = {}
    truck_info = {}
    current_wd = None
    for tag in soup.find_all(True):
        if id(tag) in leaf_ids:
            current_wd = leaf_ids[id(tag)]
            continue
        if tag.name == "a" and tag.get("href") and re.search(r"/list/\d+/?$", tag["href"]):
            if current_wd is None:
                continue
            nm = tag.get_text(" ", strip=True)
            nm = re.split(r"\s{2,}", nm)[0].strip() or nm.strip()
            half = len(nm) // 2
            if half > 0 and nm[:half] == nm[half:]:  # name is duplicated in-link
                nm = nm[:half]
            if not nm:
                continue
            href = urljoin(list_url, tag["href"])
            bucket = trucks_by_wd.setdefault(current_wd, [])
            if nm not in bucket:
                bucket.append(nm)
            truck_info.setdefault(nm, {"name": nm, "link": href})

    if not trucks_by_wd:
        print("[warn] kawabata: found 0 day sections -- page layout may have changed", file=sys.stderr)

    trucks_by_date = {}
    for d in dates:
        if d.weekday() in trucks_by_wd:
            trucks_by_date[d] = trucks_by_wd[d.weekday()]
    return trucks_by_date, truck_info


def enrich_kawabata_truck(name, info):
    """Best-effort fetch of an individual truck's own page for dish/photo."""
    link = info.get("link")
    if not link:
        return info
    htmltxt = fetch(link)
    if not htmltxt:
        return info
    soup = BeautifulSoup(htmltxt, "html.parser")
    h1 = soup.find("h1")
    if h1:
        info["name"] = h1.get_text(strip=True) or name
    # Description is intentionally a fixed line, not scraped free text --
    # see the note on parse_mellow_shop for why.
    info["desc"] = "Otemachi Kawabata Food Garden kitchen car."
    prices = soup.find_all(string=re.compile("\u5186\uff08\u7a0e\u8fbc\uff09"))
    dish = None
    if prices:
        prev = prices[0].find_previous(string=True)
        # walk backward for the first non-empty, non-price text node
        node = prices[0]
        for _ in range(6):
            node = node.find_previous(string=True)
            if node and node.strip() and "\u5186" not in node:
                dish = node.strip()
                break
    info["dish"] = dish or "Menu on-site"
    og_img = soup.find("meta", attrs={"property": "og:image"})
    info["img"] = og_img["content"] if og_img and og_img.get("content") else None
    return info



# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def slugify(name):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "x"


def build():
    dates = target_dates(5)
    date_labels = [fmt_date(d) for d in dates]
    print(f"[info] target dates: {date_labels}", file=sys.stderr)

    locations = []
    trucks = {}  # canonical_name -> {desc, dish, link, img}

    def register_truck(name, desc, dish, link, img=None):
        if not name:
            return None
        if name not in trucks:
            trucks[name] = {"desc": desc or "Otemachi-area kitchen car",
                             "dish": dish or "Menu on-site", "link": link, "img": img}
        else:
            # fill in any gaps from a fuller record
            t = trucks[name]
            t["desc"] = t["desc"] or desc
            t["dish"] = t["dish"] or dish
            t["img"] = t["img"] or img
            t["link"] = t["link"] or link
        return name

    # --- Mellow markets ---
    for market in MELLOW_MARKETS:
        entries = parse_mellow_market(market["url"])
        sched = {label: [] for label in date_labels}
        for e in entries:
            for d, label in zip(dates, date_labels):
                if e["next_date"] == d:
                    info = parse_mellow_shop(e["shop_url"])
                    name = info["name"] or e["raw"][:24]
                    dish = dish_from_raw(e["raw"], info["name"])
                    register_truck(name, info.get("bio") or "Mellow SHOP STOP kitchen car.",
                                    dish, e["shop_url"], info.get("img"))
                    sched[label].append(name)
        note = None
        if not entries:
            note = "No advance calendar currently published on Mellow for this spot."
        locations.append({"name": market["name"], "addr": market["addr"],
                           "dist": market["dist"], "maps": market.get("maps"),
                           "note": note, "sched": sched})

    # --- Sankei Building ---
    sankei_by_date, sankei_info = parse_sankei(SANKEI["url"], dates)
    sched = {label: [] for label in date_labels}
    got_any = False
    for d, label in zip(dates, date_labels):
        names = sankei_by_date.get(d)
        if names:
            got_any = True
            for nm in names:
                info = sankei_info[nm]
                register_truck(nm, info["desc"], info["dish"], info["link"], info["img"])
                sched[label].append(nm)
        else:
            sched[label] = "NP"
    locations.insert(1, {
        "name": SANKEI["name"], "addr": SANKEI["addr"], "dist": SANKEI["dist"], "maps": SANKEI.get("maps"),
        "note": None if got_any else "metrosquare.jp did not return a parseable weekly schedule this run.",
        "sched": sched,
    })

    # --- Kawabata Food Garden ---
    kawa_by_date, kawa_info = parse_kawabata(KAWABATA["url"], dates)
    sched = {label: [] for label in date_labels}
    for d, label in zip(dates, date_labels):
        names = kawa_by_date.get(d, [])[:7]  # cap so the cell stays readable
        for nm in names:
            info = kawa_info.get(nm, {"link": None})
            if "desc" not in info:
                info = enrich_kawabata_truck(nm, dict(info))
                kawa_info[nm] = info
            register_truck(info.get("name", nm), info.get("desc"), info.get("dish"),
                            info.get("link"), info.get("img"))
            sched[label].append(info.get("name", nm))
    locations.insert(2, {
        "name": KAWABATA["name"], "addr": KAWABATA["addr"], "dist": KAWABATA["dist"], "maps": KAWABATA.get("maps"),
        "note": KAWABATA["note"], "sched": sched,
    })

    return date_labels, locations, trucks


# ---------------------------------------------------------------------------
# HTML rendering (same look as the hand-built version)
# ---------------------------------------------------------------------------

def esc(s):
    return html.escape(s or "", quote=True)


def render(date_labels, locations, trucks):
    ids, used = {}, set()
    for name in trucks:
        base = "truck-" + slugify(name)[:40]
        cand, i = base, 2
        while cand in used:
            cand = f"{base}-{i}"
            i += 1
        used.add(cand)
        ids[name] = cand

    def cell_html(v):
        if v == "NP":
            return '<span class="empty">not yet posted</span>'
        if not v:
            return '<span class="empty">nothing listed</span>'
        return "<br>".join(f'<a class="truck-link" href="javascript:void(0)" data-target="{ids[t]}">{esc(t)}</a>'
                            for t in v if t in ids)

    thead = "<tr><th class=\"loc-col\">Location</th>" + "".join(f"<th>{esc(d)}</th>" for d in date_labels) + "</tr>"
    rows = []
    for loc in locations:
        maps_url = None
        if loc.get("maps"):
            maps_url = f'https://www.google.com/maps/search/?api=1&query={loc["maps"]}'
        name_html = (f'<a class="loc-map-link" href="{esc(maps_url)}" target="_blank" rel="noopener">{esc(loc["name"])}</a>'
                     if maps_url else esc(loc["name"]))
        tds = (f'<td class="loc-col"><span class="loc-name">{name_html}</span>'
               f'<span class="loc-meta">{esc(loc["addr"])} &middot; {esc(loc["dist"])}</span>')
        if loc.get("note"):
            tds += f'<span class="loc-note">{esc(loc["note"])}</span>'
        tds += "</td>"
        for d in date_labels:
            tds += f'<td>{cell_html(loc["sched"].get(d))}</td>'
        rows.append(f"<tr>{tds}</tr>")
    table1 = f'<table class="schedule"><thead>{thead}</thead><tbody>{"".join(rows)}</tbody></table>'

    truck_rows = []
    for name in sorted(trucks.keys(), key=lambda n: ids[n]):
        t = trucks[name]
        tid = ids[name]
        img_html = (f'<img class="truck-photo" src="{esc(t["img"])}" alt="{esc(name)}" loading="lazy">'
                    if t.get("img") else '<div class="truck-photo placeholder" aria-hidden="true"></div>')
        link_html = (f'<a class="ext-link" href="{esc(t["link"])}" target="_blank" rel="noopener">View truck &#8599;</a>'
                     if t.get("link") else '<span class="empty">no link</span>')
        truck_rows.append(f'''
    <tr id="{tid}">
      <td class="photo-cell">{img_html}</td>
      <td class="name-cell"><span class="tname">{esc(name)}</span></td>
      <td>{esc(t["desc"])}</td>
      <td>{esc(t["dish"])}</td>
      <td>{link_html}</td>
    </tr>''')
    table2 = (f'<table class="directory"><thead><tr><th></th><th>Truck</th><th>Description</th>'
              f'<th>Popular dish</th><th>Link</th></tr></thead><tbody>{"".join(truck_rows)}</tbody></table>')

    updated = datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Otemachi Kitchen Car Finder</title>
<style>
  :root {{
    --paper: #f7f3ea; --ink: #2b241b; --rust: #b5502e; --olive: #5c6b47;
    --line: #d8cdb8; --muted: #8a7f68; --card: #fffdf7;
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{ margin: 0; background: var(--paper); color: var(--ink);
    font-family: "Iowan Old Style","Georgia","Hiragino Mincho ProN", serif; line-height: 1.5; }}
  header {{ padding: 48px 6vw 28px; border-bottom: 3px solid var(--ink); }}
  .kicker {{ font-family: "Helvetica Neue", Arial, sans-serif; font-size: 13px;
    letter-spacing: 0.04em; color: var(--rust); margin: 0 0 10px; }}
  h1 {{ font-size: clamp(32px, 5vw, 54px); margin: 0 0 12px; font-weight: 600; line-height: 1.05; }}
  .sub {{ font-family: "Helvetica Neue", Arial, sans-serif; max-width: 620px; color: var(--muted); font-size: 15.5px; }}
  .updated {{ font-family: "Helvetica Neue", Arial, sans-serif; font-size: 12px; color: var(--muted); margin-top: 14px; }}
  nav.jump {{ font-family: "Helvetica Neue", Arial, sans-serif; font-size: 13.5px; margin-top: 20px; }}
  nav.jump a {{ color: var(--ink); text-decoration: none; border-bottom: 1px solid var(--rust); margin-right: 22px; padding-bottom: 2px; }}
  section {{ padding: 40px 6vw; }}
  section#trucks {{ background: var(--card); border-top: 3px solid var(--ink); }}
  h2 {{ font-family: "Helvetica Neue", Arial, sans-serif; font-weight: 700; font-size: 22px; margin: 0 0 6px; }}
  .section-note {{ font-family: "Helvetica Neue", Arial, sans-serif; color: var(--muted); font-size: 14px; max-width: 640px; margin: 0 0 26px; }}
  table {{ border-collapse: collapse; width: 100%; font-family: "Helvetica Neue", Arial, sans-serif; }}
  .schedule {{ font-size: 14px; }}
  .schedule th {{ text-align: left; font-weight: 700; font-size: 12.5px; letter-spacing: 0.02em;
    padding: 10px 12px; border-bottom: 2px solid var(--ink); white-space: nowrap; }}
  .schedule td {{ padding: 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  .loc-col {{ width: 240px; min-width: 200px; }}
  .loc-name {{ display:block; font-weight: 700; font-size: 14.5px; margin-bottom: 3px; }}
  a.loc-map-link {{ color: var(--ink); text-decoration: none; border-bottom: 1px solid var(--line); }}
  a.loc-map-link:hover {{ border-bottom-color: var(--rust); color: var(--rust); }}
  .loc-meta {{ display:block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
  .loc-note {{ display:block; color: var(--olive); font-size: 11.5px; font-style: italic; }}
  a.truck-link {{ color: var(--rust); text-decoration: none; border-bottom: 1px dotted var(--rust); }}
  a.truck-link:hover {{ border-bottom-style: solid; }}
  .empty {{ color: var(--muted); font-size: 13px; font-style: italic; }}
  .directory {{ font-size: 14px; }}
  .directory th {{ text-align: left; font-weight: 700; font-size: 12.5px; padding: 8px 10px; border-bottom: 2px solid var(--ink); }}
  .directory td {{ padding: 10px; border-bottom: 1px solid var(--line); vertical-align: middle; }}
  .directory tr:target, .directory tr.js-target {{ background: #fbe7c8; }}
  .photo-cell {{ width: 64px; }}
  .truck-photo {{ width: 56px; height: 56px; object-fit: cover; border-radius: 4px; display: block; border: 1px solid var(--line); }}
  .truck-photo.placeholder {{ background: var(--line); }}
  .name-cell {{ min-width: 150px; }}
  .tname {{ font-weight: 700; }}
  a.ext-link {{ color: var(--olive); text-decoration: none; font-size: 13px; white-space: nowrap; }}
  a.ext-link:hover {{ text-decoration: underline; }}
  footer {{ padding: 26px 6vw 50px; font-family: "Helvetica Neue", Arial, sans-serif; font-size: 12.5px; color: var(--muted); }}
  .table-scroll {{ overflow-x: auto; border: 1px solid var(--line); }}
  @media (max-width: 720px) {{ header {{ padding: 34px 5vw 22px; }} section {{ padding: 30px 5vw; }} }}
</style>
</head>
<body>

<header>
  <p class="kicker">URBANNET \u014cTEMACHI &middot; LUNCH FIELD GUIDE</p>
  <h1>Where the trucks are today</h1>
  <p class="sub">The eight closest kitchen&nbsp;car spots to Urbannet \u014ctemachi, with vendors for today and the next four weekdays. Tap any truck name to jump to its listing below.</p>
  <p class="updated">Last rebuilt {updated} &middot; refreshes automatically once a day</p>
  <nav class="jump"><a href="#schedule">Schedule</a><a href="#trucks">Truck directory</a></nav>
</header>

<section id="schedule">
  <h2>Locations &amp; schedule</h2>
  <p class="section-note">Ranked by distance from Urbannet \u014ctemachi. Pulled live from Mellow SHOP STOP, the Tokyo Sankei Building's own food-truck page, and Otemachi Kawabata Food Garden \u2014 still worth a same-day check, since trucks occasionally swap for weather or sell out early.</p>
  <div class="table-scroll">{table1}</div>
</section>

<section id="trucks">
  <h2>Truck directory</h2>
  <p class="section-note">Every truck referenced above, with a link out to its own page.</p>
  <div class="table-scroll">{table2}</div>
</section>

<footer>
  Auto-rebuilt daily from Mellow SHOP STOP (schedule.mellow.jp), Tokyo Sankei Building Shops &amp; Restaurants (metrosquare.jp), and Otemachi Kawabata Food Garden (otemachi-foodgarden.com). Kawabata Food Garden's row shows its published \u201cusual\u201d weekday lineup rather than date-confirmed vendors.
</footer>

<script>
  document.querySelectorAll('a.truck-link').forEach(function(a){{
    a.addEventListener('click', function(e){{
      e.preventDefault();
      var id = this.getAttribute('data-target');
      var el = document.getElementById(id);
      if (!el) return;
      document.querySelectorAll('tr.js-target').forEach(function(r){{ r.classList.remove('js-target'); }});
      el.classList.add('js-target');
      el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
      if (history.replaceState) {{ history.replaceState(null, '', '#' + id); }}
    }});
  }});
  window.addEventListener('DOMContentLoaded', function(){{
    if (location.hash) {{
      var el = document.getElementById(location.hash.slice(1));
      if (el) {{ el.classList.add('js-target'); setTimeout(function(){{ el.scrollIntoView({{behavior:'smooth', block:'center'}}); }}, 50); }}
    }}
  }});
</script>
</body>
</html>'''


def main():
    date_labels, locations, trucks = build()
    print(f"[info] {len(locations)} locations, {len(trucks)} trucks", file=sys.stderr)
    out = render(date_labels, locations, trucks)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(out)
    print("[info] wrote index.html", file=sys.stderr)


if __name__ == "__main__":
    main()
