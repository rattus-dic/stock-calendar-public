#!/usr/bin/env python3
"""Refresh the calendar's high-impact macro events from official schedules (90-day horizon).

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
END = TODAY + timedelta(days=90)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

SOURCES = {
    "fed": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "boj": "https://www.boj.or.jp/mopo/mpmsche_minu/",
    "bls": "https://www.bls.gov/schedule/news_release/bls.ics",
}

def fetch(url):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/calendar,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    })
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
    """Use only official published FOMC meeting date pairs.

    The full Fed page includes other dates; generic date-range parsing can
    mistakenly treat unrelated dates as meetings. Keep this list aligned with
    the Federal Reserve's official FOMC calendar.
    """
    url = SOURCES["fed"]
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
                if label == "2日目":
                    # FOMC statements are normally released at 14:00 US Eastern Time.
                    # Convert the actual release timestamp to JST, including DST.
                    release_jst = datetime.combine(day, time(14, 0), ET).astimezone(JST)
                    display_day = release_jst.date()
                    display_time = release_jst.strftime("%H:%M")
                    name = "FOMC政策金利・声明発表"
                    detail = "声明発表は通常、米東部時間14:00。記者会見は通常14:30（日本時間は季節により変動）。公式発表で最終確認。"
                else:
                    display_day = day
                    display_time = "時刻未定"
                    name = "FOMC（米連邦公開市場委員会）1日目"
                    detail = "米国金融政策会合の1日目。政策発表は通常2日目。"
                if TODAY <= display_day <= END:
                    out.append(mk_event(
                        display_day, name, "米国", detail,
                        url, "Federal Reserve FOMC Calendar", display_time
                    ))
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
                # All-day event: keep the published date and don't invent a time.
                event_day = datetime.strptime(raw, "%Y%m%d").date()
                event_time = "時刻未定"
            else:
                if raw.endswith("Z"):
                    dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
                else:
                    dt = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(dm.group(1) or "America/New_York"))
                dt_jst = dt.astimezone(JST)
                event_day = dt_jst.date()
                event_time = dt_jst.strftime("%H:%M")
            if not (TODAY <= event_day <= END):
                continue
            name = ("米国雇用統計" if "employment situation" in low
                    else "米国CPI（消費者物価指数）" if "consumer price index" in low
                    else "米国PPI（生産者物価指数）")
            out.append(mk_event(event_day, name, "米国",
                f"{title}。米金利・ドル円・株式市場の反応に注目。",
                url, "U.S. Bureau of Labor Statistics（公式iCalendar）", event_time))
        except (ValueError, KeyError):
            continue
    return out

class BLSAnnualScheduleParser(HTMLParser):
    """Extract Date / Time / Release cells from BLS official schedule tables."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_row = False
        self.in_cell = False
        self.cell_parts = []
        self.cells = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self.in_row = True
            self.cells = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.cell_parts = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self.in_cell:
            value = re.sub(r"\s+", " ", "".join(self.cell_parts)).strip()
            self.cells.append(value)
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.cells:
                self.rows.append(self.cells[:])
            self.in_row = False

    def handle_data(self, data):
        if self.in_cell:
            self.cell_parts.append(data)


def parse_bls_fallback():
    """Automatically parse BLS's official annual release schedule pages.

    Every run fetches the current and next calendar-year pages from bls.gov.
    Only Employment Situation, CPI and PPI are retained. BLS publishes times
    in Eastern Time, which are converted to Japan time for the calendar.
    """
    out = []
    seen = set()

    for year in (TODAY.year, TODAY.year + 1):
        schedule_url = f"https://www.bls.gov/schedule/{year}/home.htm"
        try:
            html = fetch(schedule_url)
        except Exception as exc:
            print(f"bls: official annual schedule {year} unavailable: {exc}")
            continue
        parser = BLSAnnualScheduleParser()
        parser.feed(html)

        for cells in parser.rows:
            if len(cells) < 3:
                continue
            date_text, time_text, title = cells[0], cells[1], cells[2]
            title_lower = title.lower()

            if "employment situation" in title_lower:
                name = "米国雇用統計"
            elif "consumer price index" in title_lower:
                name = "米国CPI（消費者物価指数）"
            elif "producer price index" in title_lower:
                name = "米国PPI（生産者物価指数）"
            else:
                continue

            date_match = re.search(
                r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
                r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",
                date_text,
                re.IGNORECASE,
            )
            time_match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", time_text, re.IGNORECASE)
            if not date_match or not time_match:
                continue

            month_text, day_text, year_text = date_match.groups()
            hour_text, minute_text, ampm = time_match.groups()
            try:
                month = datetime.strptime(month_text, "%B").month
                hour = int(hour_text) % 12
                if ampm.upper() == "PM":
                    hour += 12
                eastern_dt = datetime(
                    int(year_text), month, int(day_text), hour, int(minute_text), tzinfo=ET
                )
                release_jst = eastern_dt.astimezone(JST)
            except (ValueError, TypeError):
                continue

            event_day = release_jst.date()
            if not TODAY <= event_day <= END:
                continue

            unique_key = (event_day.isoformat(), name)
            if unique_key in seen:
                continue
            seen.add(unique_key)

            out.append(mk_event(
                event_day, name, "米国",
                f"{title}。BLS公式年間スケジュールから自動取得。米金利・ドル円・株式市場の反応に注目。",
                schedule_url,
                "U.S. Bureau of Labor Statistics（公式年間スケジュール・自動取得）",
                release_jst.strftime("%H:%M")
            ))

    if not out:
        raise ValueError(
            "BLS official annual schedule pages were fetched, but no Employment Situation/CPI/PPI "
            "releases were parsed in the 90-day window"
        )
    return sorted(out, key=lambda event: (event["date"], event.get("timeJst", "")))


class BOJMeetingTableParser(HTMLParser):
    """Extract year-labelled rows from the BOJ official meeting schedule."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.year = None
        self.in_heading = 0
        self.in_row = False
        self.in_cell = False
        self.cell_parts = []
        self.cells = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("h1", "h2", "h3", "h4"):
            self.in_heading += 1
        elif tag == "tr":
            self.in_row = True
            self.cells = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.cell_parts = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("h1", "h2", "h3", "h4") and self.in_heading:
            self.in_heading -= 1
        elif tag in ("td", "th") and self.in_cell:
            self.cells.append(re.sub(r"\s+", " ", "".join(self.cell_parts)).strip())
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.cells:
                self.rows.append((self.year, self.cells[:]))
            self.in_row = False

    def handle_data(self, data):
        value = data.strip()
        if self.in_cell:
            self.cell_parts.append(data)
        elif self.in_heading:
            match = re.search(r"\b(20\d{2})年\b", value)
            if match:
                self.year = int(match.group(1))
        elif not self.in_row:
            # Some page revisions put the year label in a caption or lead text.
            match = re.search(r"\b(20\d{2})年\b", value)
            if match:
                self.year = int(match.group(1))


def _boj_decision_day(text, year):
    """Return the last day of a BOJ meeting range such as 10月29日・30日."""
    month_match = re.search(r"(\d{1,2})月", text)
    day_matches = re.findall(r"(\d{1,2})日", text)
    if not month_match or not day_matches:
        return None
    month = int(month_match.group(1))
    day = int(day_matches[-1])
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _boj_outlook_day(text, year):
    """Parse an Outlook-report publication date, or return None for a dash."""
    if not text or text.strip() in ("-", "－", "—", "——"):
        return None
    month_match = re.search(r"(\d{1,2})月", text)
    day_match = re.search(r"(\d{1,2})日", text)
    if not month_match or not day_match:
        return None
    try:
        return date(year, int(month_match.group(1)), int(day_match.group(1)))
    except ValueError:
        return None


def parse_boj(html):
    """Read decision dates directly from the BOJ's official year-by-year table.

    Only the policy decision / Outlook Report day is added to the high-impact
    calendar. Other BOJ statistics and publication items are intentionally
    excluded. If the official table cannot be interpreted, raise an error so
    main() preserves the previously saved BOJ events.
    """
    url = SOURCES["boj"]
    parser = BOJMeetingTableParser()
    parser.feed(html)

    out = []
    for year, cells in parser.rows:
        if not year or len(cells) < 2:
            continue
        meeting_day = _boj_decision_day(cells[0], year)
        if meeting_day is None or not TODAY <= meeting_day <= END:
            continue

        outlook_day = _boj_outlook_day(cells[1], year)
        # The BOJ table's Outlook basic-view date is the policy decision day
        # for outlook meetings. Otherwise use the second (final) meeting day.
        event_day = outlook_day or meeting_day
        if not TODAY <= event_day <= END:
            continue

        has_outlook = outlook_day is not None
        name = ("日銀金融政策決定会合・展望レポート"
                if has_outlook else "日銀金融政策決定会合")
        detail = (
            "日銀の金融政策決定と経済・物価見通しを確認。"
            if has_outlook else
            "日銀の金融政策決定内容を確認。"
        )
        out.append(mk_event(
            event_day, name, "日本", detail, url,
            "日本銀行 金融政策決定会合日程（公式ページ自動取得）",
            "時刻未定", "最重要"
        ))

    # A zero-result parse is treated as a source-format failure, not as a
    # successful empty schedule. This prevents accidental deletion of events.
    if not out:
        raise ValueError(
            "BOJ official meeting table yielded no future events in the 90-day window"
        )

    unique = {}
    for event in out:
        unique[(event["date"], event["name"])] = event
    return list(unique.values())


def main():
    if not DATA.exists():
        raise SystemExit("events.json is missing; refusing to create an empty file.")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    parsed = []
    failed = []
    successful = set()
    fallback_sources = set()
    parsers = [("fed", parse_fomc), ("boj", parse_boj), ("bls", parse_bls)]
    for key, parser in parsers:
        try:
            payload = fetch(SOURCES[key])
            events = parser(payload)
            if key == "bls" and not events:
                # An accessible but empty feed is also suspicious; try the
                # published annual schedule before declaring a successful zero.
                events = parse_bls_fallback()
                fallback_sources.add(key)
                print(f"bls: iCalendar returned 0 target events; official annual schedule auto-parsed {len(events)} events")
            parsed.extend(events)
            successful.add(key)
            print(f"{key}: parsed {len(events)} candidate events")
        except Exception as exc:
            if key == "bls":
                try:
                    events = parse_bls_fallback()
                    parsed.extend(events)
                    successful.add(key)
                    fallback_sources.add(key)
                    print(f"bls: primary feed failed ({exc}); official annual schedule auto-parsed {len(events)} events")
                    continue
                except Exception as fallback_exc:
                    failed.append((key, f"primary: {exc}; fallback: {fallback_exc}"))
                    print(f"WARNING bls: primary and fallback failed: {exc}; {fallback_exc}")
                    continue
            failed.append((key, str(exc)))
            print(f"WARNING {key}: {exc}")

    # If all sources fail, do not touch the file. Otherwise update only sources
    # that succeeded, preserving existing entries owned by failed sources.
    if not successful:
        raise SystemExit("All official sources failed; leaving events.json unchanged.")

    source_patterns = {
        "fed": ("FOMC",),
        "boj": ("日銀金融政策決定会合", "日銀・企業向けサービス価格指数"),
        "bls": ("米国雇用統計", "米国CPI", "米国PPI"),
    }
    replace_patterns = tuple(
        pattern
        for source_key, patterns in source_patterns.items()
        if source_key in successful
        for pattern in patterns
    )
    # Keep all future entries from sources that were refreshed but not returned
    # by the parser. This avoids deleting a valid official date because a page
    # temporarily changed its HTML or exposed only part of its schedule.
    preserved = []
    for event in data.get("economicEvents", []):
        name = event.get("name", "")
        is_managed = any(pattern in name for pattern in replace_patterns)
        if not is_managed:
            preserved.append(event)
            continue
        raw_day = event.get("date")
        try:
            event_day = date.fromisoformat(str(raw_day))
        except (TypeError, ValueError):
            preserved.append(event)
            continue
        if event_day >= TODAY and not any(
            fresh.get("date") == raw_day and fresh.get("name") == name
            for fresh in parsed
        ):
            preserved.append(event)

    combined = preserved + parsed
    unique = {}
    for event in combined:
        key = (event.get("date"), event.get("name"))
        if key not in unique or event.get("importance") == "最重要":
            unique[key] = event
    data["economicEvents"] = sorted(
        unique.values(), key=lambda event: (event.get("date") or "9999", event.get("timeJst") or "")
    )
    data["updatedAt"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST") + "（公式スケジュール部分確認）"
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(data['economicEvents'])} events and preserved {len(data.get('earnings', []))} earnings entries.")
    if failed:
        print("PARTIAL UPDATE: these sources failed; their existing events were preserved: " + "; ".join(f"{k}: {err}" for k, err in failed))
    elif fallback_sources:
        print("Official data updated with automatic annual-schedule parsing for: " + ", ".join(sorted(fallback_sources)))
    else:
        print("All official primary feeds refreshed successfully.")

if __name__ == "__main__":
    main()
