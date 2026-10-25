#!/usr/bin/env python3
"""Refresh the calendar's high-impact macro events from official schedules.

Earnings entries are preserved from events.json. Company earnings dates are not
scraped from IR pages yet; they must remain explicitly confirmed/unknown.
"""
import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "events.json"
JST = ZoneInfo("Asia/Tokyo")
ET = ZoneInfo("America/New_York")
TODAY = datetime.now(JST).date()
END = TODAY + timedelta(days=14)
UA = "Mozilla/5.0 (compatible; StockCalendarEvents/1.0)"

SOURCES = {
    "fed": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "boj": "https://www.boj.or.jp/en/about/calendar/index.htm",
    "bls": f"https://www.bls.gov/schedule/{TODAY.year}/",
}

def fetch(url):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def plain(html):
    html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</t[dh]>|</tr>|</p>|</h[1-6]>", "\n", html)
    return re.sub(r"(?s)<[^>]+>", " ", html).replace("&nbsp;", " ").replace("&#160;", " ")

def mk_event(day, name, market, detail, url, source, time_jst="", importance="最重要"):
    return {
        "date": day.isoformat(), "timeJst": time_jst, "market": market,
        "name": name, "detail": detail, "importance": importance,
        "confirmed": True, "status": "公式日程", "sourceName": source, "sourceUrl": url
    }

def parse_fomc(html):
    url = SOURCES["fed"]
    text = plain(html)
    # Official calendar lists month names followed by meeting date ranges.
    month_num = {m.lower(): i for i,m in enumerate(
        ["January","February","March","April","May","June","July","August","September","October","November","December"],1)}
    out = []
    # Limit parsing to 2026 section where the official page labels year headings.
    section = re.search(r"2026(.*?)(?:2025|2027|$)", text, re.S)
    if not section:
        section = re.search(r"2026(.*)", text, re.S)
    chunk = section.group(1) if section else text
    current_month = None
    for line in chunk.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        for m, num in month_num.items():
            if line.lower().startswith(m):
                current_month = num
                break
        if current_month and re.fullmatch(r"\d{1,2}\s*[-–]\s*\d{1,2}", line):
            nums = re.findall(r"\d+", line)
            start_day, end_day = int(nums[0]), int(nums[1])
            # FOMC meeting's final day is decision day; only surface the two meeting dates.
            try:
                for d, label in [(start_day, "1日目"), (end_day, "2日目")]:
                    day = date(TODAY.year, current_month, d)
                    if TODAY <= day <= END:
                        out.append(mk_event(day, f"FOMC（米連邦公開市場委員会）{label}", "米国",
                            "米国金融政策会合。政策金利・声明・会見に注目。発表時刻は公式発表で確認。",
                            url, "Federal Reserve FOMC Calendar"))
            except ValueError:
                pass
    return out

def parse_bls(html):
    url = SOURCES["bls"]
    text = re.sub(r"\s+", " ", plain(html))
    out = []
    # BLS page uses lines like: Friday, November 6, 2026 08:30 AM Employment Situation for October 2026
    pattern = re.compile(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday),\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2}),\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)\s+([^.;]+?)(?=(?:Monday|Tuesday|Wednesday|Thursday|Friday),\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)|$)"
    )
    months = {m.lower(): i for i,m in enumerate(
        ["January","February","March","April","May","June","July","August","September","October","November","December"],1)}
    for m in pattern.finditer(text):
        try:
            mo, d, y = months[m.group(2).lower()], int(m.group(3)), int(m.group(4))
            day = date(y, mo, d)
            title = re.sub(r"\s+", " ", m.group(8)).strip()
            if not (TODAY <= day <= END):
                continue
            if not any(k in title.lower() for k in ["employment situation", "consumer price index", "producer price index"]):
                continue
            hh, mm, ap = int(m.group(5)), int(m.group(6)), m.group(7)
            if ap == "PM" and hh != 12: hh += 12
            if ap == "AM" and hh == 12: hh = 0
            dt = datetime.combine(day, time(hh, mm), ET).astimezone(JST)
            jday = dt.date()
            label = ("米国雇用統計" if "employment situation" in title.lower()
                     else "米国CPI（消費者物価指数）" if "consumer price index" in title.lower()
                     else "米国PPI（生産者物価指数）")
            out.append(mk_event(jday, label, "米国",
                f"{title}。米金利・ドル円・株式市場の反応に注目。",
                url, "U.S. Bureau of Labor Statistics", dt.strftime("%H:%M")))
        except (ValueError, IndexError):
            continue
    return out

def parse_boj(html):
    url = SOURCES["boj"]
    text = plain(html)
    out = []
    # The Bank of Japan's release schedule is updated regularly. Parse date-led rows
    # conservatively; no event is added unless a recognizable official date/title exists.
    month_names = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    pat = re.compile(rf"(?i)\b({month_names})\.?\s+(\d{{1,2}})\s+(?:\([^)]*\)\s*)?([^\\n]{{0,180}})")
    month_map = {m.lower(): i for i,m in enumerate(
        ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"],1)}
    for m in pat.finditer(text):
        try:
            mo = month_map[m.group(1).lower()[:3]]
            day = date(TODAY.year, mo, int(m.group(2)))
            title = re.sub(r"\s+", " ", m.group(3)).strip(" :-|")
            if not TODAY <= day <= END:
                continue
            low = title.lower()
            if "statement on monetary policy" in low or "outlook for economic activity and prices" in low:
                name = "日銀金融政策決定会合・展望レポート"
                detail = "日銀の政策判断・経済物価見通しに注目。時刻は公式発表で確認。"
                out.append(mk_event(day, name, "日本", detail, url, "日本銀行 公表予定"))
            elif "services producer price index" in low:
                out.append(mk_event(day, "日銀・企業向けサービス価格指数", "日本",
                    "サービス価格の動向。国内インフレ圧力を確認。", url, "日本銀行 公表予定", "08:50", "高"))
        except (ValueError, KeyError):
            continue
    # Explicit meeting dates from BOJ's official 2026 schedule as fallback.
    if TODAY.year == 2026 and TODAY <= date(2026,10,29) <= END:
        if not any(e["date"] == "2026-10-29" and "金融政策決定会合" in e["name"] for e in out):
            out.append(mk_event(date(2026,10,29), "日銀金融政策決定会合（1日目）", "日本",
                "金融政策・金利見通しに注目。", "https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm",
                "日本銀行 会合日程"))
        if not any(e["date"] == "2026-10-30" and "金融政策決定会合" in e["name"] for e in out):
            out.append(mk_event(date(2026,10,30), "日銀金融政策決定会合・展望レポート", "日本",
                "政策決定と物価・経済見通しを確認。発表時刻は未定。",
                "https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm", "日本銀行 会合日程"))
    return out

def main():
    if not DATA.exists():
        raise SystemExit("events.json is missing; refusing to create an empty file.")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    errors = []
    parsed = []
    parsers = [("fed", parse_fomc), ("boj", parse_boj), ("bls", parse_bls)]
    for key, parser in parsers:
        try:
            html = fetch(SOURCES[key])
            events = parser(html)
            parsed.extend(events)
            print(f"{key}: parsed {len(events)} candidate events")
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            print(f"WARNING {key}: {exc}")
    # If all sources fail, keep existing data intact to avoid blanking the panel.
    if not parsed and errors:
        raise SystemExit("All official sources failed; leaving events.json unchanged.")
    # Preserve other manually maintained high-impact events, but replace known
    # source-generated events to prevent stale dates from lingering.
    generated_names = ("FOMC", "日銀金融政策決定会合", "日銀・企業向けサービス価格指数",
                       "米国雇用統計", "米国CPI", "米国PPI")
    preserved = [e for e in data.get("economicEvents", [])
                 if not any(n in e.get("name", "") for n in generated_names)]
    combined = preserved + parsed
    unique = {}
    for e in combined:
        key = (e.get("date"), e.get("name"))
        if key not in unique or e.get("importance") == "最重要":
            unique[key] = e
    data["economicEvents"] = sorted(unique.values(), key=lambda e: (e.get("date") or "9999", e.get("timeJst") or ""))
    # Earnings are deliberately preserved; never overwrite them with empty scraped results.
    data["updatedAt"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST") + "（公式スケジュール確認）"
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(data['economicEvents'])} events and preserved {len(data.get('earnings', []))} earnings entries.")
    if errors:
        print("Some sources failed; review warnings:", "; ".join(errors))

if __name__ == "__main__":
    main()
