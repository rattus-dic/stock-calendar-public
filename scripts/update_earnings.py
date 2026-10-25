#!/usr/bin/env python3
"""Update earnings for the 50-company watchlist, preserving unrelated calendar data.

US: Nasdaq public earnings-calendar endpoint (schedule dates and broad session labels).
JP: Traders Web public earnings calendar, filtered to the curated watchlist.
Dates/times are not represented as company-confirmed unless the source provides a date.
If either provider fails, entries for that market are preserved rather than deleted.
"""
import json, re, sys
from datetime import datetime, timedelta, date, time
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'events.json'
ROSTER_FILE = ROOT / 'earnings_watchlist.json'
JST = ZoneInfo('Asia/Tokyo')
ET = ZoneInfo('America/New_York')
TODAY = datetime.now(JST).date()
END = TODAY + timedelta(days=14)
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
NASDAQ_URL = 'https://api.nasdaq.com/api/calendar/earnings?date={}'
JP_CALENDAR_URL = 'https://www.traders.co.jp/market_jp/earnings_calendar/all/all/1'


def fetch(url, accept='application/json,text/html,*/*'):
    req = Request(url, headers={'User-Agent': UA, 'Accept': accept, 'Accept-Language':'ja,en-US;q=0.9,en;q=0.8', 'Cache-Control':'no-cache'})
    with urlopen(req, timeout=25) as response:
        return response.read().decode('utf-8', errors='replace')


def date_key(d): return d.isoformat()

def norm_ticker(value): return re.sub(r'[^A-Z0-9.]', '', str(value or '').upper())

def make_row(company, day, time_label, source_name, source_url, detail, confirmed=True):
    return {
        'date': day.isoformat(), 'timeJst': time_label or '時刻未定',
        'market': '日本' if company['market']=='JP' else '米国',
        'name': f"{company['name']}（{company['ticker']}）",
        'ticker': company['ticker'], 'detail': detail,
        'confirmed': bool(confirmed), 'status': '予定日取得済み' if confirmed else '日程未確認',
        'sourceName': source_name, 'sourceUrl': source_url
    }


def nasdaq_rows(watch):
    by_ticker = {norm_ticker(c['ticker']): c for c in watch if c['market']=='US'}
    found = {}
    successful_days = 0
    for offset in range(15):
        day = TODAY + timedelta(days=offset)
        url = NASDAQ_URL.format(day.isoformat())
        payload = json.loads(fetch(url))
        if payload.get('status', {}).get('rCode') not in (None, 200):
            raise RuntimeError(f'Nasdaq API status {payload.get("status")}')
        data = payload.get('data') or {}
        rows = data.get('rows') or []
        # A valid response with an empty rows array is still a successful date query.
        successful_days += 1
        for row in rows:
            ticker = norm_ticker(row.get('symbol') or row.get('ticker'))
            company = by_ticker.get(ticker)
            if not company:
                continue
            raw_time = str(row.get('time') or row.get('timeOfDay') or '').lower().replace('_','-')
            # Nasdaq's session labels are not exact release times; never fabricate HH:MM.
            if 'after' in raw_time or 'close' in raw_time:
                label = '米国市場引け後（正確な時刻未確認）'
            elif 'before' in raw_time or 'open' in raw_time:
                label = '米国市場開始前（正確な時刻未確認）'
            else:
                label = '時刻未定'
            name = str(row.get('name') or company['name'])
            found[ticker] = make_row(company, day, label, 'Nasdaq Earnings Calendar', url,
                f'Nasdaqの決算カレンダーに掲載。時刻ラベルは市場セッション区分で、正確な発表時刻ではありません。企業公式IRで最終確認してください。')
    if successful_days != 15:
        raise RuntimeError('Nasdaq calendar did not return all 15 day queries')
    return list(found.values())


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.rows=[]; self.row=None; self.cell=None; self.in_table=False
    def handle_starttag(self, tag, attrs):
        tag=tag.lower()
        if tag=='tr': self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        tag=tag.lower()
        if tag in ('td','th') and self.row is not None and self.cell is not None:
            self.row.append(re.sub(r'\s+',' ',''.join(self.cell)).strip()); self.cell=None
        elif tag=='tr' and self.row is not None:
            if self.row: self.rows.append(self.row)
            self.row=None; self.cell=None


def parse_jp_date(text, today):
    # Site commonly renders MM/DD or YYYY/MM/DD in table cells.
    m=re.search(r'(?<!\d)(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})', text)
    if m:
        try: return date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
        except ValueError: return None
    m=re.search(r'(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)', text)
    if m:
        month,day=int(m.group(1)),int(m.group(2))
        for year in (today.year,today.year+1):
            try:
                candidate=date(year,month,day)
                if today-timedelta(days=7) <= candidate <= today+timedelta(days=370): return candidate
            except ValueError: pass
    m=re.search(r'(\d{1,2})月(\d{1,2})日',text)
    if m:
        try: return date(today.year,int(m.group(1)),int(m.group(2)))
        except ValueError: return None
    return None


def jp_rows(watch):
    by_ticker={c['ticker']:c for c in watch if c['market']=='JP'}
    html=fetch(JP_CALENDAR_URL, 'text/html,application/xhtml+xml,*/*')
    parser=TableParser(); parser.feed(html)
    found={}
    for cells in parser.rows:
        line=' '.join(cells)
        matched=None
        # JP rows may use numeric stock code; allow 285A style codes too.
        for ticker, company in by_ticker.items():
            if re.search(r'(?<![A-Z0-9])'+re.escape(ticker)+r'(?![A-Z0-9])', line, re.I):
                matched=company; break
        if not matched: continue
        day=None
        for cell in cells:
            day=parse_jp_date(cell,TODAY)
            if day: break
        if not day or not (TODAY <= day <= END): continue
        time_label='時刻未定'
        for cell in cells:
            tm=re.search(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)',cell)
            if tm:
                hh,mm=int(tm.group(1)),int(tm.group(2))
                if 0<=hh<=23 and 0<=mm<=59: time_label=f'{hh:02d}:{mm:02d}'; break
        found[matched['ticker']]=make_row(matched,day,time_label,'トレーダーズ・ウェブ 決算発表スケジュール',JP_CALENDAR_URL,
          '第三者の決算カレンダー掲載情報。会社側の変更があり得るため、企業公式IRで最終確認してください。')
    # Do not treat a structurally empty/unparseable page as a successful no-results response.
    if not parser.rows or not found:
        raise RuntimeError(f'Japanese earnings calendar could not be parsed/matched (rows={len(parser.rows)}, matched={len(found)})')
    return list(found.values())


def main():
    if not DATA.exists() or not ROSTER_FILE.exists(): raise SystemExit('events.json or earnings_watchlist.json missing')
    data=json.loads(DATA.read_text(encoding='utf-8'))
    roster=json.loads(ROSTER_FILE.read_text(encoding='utf-8'))
    watch=roster['companies']
    if len(watch)!=50: raise SystemExit(f'Expected 50 watchlist companies, found {len(watch)}')
    results={}; failed=[]
    for market, fn in [('US',nasdaq_rows),('JP',jp_rows)]:
        try:
            rows=fn(watch)
            results[market]=rows
            print(f'{market}: parsed {len(rows)} watchlist earnings in next 14 days')
        except Exception as exc:
            failed.append((market,str(exc)))
            print(f'WARNING {market} earnings source failed: {exc}')
    if not results: raise SystemExit('Both earnings sources failed; existing earnings entries preserved.')
    watch_tickers={c['ticker'] for c in watch}
    # Replace entries for a market only when that provider succeeded. Preserve non-watchlist entries.
    preserved=[]
    for old in data.get('earnings',[]):
        ticker=str(old.get('ticker') or '')
        name=str(old.get('name') or '')
        market='US' if old.get('market')=='米国' or re.search(r'\([A-Z.]{1,6}\)',name) else 'JP'
        is_watch = ticker in watch_tickers or any(f'({t})' in name or f'（{t}）' in name for t in watch_tickers)
        if is_watch and market in results: continue
        # Do not keep expired dated watchlist records for markets refreshed successfully.
        preserved.append(old)
    merged=preserved+results.get('JP',[])+results.get('US',[])
    # Unique by market/ticker/date; keep newest record on duplicate keys.
    unique={}
    for row in merged:
        key=(row.get('market'),row.get('ticker') or row.get('name'),row.get('date'))
        unique[key]=row
    data['earnings']=sorted(unique.values(),key=lambda r:(r.get('date') or '9999-99-99',r.get('market') or '',r.get('name') or ''))
    data['earningsUpdatedAt']=datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')
    # Keep general updatedAt owned by the macro-events updater; expose separate earnings timestamp.
    DATA.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Saved {len(data["earnings"])} earnings entries; updated markets: {", ".join(results)}')
    if failed: print('PARTIAL UPDATE; existing market entries preserved: '+'; '.join(f'{m}: {e}' for m,e in failed))

if __name__=='__main__': main()
