#!/usr/bin/env python3
"""Update the 50-company earnings calendar with multiple public sources.

US: Nasdaq public earnings calendar (daily, rolling 90 days).
JP: Traders Web calendar (all available pagination pages) plus Matsui Securities
    schedule page as a secondary cross-check when its public HTML is accessible.

The script never treats an empty result from one source as proof that no dates
exist. It refreshes changed dates, avoids stale duplicate future dates, preserves
past history and manual/official records, and labels unknown next dates clearly.
"""
import json
import re
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse, parse_qs
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "events.json"
ROSTER_FILE = ROOT / "earnings_watchlist.json"
JST = ZoneInfo("Asia/Tokyo")
TODAY = datetime.now(JST).date()
END = TODAY + timedelta(days=90)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings?date={}"
TRADERS_BASE = "https://www.traders.co.jp/market_jp/earnings_calendar/all/all/{}"
TRADERS_URL = "https://www.traders.co.jp/market_jp/earnings_calendar"
MATSUI_URL = "https://finance.matsui.co.jp/find-by-schedule/index"
MAX_TRADERS_PAGES = 8


def fetch(url, accept="text/html,application/xhtml+xml,application/json,*/*"):
    req = Request(url, headers={
        "User-Agent": UA, "Accept": accept,
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    })
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def norm_ticker(value):
    return re.sub(r"[^A-Z0-9.]", "", str(value or "").upper())


def make_row(company, day, time_label, source_name, source_url, detail):
    return {
        "date": day.isoformat(),
        "timeJst": time_label or "時刻未定",
        "market": "日本" if company["market"] == "JP" else "米国",
        "name": f'{company["name"]}（{company["ticker"]}）',
        "ticker": company["ticker"],
        "detail": detail,
        "confirmed": True,
        "status": "予定日取得済み",
        "sourceName": source_name,
        "sourceUrl": source_url,
    }


def parse_date(text, today=TODAY):
    text = str(text or "")
    m = re.search(r"(?<!\d)(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
                if today - timedelta(days=7) <= candidate <= today + timedelta(days=370):
                    return candidate
            except ValueError:
                pass
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
                if today - timedelta(days=7) <= candidate <= today + timedelta(days=370):
                    return candidate
            except ValueError:
                pass
    return None


class HTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self.row is not None and self.cell is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row, self.cell = None, None


def match_company(line, by_ticker):
    # Prefer exact ticker token matches; handles numeric codes and 285A.
    for ticker, company in by_ticker.items():
        if re.search(r"(?<![A-Z0-9])" + re.escape(ticker) + r"(?![A-Z0-9])", line, re.I):
            return company
    return None


def time_from_cells(cells):
    for cell in cells:
        m = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", cell)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"
    return "時刻未定"


def rows_from_calendar(html, by_ticker, source_name, source_url, source_detail):
    parser = HTMLTableParser()
    parser.feed(html)
    found = {}
    for cells in parser.rows:
        line = " ".join(cells)
        company = match_company(line, by_ticker)
        if not company:
            continue
        day = None
        for cell in cells:
            day = parse_date(cell)
            if day:
                break
        if day and TODAY <= day <= END:
            row = make_row(company, day, time_from_cells(cells),
                           source_name, source_url, source_detail)
            found[(company["ticker"], day.isoformat())] = row
    return found, parser


def traders_rows(watch):
    by_ticker = {c["ticker"]: c for c in watch if c["market"] == "JP"}
    found = {}
    successful_pages = 0
    # The provider paginates its full upcoming list (often 500 rows per page).
    # Searching page 1 alone misses many watchlist names.
    for page in range(1, MAX_TRADERS_PAGES + 1):
        url = TRADERS_BASE.format(page)
        html = fetch(url)
        page_rows, parser = rows_from_calendar(
            html, by_ticker, "トレーダーズ・ウェブ 決算発表スケジュール",
            url, "公開決算カレンダー掲載情報。変更される可能性があるため企業IRも確認してください。"
        )
        if not parser.rows:
            if page == 1:
                raise RuntimeError("トレーダーズ・ウェブの表を解析できません")
            break
        successful_pages += 1
        found.update(page_rows)
        # Short pause to avoid hammering a public site.
        time.sleep(0.25)
    if successful_pages == 0:
        raise RuntimeError("トレーダーズ・ウェブのページを取得できません")
    return list(found.values()), successful_pages


def matsui_rows(watch):
    """Best-effort parser for Matsui's public schedule page.

    Matsui may return 403 or render some calendar navigation client-side. That is
    reported as a source failure, not as an empty calendar.
    """
    by_ticker = {c["ticker"]: c for c in watch if c["market"] == "JP"}
    html = fetch(MATSUI_URL)
    found, parser = rows_from_calendar(
        html, by_ticker, "松井証券 決算発表スケジュール",
        MATSUI_URL,
        "松井証券の決算発表スケジュール掲載情報。発表日は東証情報、時刻は前回実績に基づく場合があります。"
    )
    # Follow same-site pagination links where the page exposes them.
    base_host = urlparse(MATSUI_URL).netloc
    candidates = []
    for href in parser.links:
        full = urljoin(MATSUI_URL, href)
        parsed = urlparse(full)
        if parsed.netloc == base_host and full != MATSUI_URL:
            if any(k in (parsed.path + "?" + parsed.query).lower()
                   for k in ("page", "schedule", "date", "month")):
                candidates.append(full)
    for url in list(dict.fromkeys(candidates))[:12]:
        try:
            page_html = fetch(url)
            page_found, _ = rows_from_calendar(
                page_html, by_ticker, "松井証券 決算発表スケジュール",
                url,
                "松井証券の決算発表スケジュール掲載情報。発表日は東証情報、時刻は前回実績に基づく場合があります。"
            )
            found.update(page_found)
            time.sleep(0.2)
        except Exception as exc:
            print(f"WARNING Matsui page skipped: {url}: {exc}")
    if not parser.rows:
        raise RuntimeError("松井証券ページに解析可能な表がありません")
    return list(found.values())


def nasdaq_rows(watch):
    by_ticker = {norm_ticker(c["ticker"]): c for c in watch if c["market"] == "US"}
    found = {}
    successful_days = 0
    for offset in range(91):
        day = TODAY + timedelta(days=offset)
        url = NASDAQ_URL.format(day.isoformat())
        payload = json.loads(fetch(url, "application/json,*/*"))
        if payload.get("status", {}).get("rCode") not in (None, 200):
            raise RuntimeError(f'Nasdaq API status {payload.get("status")}')
        rows = (payload.get("data") or {}).get("rows") or []
        successful_days += 1
        for item in rows:
            ticker = norm_ticker(item.get("symbol") or item.get("ticker"))
            company = by_ticker.get(ticker)
            if not company:
                continue
            raw_time = str(item.get("time") or item.get("timeOfDay") or "").lower().replace("_", "-")
            if "after" in raw_time or "close" in raw_time:
                label = "米国市場引け後"
            elif "before" in raw_time or "open" in raw_time:
                label = "米国市場開始前（正確な時刻未確認）"
            else:
                label = "時刻未定"
            found[(ticker, day.isoformat())] = make_row(
                company, day, label, "Nasdaq Earnings Calendar", url,
                "Nasdaqの決算カレンダー掲載情報。セッション区分は正確な発表時刻ではありません。"
            )
        if offset % 10 == 9:
            time.sleep(0.2)
    if successful_days != 91:
        raise RuntimeError(f"Nasdaq calendar returned {successful_days}/91 day queries")
    return list(found.values())


def merge_rows(old_rows, fresh_rows, watch, successful_sources):
    """Refresh provider dates without retaining superseded dates.

    - Old dates from a provider are replaced when that same provider returns a
      fresh record for the same ticker.
    - If any provider returns a fresh date for a ticker, older third-party
      calendar dates for that ticker are removed to avoid stale duplicates.
    - Manual/official-IR entries are preserved unless a fresh provider record
      is for the same ticker and date; official records retain higher priority.
    - Past dated rows are retained as history.
    """
    provider_names = {
        "Nasdaq Earnings Calendar",
        "松井証券 決算発表スケジュール",
        "トレーダーズ・ウェブ 決算発表スケジュール",
    }
    priority = {
        "Nasdaq Earnings Calendar": 30,
        "トレーダーズ・ウェブ 決算発表スケジュール": 20,
        "松井証券 決算発表スケジュール": 25,
        "ソフトバンクグループ公式IR（補完登録）": 100,
    }

    def ticker_key(row):
        return (row.get("market"), str(row.get("ticker") or ""))

    def row_key(row):
        return (*ticker_key(row), row.get("date") or "")

    fresh_by_ticker = {}
    for row in fresh_rows:
        fresh_by_ticker.setdefault(ticker_key(row), []).append(row)

    # Keep historical rows, manual/official records, and unknown placeholders.
    kept = []
    for old in old_rows:
        raw_date = old.get("date")
        if raw_date:
            try:
                old_day = date.fromisoformat(str(raw_date))
            except ValueError:
                kept.append(old)
                continue
            if old_day < TODAY:
                kept.append(old)
                continue

        old_source = old.get("sourceName")
        key = ticker_key(old)

        # A new result for this ticker supersedes stale calendar-derived future
        # dates from any provider. This prevents old/new date double entries.
        if old_source in provider_names and key in fresh_by_ticker:
            continue

        # If this provider was successfully refreshed but no matching result
        # exists, preserve the old row: absence is not proof of cancellation.
        kept.append(old)

    merged = {}
    for row in kept + fresh_rows:
        key = row_key(row)
        previous = merged.get(key)
        if previous is None or priority.get(row.get("sourceName"), 0) > priority.get(previous.get("sourceName"), 0):
            merged[key] = row

    # If multiple fresh sources disagree on the date for one ticker, keep the
    # higher-priority source's date and report the disagreement in the log.
    selected = {}
    for row in fresh_rows:
        key = ticker_key(row)
        current = selected.get(key)
        if current is None or priority.get(row.get("sourceName"), 0) > priority.get(current.get("sourceName"), 0):
            if current and current.get("date") != row.get("date"):
                print(f'WARNING date disagreement {key}: {current.get("date")} ({current.get("sourceName")}) vs {row.get("date")} ({row.get("sourceName")}); using higher-priority source')
            selected[key] = row
        elif current.get("date") != row.get("date"):
            print(f'WARNING date disagreement {key}: {current.get("date")} ({current.get("sourceName")}) vs {row.get("date")} ({row.get("sourceName")}); keeping higher-priority source')

    # Remove all provider-sourced future rows for tickers with a fresh result,
    # then add only the selected fresh date for that ticker.
    fresh_keys = set(selected)
    final_rows = []
    for row in merged.values():
        if row.get("sourceName") in provider_names and ticker_key(row) in fresh_keys:
            continue
        final_rows.append(row)
    final_rows.extend(selected.values())

    # Preserve existing placeholders. Add a placeholder for every watchlist
    # company without a dated event. The status wording explicitly means
    # "next date not found in the checked sources", not "company has not
    # announced a date".
    dated = {ticker_key(r) for r in final_rows if r.get("date")}
    placeholders = {ticker_key(r) for r in final_rows if not r.get("date")}
    for company in watch:
        market = "日本" if company["market"] == "JP" else "米国"
        key = (market, str(company["ticker"]))
        if key in dated or key in placeholders:
            continue
        final_rows.append({
            "date": None,
            "timeJst": "未定",
            "market": market,
            "name": f'{company["name"]}（{company["ticker"]}）',
            "ticker": company["ticker"],
            "detail": "確認した公開カレンダーでは次回日程を確認できていません。未発表とは限りません。前回決算発表日が分かる場合は履歴欄に保持します。",
            "confirmed": False,
            "status": "次回日程未確認",
            "sourceName": "自動照合",
            "sourceUrl": company.get("irUrl", ""),
        })

    # Make placeholder wording consistent for existing null-date watchlist rows.
    for row in final_rows:
        if not row.get("date") and row.get("ticker"):
            row["status"] = "次回日程未確認"
            row["confirmed"] = False

    return sorted(
        final_rows,
        key=lambda r: (r.get("date") or "9999-99-99", r.get("market") or "", r.get("name") or "")
    )


def main():
    if not DATA.exists() or not ROSTER_FILE.exists():
        raise SystemExit("events.json or earnings_watchlist.json missing")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    watch = json.loads(ROSTER_FILE.read_text(encoding="utf-8")).get("companies", [])
    if len(watch) != 50:
        raise SystemExit(f"Expected 50 watchlist companies, found {len(watch)}")

    fresh = []
    failed = []
    successful_sources = set()
    for market, sources in (
        ("JP", [("Traders Web", traders_rows), ("Matsui", matsui_rows)]),
        ("US", [("Nasdaq", nasdaq_rows)]),
    ):
        successes = 0
        for label, fn in sources:
            try:
                result = fn(watch)
                rows = result[0] if isinstance(result, tuple) else result
                fresh.extend(rows)
                successful_sources.add({"Traders Web": "トレーダーズ・ウェブ 決算発表スケジュール", "Matsui": "松井証券 決算発表スケジュール", "Nasdaq": "Nasdaq Earnings Calendar"}[label])
                successes += 1
                if label == "Traders Web":
                    print(f"JP Traders Web: {len(rows)} matched; pages checked: {result[1]}")
                else:
                    print(f"{market} {label}: {len(rows)} matched")
            except Exception as exc:
                failed.append(f"{market} {label}: {exc}")
                print(f"WARNING {market} {label} failed: {exc}")
        if successes == 0:
            print(f"WARNING: no {market} source succeeded; existing {market} entries are retained")

    # Keep the known official SoftBank date as a temporary fallback.
    sbg = next((c for c in watch if c.get("ticker") == "9984"), None)
    sbg_day = date(2026, 11, 10)
    if sbg and TODAY <= sbg_day <= END:
        fresh.append(make_row(
            sbg, sbg_day, "時刻未定", "ソフトバンクグループ公式IR（補完登録）",
            "https://group.softbank/ir/calendar",
            "会社公式IRで確認した決算発表予定日。発表時刻は未確認。"
        ))

    data["earnings"] = merge_rows(data.get("earnings", []), fresh, watch, successful_sources)
    data["earningsUpdatedAt"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dated_watch = {(r.get("market"), str(r.get("ticker"))) for r in data["earnings"] if r.get("date")}
    print(f'Saved {len(data["earnings"])} earnings entries.')
    print(f'Watchlist with dated entries: {len(dated_watch)}/50')
    print(f'Watchlist without a known date in the calendar: {50-len(dated_watch)}')
    if failed:
        print("PARTIAL UPDATE; source failures: " + "; ".join(failed))


if __name__ == "__main__":
    main()
