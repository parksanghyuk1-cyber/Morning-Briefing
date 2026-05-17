"""
weekly_outlook.py
─────────────────
주간 글로벌 시장 전망 및 주요 일정
- ForexFactory JSON API: 경제지표 캘린더 (작동 시)
- yfinance: 직전주 시장 데이터
- Groq: 전체 브리핑 생성 (캘린더 포함 fallback)
매주 일요일 오후 6시 KST 자동 전송
"""

import os
import json
import datetime
import requests
import yfinance as yf
from groq import Groq


# ─────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

COUNTRY_EMOJI = {
    "USD": "🇺🇸", "KRW": "🇰🇷", "CNY": "🇨🇳", "JPY": "🇯🇵",
    "EUR": "🇪🇺", "GBP": "🇬🇧", "AUD": "🇦🇺", "CAD": "🇨🇦",
    "CHF": "🇨🇭", "NZD": "🇳🇿",
}

def next_week_range():
    today = datetime.date.today()
    days_to_mon = (7 - today.weekday()) % 7 or 7
    mon = today + datetime.timedelta(days=days_to_mon)
    fri = mon + datetime.timedelta(days=4)
    return mon, fri

def fmt_date_kr(d: datetime.date) -> str:
    return f"{d.month}월 {d.day}일 ({WEEKDAY_KR[d.weekday()]})"


# ─────────────────────────────────────────
# 1. 경제지표 캘린더 (ForexFactory)
# ─────────────────────────────────────────

def fetch_ff_calendar() -> list[dict]:
    """ForexFactory 다음주 캘린더. 실패 시 빈 리스트."""
    try:
        resp = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []

def parse_ff_calendar(raw: list[dict], mon: datetime.date) -> dict:
    """날짜별로 High/Medium 이벤트 분류. {date_str: [events]}"""
    by_date = {}
    for i in range(5):
        d = mon + datetime.timedelta(days=i)
        by_date[d.isoformat()] = []

    for ev in raw:
        try:
            dt_str  = ev.get("date", "")
            dt      = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            date_key = dt.date().isoformat()
            if date_key not in by_date:
                continue
            if ev.get("impact") not in ("High", "Medium"):
                continue
            country  = ev.get("country", "")
            emoji    = COUNTRY_EMOJI.get(country, "🌐")
            forecast = ev.get("forecast") or "-"
            previous = ev.get("previous") or "-"
            by_date[date_key].append({
                "emoji":    emoji,
                "title":    ev.get("title", ""),
                "impact":   ev.get("impact", ""),
                "forecast": forecast,
                "previous": previous,
            })
        except Exception:
            continue
    return by_date


# ─────────────────────────────────────────
# 2. 직전주 시장 데이터 (Groq 컨텍스트용)
# ─────────────────────────────────────────

def get_market_context() -> str:
    tickers = {
        "S&P500":    "^GSPC",
        "나스닥":    "^IXIC",
        "다우":      "^DJI",
        "VIX":       "^VIX",
        "달러인덱스": "DX-Y.NYB",
        "원달러":    "KRW=X",
        "미국10년채": "^TNX",
        "금":        "GC=F",
        "WTI":       "CL=F",
    }
    lines = []
    for name, ticker in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="10d")
            if len(hist) < 2:
                continue
            curr = float(hist["Close"].iloc[-1])
            week_prev = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else float(hist["Close"].iloc[0])
            chg = (curr - week_prev) / week_prev * 100
            lines.append(f"- {name}: {curr:.2f} (주간 {chg:+.2f}%)")
        except Exception:
            pass
    return "\n".join(lines) if lines else "시장 데이터 수집 실패"


# ─────────────────────────────────────────
# 3. Groq로 주간 전망 생성
# ─────────────────────────────────────────

def build_groq_prompt(
    mon: datetime.date,
    fri: datetime.date,
    calendar_by_date: dict,
    market_context: str,
    has_calendar: bool,
) -> str:

    # 캘린더 데이터 텍스트화
    calendar_text = ""
    if has_calendar:
        for i in range(5):
            d = mon + datetime.timedelta(days=i)
            key = d.isoformat()
            events = calendar_by_date.get(key, [])
            if events:
                calendar_text += f"\n{fmt_date_kr(d)}:\n"
                for ev in events:
                    stars = "⭐⭐⭐" if ev["impact"] == "High" else "⭐⭐"
                    calendar_text += f"  {ev['emoji']} {ev['title']} {stars} (예상: {ev['forecast']} / 이전: {ev['previous']})\n"

    week_str = f"{mon.year}년 {mon.month}월 {mon.day}일 ~ {fri.month}월 {fri.day}일"

    return f"""당신은 한국 VC/기관투자자를 위한 주간 글로벌 시장 브리핑을 작성하는 전문 애널리스트입니다.

아래 정보를 바탕으로 [{week_str}] 주간 글로벌 시장 전망 및 주요 일정을 작성해주세요.

=== 직전주 시장 데이터 ===
{market_context}

=== 다음주 경제지표 일정 (ForexFactory 데이터) ===
{calendar_text if has_calendar else "캘린더 데이터 수집 실패 - 주요 예정 지표를 AI가 직접 작성해주세요"}

=== 출력 형식 (반드시 준수) ===

안녕하십니까 🤖 주간 시장 브리핑 봇입니다

[{week_str}] 주간 글로벌 시장 전망 및 주요 일정입니다.
[직전주 시장 흐름과 다음주 주요 변수를 2-3문장으로 요약]

━━━━━━━━━━
📅 요일별 상세 일정
━━━━━━━━━━

[월요일부터 금요일까지 각 요일별로 아래 형식 반복]
📌 [날짜] ([요일]): [해당일 핵심 이벤트 한줄 제목]
√ 시장 휴장
- [휴장 시장 목록, 없으면 (없음)]
√ 경제 지표
- [국가이모지] [지표명] (Consens: [예상치] / Prev: [이전치])
  [지표가 없으면 (주요 지표 발표 없음)]
√ 주요 뉴스 및 실적/이벤트
- [주요 이벤트, 없으면 (없음)]

━━━━━━━━━━
💡 투자 전략적 관점
━━━━━━━━━━
📌 변동성 관리
- [2-3개 불릿]
📌 섹터별 모멘텀
- [2-3개 불릿, 구체적 섹터/종목 언급]
📌 매크로/환율 체크
- [2-3개 불릿]

=== 작성 규칙 ===
- 한국어로 작성
- 구체적 수치와 지표명 사용
- 전문적이고 간결한 문체
- 과도한 수식어 지양
- 전체 길이: 텔레그램 기준 2000-2500자
"""

def generate_with_groq(prompt: str) -> str:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    msg = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3000,
        temperature=0.4,
    )
    return msg.choices[0].message.content


# ─────────────────────────────────────────
# 4. Telegram 전송
# ─────────────────────────────────────────

def send_telegram(text: str):
    token   = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    # 4000자씩 분할 전송
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
            timeout=15,
        )
        if not resp.ok:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
                timeout=15,
            )


# ─────────────────────────────────────────
# 5. 메인
# ─────────────────────────────────────────

def main():
    mon, fri = next_week_range()
    print(f"대상 주: {mon} ~ {fri}")

    print("  → ForexFactory 캘린더 수집 중...")
    raw_calendar = fetch_ff_calendar()
    has_calendar = len(raw_calendar) > 0
    calendar_by_date = parse_ff_calendar(raw_calendar, mon) if has_calendar else {}
    print(f"  → 캘린더: {'수집 성공' if has_calendar else '실패 → Groq 직접 생성'}")

    print("  → 시장 데이터 수집 중...")
    market_ctx = get_market_context()

    print("  → Groq 주간 전망 생성 중...")
    prompt   = build_groq_prompt(mon, fri, calendar_by_date, market_ctx, has_calendar)
    briefing = generate_with_groq(prompt)

    print("  → Telegram 전송 중...")
    send_telegram(briefing)

    print("✅ 완료")
    print("─" * 60)
    print(briefing)


if __name__ == "__main__":
    main()
