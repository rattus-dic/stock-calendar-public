import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "events.json"
JST = ZoneInfo("Asia/Tokyo")
TODAY = datetime.now(JST).date()
END = TODAY + timedelta(days=14)

SOURCES = {
    "Federal Reserve FOMC Calendar": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "日本銀行 公表予定": "https://www.boj.or.jp/about/calendar/",
    "U.S. Bureau of Labor Statistics": "https://www.bls.gov/schedule/2026/11_sched_list.htm",
}

def fetch(url):
    req = Request(url, headers={"User-Agent": "stock-calendar-public/1.0 (public market event calendar)"})
    with urlopen(req, timeout=20) as res:
        return res.read().decode("utf-8", errors="replace")

def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    # Fetch official sources daily so failed/changed source pages are detected in the workflow log.
    source_status = {}
    for name, url in SOURCES.items():
        try:
            body = fetch(url)
            source_status[name] = len(body) > 1000
            if not source_status[name]:
                print(f"WARNING: official source returned unusually little content: {name}")
        except Exception as exc:
            source_status[name] = False
            print(f"WARNING: source fetch failed: {name}: {exc}")

    # Keep only the selected current/next-week events in the visible event panel.
    # Dates are curated from the linked official schedules; never infer missing dates.
    kept = []
    for event in data.get("economicEvents", []):
        date_text = event.get("date")
        if not date_text:
            continue
        try:
            day = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            continue
        if TODAY <= day <= END:
            kept.append(event)
    data["economicEvents"] = kept
    data["updatedAt"] = datetime.now(JST).strftime("%Y-%m-%d %H:%M JST") + "（公式ソース確認）"
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(f"Saved {len(kept)} events for {TODAY} through {END}; source checks: {source_status}")

if __name__ == "__main__":
    main()
