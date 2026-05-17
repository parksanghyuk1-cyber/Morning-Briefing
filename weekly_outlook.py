"""
weekly_outlook.py v2
─────────────────────
주간 글로벌 시장 전망 및 주요 일정
- yfinance: 직전주 실제 시장 데이터 (수치 기반 분석)
- ForexFactory: 캘린더 (작동 시) / 실패 시 수치 없이 이름만
- Groq: 전략 분석 (실제 데이터 기반, hallucination 방지)
매주 일요일 오후 6시 KST
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
}

def next_week_range():
    today = datetime.date.today()
    days_to_mon = (7 - today.weekday()) % 7 or 7
    mon = today + datetime.timedelta(days=days_to_mon)
    fri = mon + datetime.timedelta(days=4)
    return mon, fri

def fmt_date(d: datetime.date) -> str:
    return f"{d.month}월 {d.day}일 ({WEEKDAY_KR[d.weekday()]})"


# ─────────────────────────────────────────
# 1. 실제 시장 데이터 수집 (분석 근거)
# ─────────────────────────────────────────

def get_market_data() -> dict:
    """직전주 종가 기준 주요 지표 수집"""
    tickers = {
        "S&P500":    ("^GSPC",    0),
        "나스닥":    ("^IXIC",    0),
        "다우":      ("^DJI",     0),
        "러셀2000":  ("^RUT",     0),
        "VIX":       ("^VIX",     2),
        "미국10년채": ("^TNX",    2),
        "미국2년채":  ("^IRX",    2),
        "달러인덱스": ("DX-Y.NYB", 2),
        "원달러":    ("KRW=X",    2),
        "달러엔":    ("JPY=X",    2),
        "금":        ("GC=F",     0),
        "WTI":       ("CL=F",     2),
        "나스닥선물": ("NQ=F",    0),
    }
    result = {}
    for name, (ticker, dec) in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period="15d")
            if len(hist) < 2:
                continue
            curr  = float(hist["Close"].iloc[-1])
            prev1 = float(hist["Close"].iloc[-2])   # 전일
            prev5 = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else float(hist["Close"].iloc[0])  # 주간
            result[name] = {
                "현재":   round(curr, dec),
                "일간":   round((curr - prev1) / prev1 * 100, 2),
                "주간":   round((curr - prev5) / prev5 * 100, 2),
            }
        except Exception:
            pass
    return result


def format_market_for_prompt(data: dict) -> str:
    """Groq에게 전달할 시장 데이터 텍스트"""
    lines = []
    groups = {
        "📈 미국 증시": ["S&P500", "나스닥", "다우", "러셀2000"],
        "😨 변동성/금리": ["VIX", "미국10년채", "미국2년채"],
        "💵 환율/달러": ["달러인덱스", "원달러", "달러엔"],
        "💢 원자재": ["금", "WTI"],
    }
    for group, keys in groups.items():
        lines.append(group)
        for k in keys:
            d = data.get(k)
            if d:
                lines.append(
                    f"  {k}: {d['현재']} "
                    f"(일간 {d['일간']:+.2f}%, 주간 {d['주간']:+.2f}%)"
                )
    return "\n".join(lines)


# ─────────────────────────────────────────
# 2. ForexFactory 캘린더
# ─────────────────────────────────────────

def fetch_ff_calendar() -> list[dict]:
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

def parse_ff_calendar(raw: list, mon: datetime.date) -> dict:
    by_date = {}
    for i in range(5):
        by_date[(mon + datetime.timedelta(days=i)).isoformat()] = []

    for ev in raw:
        try:
            dt  = datetime.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            key = dt.date().isoformat()
            if key not in by_date:
                continue
            if ev.get("impact") not in ("High", "Medium"):
                continue
            country  = ev.get("country", "")
            forecast = ev.get("forecast") or "미발표"
            previous = ev.get("previous") or "-"
            stars    = "⭐⭐⭐" if ev["impact"] == "High" else "⭐⭐"
            by_date[key].append(
                f"{COUNTRY_EMOJI.get(country,'🌐')} {ev.get('title','')} {stars} "
                f"(Consens: {forecast} / Prev: {previous})"
            )
        except Exception:
            continue
    return by_date


# ─────────────────────────────────────────
# 3. Groq 프롬프트 (핵심)
# ─────────────────────────────────────────

def build_prompt(
    mon: datetime.date,
    fri: datetime.date,
    market_data: dict,
    calendar: dict,   # {date_str: [event_str, ...]}
    has_calendar: bool,
) -> str:

    week_str    = f"{mon.year}년 {mon.month}월 {mon.day}일 ~ {fri.month}월 {fri.day}일"
    market_text = format_market_for_prompt(market_data)

    # 캘린더 섹션 구성
    cal_lines = []
    for i in range(5):
        d   = mon + datetime.timedelta(days=i)
        key = d.isoformat()
        evs = calendar.get(key, []) if has_calendar else []

        cal_lines.append(f"📌 {fmt_date(d)}:")
        cal_lines.append("  √ 경제 지표")
        if evs:
            for e in evs:
                cal_lines.append(f"    - {e}")
        else:
            cal_lines.append("    - (데이터 없음)")
        cal_lines.append("  √ 시장 휴장 / 주요 이벤트")
        cal_lines.append("    - [아래 규칙에 따라 작성]")
    cal_text = "\n".join(cal_lines)

    return f"""당신은 한국 기관투자자/VC 심사역을 위한 주간 글로벌 시장 브리핑을 작성하는 시니어 애널리스트입니다.

=== 직전주 실제 시장 데이터 (반드시 이 수치 사용) ===
{market_text}

=== 다음주 경제지표 일정 ===
{cal_text}

=== 작성 지시 ===

아래 형식으로 [{week_str}] 주간 브리핑을 한국어로 작성하세요.

**절대 금지 사항:**
- 위에 제공된 시장 데이터 외에 수치를 임의로 지어내는 것
- 경제지표의 Consensus/Previous 값을 모를 경우 추측해서 쓰는 것
- "고려할 수 있다", "포함할 수 있다" 같은 공허한 표현
- 구체성 없는 일반론적 조언

**반드시 포함할 것:**
- 실제 시장 데이터 수치를 인용한 구체적 분석
- 현재 매크로 국면(금리/달러/VIX 수준)에 대한 명확한 해석
- 한국 투자자 관점의 원달러/외국인 수급 시사점
- 다음주 주목할 구체적 섹터나 이벤트

**출력 형식:**

---
안녕하십니까 🤖 주간 시장 브리핑 봇입니다

[{week_str}] 주간 글로벌 시장 전망 및 주요 일정입니다.
[직전주 핵심 시장 흐름을 실제 수치와 함께 2-3문장으로 요약. S&P500 주간 등락률, VIX 수준, 달러/금리 방향성 포함]

━━━━━━━━━━
📅 요일별 상세 일정
━━━━━━━━━━

[각 요일별 반복 - 월~금 5일]
📌 [날짜] ([요일]): [핵심 이벤트 한줄 제목]
√ 시장 휴장
- [공휴일 있으면 표기, 없으면 (없음)]
√ 경제 지표
- [제공된 데이터 있으면 그대로 사용. 없으면 "(주요 지표 발표 없음)" 표기. 수치 절대 지어내지 말것]
√ 주요 뉴스 및 실적/이벤트
- [실제 알려진 이벤트만 작성. 모르면 (없음)]

━━━━━━━━━━
💡 투자 전략적 관점
━━━━━━━━━━
📌 변동성 관리
- [VIX {market_data.get('VIX', {}).get('현재', 'N/A')} 수준의 구체적 의미와 대응 방향]
- [금리/달러 현 레벨에서 포지션 관리 시사점]
📌 섹터별 모멘텀
- [직전주 시장 흐름에서 읽히는 구체적 섹터 방향성 - 반도체/AI/에너지 등 실제 맥락]
- [한국 증시 관련 외국인 수급/환율 영향 해석]
📌 매크로/환율 체크
- [원달러 {market_data.get('원달러', {}).get('현재', 'N/A')} 수준과 주간 변화의 시사점]
- [미국10년채 {market_data.get('미국10년채', {}).get('현재', 'N/A')}% 기준 금리 국면 해석]
- [다음주 주목할 매크로 이벤트]
---

전체 길이: 1800~2500자 사이로 작성하세요.
"""


# ─────────────────────────────────────────
# 4. 생성 + 전송
# ─────────────────────────────────────────

def generate_with_groq(prompt: str) -> str:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    msg = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3500,
        temperature=0.3,   # 낮게 유지 → hallucination 감소
    )
    return msg.choices[0].message.content


def send_telegram(text: str):
    token   = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
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

    print("  → 시장 데이터 수집 중...")
    market_data = get_market_data()
    for k, v in market_data.items():
        print(f"     {k}: {v}")

    print("  → ForexFactory 캘린더 수집 중...")
    raw_cal     = fetch_ff_calendar()
    has_cal     = len(raw_cal) > 0
    calendar    = parse_ff_calendar(raw_cal, mon) if has_cal else {}
    print(f"     캘린더: {'✅ 수집 성공' if has_cal else '❌ 실패 → 수치 없이 진행'}")

    print("  → Groq 브리핑 생성 중...")
    prompt   = build_prompt(mon, fri, market_data, calendar, has_cal)
    briefing = generate_with_groq(prompt)

    print("  → Telegram 전송 중...")
    send_telegram(briefing)

    print("✅ 완료")
    print("─" * 60)
    print(briefing)


if __name__ == "__main__":
    main()
