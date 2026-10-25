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
    "bls": "https://www.bls.gov/schedule/news_release/bls.ics",
}

def fetch(url):
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

class TextLines(HTMLParser):
    """Convert HTML to text while retaining table and heading boundaries."""
    BLOCKS = {"br", "p", "div", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.BLOCKS:
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag.lower() in self.BLOCKS:
            self.parts.append("\n")
    def handle_data(self, data):
        self.parts.append(data)

def plain(html):
    parser = TextLines()
    parser.feed(html)
    lines = []
    for line in "".join(parser.parts).replace("\xa0", " ").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)

def mk_event(day, name, market, detail, url, source, time_jst="", importance="最重要"):
    return {
        "date": day.isoformat(), "timeJst": time_jst, "market": market,
        "name": name, "detail": detail, "importance": importance,
        "confirmed": True, "status": "公式日程", "sourceName": source, "sourceUrl": url
    }

def parse_fomc(html):
    """Use only the Fed's published FOMC meeting date pairs.

    The page also contains dates for minutes, press conferences and other
    calendar items. A generic month/date-range scan can misclassify those as
    meetings, so accept only the official scheduled meeting pairs below.
    Refresh this table when the Fed publishes a new year's schedule.
    """
    url = SOURCES["fed"]
    # Official Federal Reserve meeting calendar:
    # 2026: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    # 2027: same official calendar page.
    official_meetings = {
        2026: [(1, 27, 28), (3, 17, 18), (4, 28, 29), (6, 16, 17),
               (7, 28, 29), (9, 15, 16), (10, 27, 28), (12, 8, 9)],
        2027: [(1, 26, 27), (3, 16, 17), (4, 27, 28), (6, 8, 9),
               (7, 27, 28), (9, 14, 15), (10, 26, 27), (12, 7, 8)],
    }
    out = []
    for year in (TODAY.year, TODAY.year + 1):
        for month, start_day, end_day in official_meetings.get(year, []):
            for day_num, label in ((start_day, "1日目"), (end_day, "2日目")):
                try:
                    day = date(year, month, day_num)
                except ValueError:
                    continue
                if TODAY <= day <= END:
                    out.append(mk_event(
                        day, f"FOMC（米連邦公開市場委員会）{label}", "米国",
                        "米国金融政策会合。政策金利・声明・会見に注目。発表時刻は公式発表で確認。",
                        url, "Federal Reserve FOMC Calendar"
                    ))
    # Deduplicate dates defensively.
    unique = {}
    for event in out:
        unique[(event["date"], event["name"])] = event
    return list(unique.values())

def parse_bls(ics_text):
    """Read BLS's official iCalendar feed instead of its HTML page (which can 403)."""
    url = SOURCES["bls"]
    # Unfold RFC 5545 continuation lines before parsing VEVENT properties.
    text = re.sub(r"\r?\n[ \t]", "", ics_text)
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S | re.I)
    out = []
    for block in blocks:
        sm = re.search(r"(?im)^SUMMARY(?:;[^:]*)?:(.*)$", block)
        dm = re.search(r"(?im)^DTSTART(?:;TZID=([^:;]+))?(?:;[^:]*)?:(\d{8}(?:T\d{6}Z?)?)", block)
        if not sm or not dm:
            continue
        title = sm.group(1).strip().replace(r"\,", ",").replace(r"\;", ";")
        low = title.lower()
        if not any(k in low for k in ["employment situation", "consumer price index", "producer price index"]):
            continue
        raw = dm.group(2)
        try:
            if len(raw) == 8:
                dt = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=ET)
            elif raw.endswith("Z"):
                dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
            else:
                dt = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(dm.group(1) or "America/New_York"))
            dt_jst = dt.astimezone(JST)
            if not (TODAY <= dt_jst.date() <= END):
                continue
            name = ("米国雇用統計" if "employment situation" in low
                    else "米国CPI（消費者物価指数）" if "consumer price index" in low
                    else "米国PPI（生産者物価指数）")
            out.append(mk_event(dt_jst.date(), name, "米国",
                f"{title}。米金利・ドル円・株式市場の反応に注目。",
                url, "U.S. Bureau of Labor Statistics（公式iCalendar）", dt_jst.strftime("%H:%M")))
        except (ValueError, KeyError):
            continue
    return out

def parse_boj(html):
    url = SOURCES["boj"]
    text = plain(html)
    out = []
    # The Bank of Japan's release schedule is updated regularly. Parse date-led rows
    # conservatively; no event is added unless a recognizable official date/title exists.
    month_names = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    pat = re.compile(rf"(?i)\b({month_names})\.?\s+(\d{{1,2}})\s+(?:\([^)]*\)\s*)?([^\n]{{0,180}})")
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
    # All-or-nothing: if any source is unavailable, do not save a partial refresh.
    # This prevents one failed source from accidentally deleting its upcoming events.
    if errors:
        raise SystemExit("One or more official sources failed; leaving events.json unchanged: " + "; ".join(errors))
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

if __name__ == "__main__":
    main()
