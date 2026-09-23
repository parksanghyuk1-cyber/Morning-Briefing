"""
대시보드 상단의 [오늘의 핵심 이슈], [주요 일정] 섹션
- 핵심 이슈: CNBC Top News RSS, 최근 24시간 헤드라인
- 주요 일정: 향후 2주 미국 주요 지표 발표 및 FOMC
    FRED 발표 일정 (BLS, BEA, Census)  → FRED_API_KEY 필요
    BEA 공식 캘린더                   → FRED 실패 시 대체 (BLS는 자동 접속 차단)
    ISM PMI                          → 제조업 매월 첫 영업일, 서비스업 셋째 영업일
    FOMC                             → 연준 홈페이지 일정표
"""
import datetime
import email.utils
import html
import os
import re
import xml.etree.ElementTree as ET

import holidays
import requests

KST = datetime.timezone(datetime.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0"}

CNBC_TOP_NEWS = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/releases/dates"
BEA_ICS = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FOMC_PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

BLS = "U.S. Bureau of Labor Statistics"
BEA = "U.S. Bureau of Economic Analysis"
CENSUS = "U.S. Census Bureau"

# FRED release_name → (표시 이름, 발표 기관)
FRED_RELEASES = {
    "Consumer Price Index": ("미국 CPI 소비자물가", BLS),
    "Employment Situation": ("미국 고용보고서 (비농업 고용·실업률)", BLS),
    "Producer Price Index": ("미국 PPI 생산자물가", BLS),
    "Job Openings and Labor Turnover Survey": ("미국 JOLTS 구인건수", BLS),
    "Personal Income and Outlays": ("미국 PCE 물가", BEA),
    "Gross Domestic Product": ("미국 GDP", BEA),
    "Advance Monthly Sales for Retail and Food Services": ("미국 소매판매", CENSUS),
}

GDP_STAGES = {"Advance": "속보치", "Second": "잠정치", "Third": "확정치"}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ── 오늘의 핵심 이슈 ─────────────────────────────────────────────────────────

def get_headlines(n: int = 3, hours: int = 24) -> list[str]:
    """CNBC 편집 순서 그대로 최근 기사 n개, 텔레그램 HTML용으로 escape"""
    try:
        resp = requests.get(CNBC_TOP_NEWS, headers=UA, timeout=15)
        resp.raise_for_status()
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
        titles = []
        for item in ET.fromstring(resp.content).iter("item"):
            title = (item.findtext("title") or "").strip()
            pub = item.findtext("pubDate")
            if not title or not pub:
                continue
            if email.utils.parsedate_to_datetime(pub) < cutoff:
                continue
            titles.append(html.escape(title))
            if len(titles) >= n:
                break
        return titles
    except Exception as e:
        print(f"[headlines] {e}")
        return []


# ── 주요 일정 ─────────────────────────────────────────────────────────────────

def _fred_events(start: datetime.date, end: datetime.date) -> list[tuple]:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY 미설정")
    resp = requests.get(FRED_RELEASE_DATES, timeout=15, params={
        "api_key": key,
        "file_type": "json",
        "realtime_start": start.isoformat(),
        "realtime_end": end.isoformat(),
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc",
        "limit": 1000,
    })
    resp.raise_for_status()
    events = []
    for r in resp.json().get("release_dates", []):
        spec = FRED_RELEASES.get(r.get("release_name"))
        if spec:
            events.append((datetime.date.fromisoformat(r["date"]), *spec))
    return events


def _bea_events(start: datetime.date, end: datetime.date) -> list[tuple]:
    raw = requests.get(BEA_ICS, headers=UA, timeout=15).text
    events = []
    for block in raw.split("BEGIN:VEVENT")[1:]:
        d = re.search(r"DTSTART[^:]*:(\d{8})", block)
        s = re.search(r"SUMMARY[^:]*:(.*)", block)
        if not d or not s:
            continue
        date = datetime.datetime.strptime(d.group(1), "%Y%m%d").date()
        summary = s.group(1)
        if not start <= date <= end:
            continue
        if summary.startswith("Personal Income and Outlays"):
            events.append((date, "미국 PCE 물가", BEA))
        elif summary.startswith("GDP"):
            stage = next((v for k, v in GDP_STAGES.items() if k in summary), "")
            events.append((date, f"미국 GDP{f' ({stage})' if stage else ''}", BEA))
    return events


def _us_business_days(year: int, month: int) -> list[datetime.date]:
    us_holidays = holidays.US(years=year)
    d = datetime.date(year, month, 1)
    days = []
    while d.month == month:
        if d.weekday() < 5 and d not in us_holidays:
            days.append(d)
        d += datetime.timedelta(days=1)
    return days


def _ism_events(start: datetime.date, end: datetime.date) -> list[tuple]:
    events = []
    y, m = start.year, start.month
    while datetime.date(y, m, 1) <= end:
        bdays = _us_business_days(y, m)
        events.append((bdays[0], "미국 ISM 제조업 PMI", "ISM"))
        events.append((bdays[2], "미국 ISM 서비스업 PMI", "ISM"))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return [e for e in events if start <= e[0] <= end]


def _fomc_events(start: datetime.date, end: datetime.date) -> list[tuple]:
    """연준 일정표에서 회의 마지막 날(결정 발표일)만 추출, *는 경제전망(점도표) 발표 회의"""
    raw = requests.get(FOMC_PAGE, headers=UA, timeout=15).text
    events = []
    sections = re.split(r"(\d{4}) FOMC Meetings", raw)
    for year, body in zip(sections[1::2], sections[2::2]):
        for month, days in re.findall(
            r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>[\s\S]*?fomc-meeting__date[^>]*>([^<]+)<', body
        ):
            if "notation" in days.lower() or "unscheduled" in days.lower():
                continue
            last_month = MONTHS.get(month.split("/")[-1].strip()[:3].lower())
            last_day = re.findall(r"\d+", days)
            if not last_month or not last_day:
                continue
            date = datetime.date(int(year), last_month, int(last_day[-1]))
            if start <= date <= end:
                name = "FOMC 금리 결정" + (" (점도표 발표)" if "*" in days else "")
                events.append((date, name, "Federal Reserve"))
    return events


def get_upcoming_events(days: int = 14, limit: int = 6) -> list[str]:
    """오늘(KST)부터 days일 이내 일정. 날짜는 미국 현지 발표일 기준"""
    today = datetime.datetime.now(KST).date()
    end = today + datetime.timedelta(days=days)

    events = []
    try:
        events += _fred_events(today, end)
    except Exception as e:
        print(f"[calendar] FRED 실패, BEA 캘린더로 대체: {e}")
        try:
            events += _bea_events(today, end)
        except Exception as e2:
            print(f"[calendar] BEA 실패: {e2}")
    for source in (_ism_events, _fomc_events):
        try:
            events += source(today, end)
        except Exception as e:
            print(f"[calendar] {source.__name__} 실패: {e}")

    events = sorted(set(events))[:limit]
    lines = []
    for date, name, org in events:
        tonight = " ← 오늘 밤" if date == today else ""
        lines.append(f"- {date:%m/%d} {name} ({org}){tonight}")
    return lines
