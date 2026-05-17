"""
morning_briefing/briefing.py
────────────────────────────
매일 오전 7시 KST에 실행되는 아침 브리핑 스크립트.
- 미국 증시 / 선물 / 환율 / 금리 / VIX 수집
- 공포탐욕지수 수집
- 당일 미국 주요 경제지표 일정 수집
- 브로커리지 리포트 파일 읽기 (선택)
- Groq API로 전체 브리핑 생성 (무료)
- Telegram 전송
"""

import os
import json
import datetime
import textwrap
import requests
import yfinance as yf


# ─────────────────────────────────────────
# 1. 시장 데이터 수집
# ─────────────────────────────────────────

TICKERS = {
    "S&P500":     "^GSPC",
    "나스닥":      "^IXIC",
    "다우":        "^DJI",
    "러셀2000":    "^RUT",
    "S&P선물":     "ES=F",
    "나스닥선물":  "NQ=F",
    "VIX":         "^VIX",
    "미국2년채":   "^IRX",
    "미국10년채":  "^TNX",
    "원달러":      "KRW=X",
    "달러엔":      "JPY=X",
    "달러인덱스":  "DX-Y.NYB",
    "금":          "GC=F",
    "WTI":         "CL=F",
}

def fetch_market_data() -> dict:
    result = {}
    for name, ticker in TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) < 2:
                continue
            prev    = hist["Close"].iloc[-2]
            curr    = hist["Close"].iloc[-1]
            chg_pct = (curr - prev) / prev * 100
            result[name] = {
                "현재":   round(float(curr), 4),
                "전일":   round(float(prev), 4),
                "등락률": round(float(chg_pct), 2),
            }
        except Exception as e:
            result[name] = {"error": str(e)}
    return result


# ─────────────────────────────────────────
# 2. 공포탐욕지수
# ─────────────────────────────────────────

def fetch_fear_greed() -> dict:
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=2", timeout=10)
        data = resp.json()["data"]
        return {
            "현재값":   int(data[0]["value"]),
            "분류":     data[0]["value_classification"],
            "전일값":   int(data[1]["value"]),
            "전일분류": data[1]["value_classification"],
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────
# 3. 경제지표 일정
# ─────────────────────────────────────────

def fetch_economic_calendar() -> list:
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    try:
        url = f"https://api.tradingeconomics.com/calendar/country/united states/{today_str}/{today_str}"
        resp = requests.get(url, params={"f": "json"}, timeout=10)
        if resp.status_code != 200:
            return []
        important = []
        for e in resp.json():
            imp = e.get("Importance", 0)
            if imp and int(imp) >= 2:
                important.append({
                    "시간":   e.get("Date", "")[-8:-3],
                    "지표":   e.get("Category", ""),
                    "예상":   e.get("Forecast", "-"),
                    "이전":   e.get("Previous", "-"),
                    "중요도": "⭐" * int(imp),
                })
        return important[:8]
    except Exception:
        return []


# ─────────────────────────────────────────
# 4. 브로커리지 리포트 읽기 (선택)
# ─────────────────────────────────────────

def load_brokerage_reports(reports_dir: str = "./reports") -> str:
    if not os.path.isdir(reports_dir):
        return ""
    texts = []
    for fname in sorted(os.listdir(reports_dir)):
        if not fname.endswith((".txt", ".md")):
            continue
        fpath = os.path.join(reports_dir, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    texts.append(f"[{fname}]\n{content}")
        except Exception:
            pass
    return "\n\n".join(texts)


# ─────────────────────────────────────────
# 5. Groq API로 브리핑 생성
# ─────────────────────────────────────────

def build_prompt(market, fear_greed, calendar, reports, date_str) -> str:
    market_json   = json.dumps(market,     ensure_ascii=False, indent=2)
    fg_json       = json.dumps(fear_greed, ensure_ascii=False)
    calendar_json = json.dumps(calendar,   ensure_ascii=False, indent=2)

    report_section = ""
    if reports:
        report_section = f"\n[오늘의 브로커리지 리포트]\n{reports[:6000]}\n"

    return textwrap.dedent(f"""
        당신은 한국 VC 심사역에게 아침 시장 브리핑을 작성하는 전문 애널리스트입니다.
        아래 데이터를 바탕으로 오늘({date_str}) 아침 브리핑을 작성해주세요.

        반드시 아래 형식을 지켜주세요:

        ━━━━━━━━━━━━━━━━━━━━━━━━━━
        📊 {date_str} 아침 브리핑
        ━━━━━━━━━━━━━━━━━━━━━━━━━━

        💬 한줄 총평
        [시장 전반 분위기 1-2문장]

        📈 미국 증시
        [S&P500, 나스닥, 다우, 러셀2000 등락률과 한줄 해석]

        🔢 선물 / 변동성
        [S&P·나스닥 선물, VIX 코멘트]

        💵 환율 / 금리
        [원달러, 달러인덱스, 미국 10년채]

        😨 공포탐욕지수
        [수치와 전일 대비 변화, 한줄 해석]

        📅 오늘 주목할 지표
        [있으면 정리, 없으면 "주요 지표 없음"]
        {"📑 브로커리지 리포트 요약" if reports else ""}
        {"[핵심 투자 아이디어 3-5개]" if reports else ""}

        ━━━━━━━━━━━━━━━━━━━━━━━━━━

        규칙:
        - 숫자는 아래 데이터 기준으로만 사용 (임의 추정 금지)
        - 간결하고 전문적인 문체
        - 전체 600-900자 내외

        [시장 데이터]
        {market_json}

        [공포탐욕지수]
        {fg_json}

        [경제지표 일정]
        {calendar_json if calendar else "주요 지표 없음"}
        {report_section}
    """).strip()


def generate_briefing(prompt: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    msg = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.3,
    )
    return msg.choices[0].message.content


# ─────────────────────────────────────────
# 6. Telegram 전송
# ─────────────────────────────────────────

def send_telegram(text: str):
    token   = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    chunks  = [text[i:i+4000] for i in range(0, len(text), 4000)]
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
# 7. 메인
# ─────────────────────────────────────────

def main():
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    weekday  = ["월","화","수","목","금","토","일"][kst_now.weekday()]
    date_str = kst_now.strftime(f"%Y년 %m월 %d일 ({weekday})")

    print(f"[{kst_now.strftime('%H:%M')} KST] 브리핑 시작...")

    print("  → 시장 데이터 수집 중...")
    market = fetch_market_data()

    print("  → 공포탐욕지수 수집 중...")
    fear_greed = fetch_fear_greed()

    print("  → 경제지표 일정 수집 중...")
    calendar = fetch_economic_calendar()

    print("  → 브로커리지 리포트 로드 중...")
    reports = load_brokerage_reports("./reports")

    print("  → Groq API 브리핑 생성 중...")
    prompt   = build_prompt(market, fear_greed, calendar, reports, date_str)
    briefing = generate_briefing(prompt)

    print("  → Telegram 전송 중...")
    send_telegram(briefing)

    print("  ✅ 완료")
    print("─" * 60)
    print(briefing)


if __name__ == "__main__":
    main()
