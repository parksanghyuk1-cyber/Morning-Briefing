"""
대시보드 상단의 [오늘의 핵심 이슈], [주요 일정] 섹션
- 핵심 이슈: 로이터, 블룸버그, WSJ, FT, CNBC 최근 24시간 기사 중
             Gemini가 시장 영향이 큰 3건을 고르고 한 줄 설명 작성
             (Gemini 실패 시 매체별 최신 기사를 설명 없이 사용)
- 주요 일정: 향후 2주 미국 주요 지표 발표 및 FOMC, 중요도 ★ 1~5개
    FRED 발표 일정 (BLS, BEA, Census)  → FRED_API_KEY 필요
    BEA 공식 캘린더                   → FRED 실패 시 대체 (BLS는 자동 접속 차단)
    ISM PMI                          → 제조업 매월 첫 영업일, 서비스업 셋째 영업일
    FOMC                             → 연준 홈페이지 일정표
"""
import datetime
import email.utils
import html
import json
import os
import re
import xml.etree.ElementTree as ET

import holidays
import requests

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

KST = datetime.timezone(datetime.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}

# (매체, RSS 주소) — 위에 있을수록 Gemini 실패 시 우선 사용
NEWS_FEEDS = [
    ("Reuters", "https://news.google.com/rss/search?q=site:reuters.com+when:1d+(markets+OR+economy+OR+fed)"
                "&hl=en-US&gl=US&ceid=US:en"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg", "https://feeds.bloomberg.com/economics/news.rss"),
    ("WSJ", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"),
    ("FT", "https://www.ft.com/markets?format=rss"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
]
PER_FEED = 12  # 피드당 후보 기사 수

FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/releases/dates"
BEA_ICS = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
FOMC_PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

BLS = "U.S. Bureau of Labor Statistics"
BEA = "U.S. Bureau of Economic Analysis"
CENSUS = "U.S. Census Bureau"

# 중요도 1~5
#   5 = 시장 방향을 바꾸는 이벤트 (FOMC, CPI, 고용보고서)
#   4 = 연준이 직접 보는 지표 (PCE, GDP 속보치)
#   3 = 경기 흐름 확인 (ISM 제조업, 소매판매, PPI)
#   2 = 보조 지표 (ISM 서비스업, JOLTS, GDP 잠정치)
#   1 = 참고 (GDP 확정치)
MAX_STARS = 5

# FRED release_name → (표시 이름, 발표 기관, 중요도)
FRED_RELEASES = {
    "Consumer Price Index": ("미국 CPI 소비자물가", BLS, 5),
    "Employment Situation": ("미국 고용보고서 (비농업 고용·실업률)", BLS, 5),
    "Personal Income and Outlays": ("미국 PCE 물가", BEA, 4),
    "Gross Domestic Product": ("미국 GDP", BEA, 3),
    "Producer Price Index": ("미국 PPI 생산자물가", BLS, 3),
    "Advance Monthly Sales for Retail and Food Services": ("미국 소매판매", CENSUS, 3),
    "Job Openings and Labor Turnover Survey": ("미국 JOLTS 구인건수", BLS, 2),
}
# BEA 캘린더의 GDP 차수 → (표시, 중요도)
GDP_STAGES = {"Advance": ("속보치", 4), "Second": ("잠정치", 2), "Third": ("확정치", 1)}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ── 오늘의 핵심 이슈 ─────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _collect_articles(hours: int) -> list[dict]:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    articles, seen = [], set()
    for source, url in NEWS_FEEDS:
        try:
            resp = requests.get(url, headers=UA, timeout=15)
            resp.raise_for_status()
            count = 0
            for item in ET.fromstring(resp.content).iter("item"):
                title = _clean(item.findtext("title"))
                pub = item.findtext("pubDate")
                if not title or not pub or email.utils.parsedate_to_datetime(pub) < cutoff:
                    continue
                if source == "Reuters":
                    # 구글 뉴스 제목 끝의 " - Reuters" 제거, 설명란은 링크뿐이라 버림
                    title, summary = re.sub(r"\s+-\s+Reuters$", "", title), ""
                else:
                    summary = _clean(item.findtext("description"))[:300]
                if title.lower() in seen:
                    continue
                seen.add(title.lower())
                articles.append({"source": source, "title": title, "summary": summary})
                count += 1
                if count >= PER_FEED:
                    break
        except Exception as e:
            print(f"[news] {source} 실패: {e}")
    return articles


def _pick_with_gemini(articles: list[dict], n: int) -> list[dict]:
    if not os.environ.get("GEMINI_API_KEY") or not GENAI_AVAILABLE:
        raise RuntimeError("Gemini 사용 불가")

    listing = "\n".join(
        f"[{i}] ({a['source']}) {a['title']}" + (f" — {a['summary']}" if a["summary"] else "")
        for i, a in enumerate(articles)
    )
    prompt = f"""아래는 최근 24시간 글로벌 금융 뉴스 목록입니다.
한국 투자자가 오늘 아침 반드시 알아야 할 시장 영향이 큰 이슈 {n}건을 고르세요.

규칙:
- 서로 다른 이슈로 {n}건. 같은 사건을 다룬 기사는 1건만
- 가능하면 서로 다른 매체에서 고를 것
- 개별 기업 소식보다 금리, 물가, 통화정책, 환율, 원자재, 지정학 등 거시 이슈 우선
- 각 이슈에 한국어 설명 한 문장 (60자 이내, 명사형 마무리)
- 설명은 목록에 있는 제목과 요약 내용만 근거로 작성하고, 없는 수치나 사실을 추가하지 말 것

JSON 배열만 출력: [{{"id": 번호, "summary": "한 줄 설명"}}]

[뉴스 목록]
{listing}"""

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=4096, temperature=0.2, response_mime_type="application/json",
        ),
    )
    picks = []
    for p in json.loads(resp.text):
        i = int(p["id"])
        if 0 <= i < len(articles) and all(x["title"] != articles[i]["title"] for x in picks):
            picks.append({**articles[i], "explain": str(p.get("summary", "")).strip()})
    if not picks:
        raise RuntimeError("Gemini 응답에 유효한 기사 없음")
    return picks[:n]


def _pick_fallback(articles: list[dict], n: int) -> list[dict]:
    """매체별 최신 기사 1개씩 순서대로"""
    picks, used = [], set()
    for a in articles:
        if a["source"] not in used:
            picks.append({**a, "explain": ""})
            used.add(a["source"])
        if len(picks) >= n:
            break
    return picks


def get_headlines(n: int = 3, hours: int = 24) -> list[dict]:
    """[{source, title, summary, explain}], 텍스트는 escape 전 원문"""
    articles = _collect_articles(hours)
    if not articles:
        return []
    try:
        return _pick_with_gemini(articles, n)
    except Exception as e:
        print(f"[news] Gemini 선별 실패, 매체별 최신 기사로 대체: {e}")
        return _pick_fallback(articles, n)


def format_headlines(picks: list[dict]) -> list[str]:
    """텔레그램 HTML 메시지용 줄"""
    lines = []
    for i, p in enumerate(picks, 1):
        lines.append(f"{i}. {html.escape(p['title'], quote=False)} ({p['source']})")
        if p["explain"]:
            lines.append(f"   └ {html.escape(p['explain'], quote=False)}")
    return lines


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
            events.append((date, "미국 PCE 물가", BEA, 4))
        elif summary.startswith("GDP"):
            stage, stars = next((v for k, v in GDP_STAGES.items() if k in summary), ("", 3))
            events.append((date, f"미국 GDP{f' ({stage})' if stage else ''}", BEA, stars))
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
        events.append((bdays[0], "미국 ISM 제조업 PMI", "ISM", 3))
        events.append((bdays[2], "미국 ISM 서비스업 PMI", "ISM", 2))
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
                events.append((date, name, "Federal Reserve", 5))
    return events


def get_upcoming_events(days: int = 14, limit: int = 6) -> list[str]:
    """오늘(KST)부터 days일 이내 일정, 날짜는 미국 현지 발표일 기준
    limit을 넘으면 중요도 낮은 것부터 제외"""
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

    events = set(events)
    keep = sorted(events, key=lambda e: (-e[3], e[0]))[:limit]
    lines = []
    for date, name, org, stars in sorted(keep, key=lambda e: (e[0], -e[3], e[1])):
        tonight = " · 오늘 밤" if date == today else ""
        lines.append(f"- {date:%m/%d} {name} ({org}) {'★' * stars}{'☆' * (MAX_STARS - stars)}{tonight}")
    return lines
