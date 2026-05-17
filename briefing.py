"""
morning_briefing/briefing.py
────────────────────────────
매일 오전 7시 KST에 실행되는 아침 브리핑 스크립트.
- 미국 증시 / 선물 / 환율 / 금리 / VIX 수집
- 공포탐욕지수 수집
- 당일 미국 주요 경제지표 일정 수집
- 브로커리지 리포트 파일 읽기 (선택)
- Gemini API로 전체 브리핑 생성 (무료)
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
    # 지수
    "S&P500":       "^GSPC",
    "나스닥":        "^IXIC",
    "다우":          "^DJI",
    "러셀2000":      "^RUT",
    # 선물 (연속)
    "S&P선물":       "ES=F",
    "나스닥선물":    "NQ=F",
    # 변동성
    "VIX":           "^VIX",
    # 금리
    "미국2년채":     "^IRX",
    "미국10년채":    "^TNX",
    "미국30년채":    "^TYX",
    # 환율
    "원달러":        "KRW=X",
    "달러엔":        "JPY=X",
    "달러위안":      "CNY=X",
    "달러인덱스":    "DX-Y.NYB",
    # 원자재
    "금":            "GC=F",
    "WTI":           "CL=F",
}

def fetch_market_data() -> dict:
    """yfinance로 시장 데이터를 수집, 전일 대비 등락률 계산."""
    result = {}
    for name, ticker in TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d")
            if len(hist) < 2:
                continue
            prev  = hist["Close"].iloc[-2]
            curr  = hist["Close"].iloc[-1]
            chg_pct = (curr - prev) / prev * 100
            result[name] = {
                "현재": round(float(curr), 4),
                "전일": round(float(prev), 4),
                "등락률": round(float(chg_pct), 2),
            }
        except Exception as e:
            result[name] = {"error": str(e)}
    return result


def fetch_fear_greed() -> dict:
    """CNN Fear & Greed Index (alternative.me 제공)."""
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=2",
            timeout=10
        )
        data = resp.json()["data"]
        today = data[0]
        yesterday = data[1]
        return {
            "현재값":   int(today["value"]),
            "분류":     today["value_classification"],
            "전일값":   int(yesterday["value"]),
            "전일분류": yesterday["value_classification"],
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────
# 2. 경제지표 일정 수집 (Investing.com 캘린더)
# ─────────────────────────────────────────

def fetch_economic_calendar() -> list[dict]:
    """
    Stooq / investing.com 대신 FRED API (무료) 또는
    공개 캘린더 API를 사용합니다.
    여기서는 tradingeconomics 공개 엔드포인트 활용.
    API 키 없이 접근 가능한 범위에서만 수집합니다.
    """
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    try:
        # TradingEconomics 공개 캘린더 (rate limit 있음)
        url = f"https://api.tradingeconomics.com/calendar/country/united states/{today_str}/{today_str}"
        params = {"f": "json"}
        # API 키 있으면 추가: params["c"] = os.environ.get("TE_API_KEY", "")
        resp = requests.get(url, params=params, timeout=10)

        if resp.status_code != 200:
            return []

        events = resp.json()
        important = []
        for e in events:
            imp = e.get("Importance", 0)
            if imp and int(imp) >= 2:          # 중요도 2 이상만
                important.append({
                    "시간(현지)": e.get("Date", "")[-8:-3],
                    "지표": e.get("Category", ""),
                    "예상": e.get("Forecast", "-"),
                    "이전": e.get("Previous", "-"),
                    "중요도": "⭐" * int(imp),
                })
        return important[:8]  # 최대 8개
    except Exception:
        return []


# ─────────────────────────────────────────
# 3. 브로커리지 리포트 읽기 (선택)
# ─────────────────────────────────────────

def load_brokerage_reports(reports_dir: str = "./reports") -> str:
    """
    reports/ 폴더에 넣어둔 당일 리포트 텍스트 파일들을 읽어 합칩니다.
    파일이 없으면 빈 문자열 반환.

    사용법:
      - reports/report1.txt, reports/report2.txt 등으로 저장
      - GitHub Actions에서 별도 step으로 다운로드하거나
        수동으로 커밋해두면 됩니다.
    """
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
# 4. Gemini API로 브리핑 생성 (무료)
# ─────────────────────────────────────────

def build_prompt(
    market: dict,
    fear_greed: dict,
    calendar: list,
    reports: str,
    date_str: str,
) -> str:
    market_json    = json.dumps(market,    ensure_ascii=False, indent=2)
    fg_json        = json.dumps(fear_greed, ensure_ascii=False)
    calendar_json  = json.dumps(calendar,  ensure_ascii=False, indent=2)

    report_section = ""
    if reports:
        report_section = f"""
─────────────────────────────────
[오늘의 브로커리지 리포트]
{reports[:6000]}  ← 토큰 절약을 위해 6,000자 제한
─────────────────────────────────
"""

    return textwrap.dedent(f"""
        당신은 한국 VC 심사역에게 아침 시장 브리핑을 작성하는 전문 애널리스트입니다.
        아래 데이터를 바탕으로 오늘({date_str}) 아침 브리핑을 작성해주세요.

        작성 형식 (반드시 준수):
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📊 {date_str} 아침 브리핑
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        💬 한줄 총평
        [시장 전반 분위기를 1-2문장으로]

        📈 미국 증시
        [S&P500, 나스닥, 다우, 러셀2000 등락률과 한줄 해석]

        🔢 선물 / 변동성
        [S&P/나스닥 선물, VIX 수준 코멘트]

        💵 환율 / 금리
        [원달러, 달러인덱스, 미국 10년채 등 핵심 수치와 방향]

        😨 공포탐욕지수
        [수치와 전일 대비 변화, 한줄 해석]

        📅 오늘 주목할 지표
        [경제지표 일정이 있으면 정리, 없으면 "주요 지표 없음"]

        {"📑 브로커리지 리포트 요약" if reports else ""}
        {"[리포트의 핵심 투자 아이디어 3-5개를 불릿으로]" if reports else ""}

        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        규칙:
        - 숫자는 반드시 원본 데이터 기준으로만 사용 (임의 추정 금지)
        - 문체는 간결하고 전문적으로 (과도한 수식어 지양)
        - 전체 길이: 텔레그램 기준 600-900자 내외

        ─── 데이터 ───
        [시장 데이터]
        {market_json}

        [공포탐욕지수]
        {fg_json}

        [오늘 경제지표 일정]
        {calendar_json if calendar else "수집 실패 또는 주요 지표 없음"}
        {report_section}
    """).strip()


def generate_briefing(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config={"max_output_tokens": 1200, "temperature": 0.3},
    )
    response = model.generate_content(prompt)
    return response.text


# ─────────────────────────────────────────
# 5. Telegram 전송
# ─────────────────────────────────────────

def send_telegram(text: str):
    token   = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    # Telegram 메시지 4096자 제한 → 초과 시 분할
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       chunk,
                "parse_mode": "Markdown",
            },
            timeout=15,
        )
        if not resp.ok:
            # Markdown 파싱 실패 시 plain text로 재시도
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
                timeout=15,
            )


# ─────────────────────────────────────────
# 6. 메인
# ─────────────────────────────────────────

def main():
    kst_now  = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    date_str = kst_now.strftime("%Y년 %m월 %d일 (%a)").replace(
        "Mon","월").replace("Tue","화").replace("Wed","수").replace(
        "Thu","목").replace("Fri","금").replace("Sat","토").replace("Sun","일")

    print(f"[{kst_now.strftime('%H:%M')} KST] 브리핑 시작...")

    print("  → 시장 데이터 수집 중...")
    market = fetch_market_data()

    print("  → 공포탐욕지수 수집 중...")
    fear_greed = fetch_fear_greed()

    print("  → 경제지표 일정 수집 중...")
    calendar = fetch_economic_calendar()

    print("  → 브로커리지 리포트 로드 중...")
    reports = load_brokerage_reports("./reports")

    print("  → Gemini API 브리핑 생성 중...")
    prompt   = build_prompt(market, fear_greed, calendar, reports, date_str)
    briefing = generate_briefing(prompt)

    print("  → Telegram 전송 중...")
    send_telegram(briefing)

    print("  ✅ 완료")
    print("─" * 60)
    print(briefing)


if __name__ == "__main__":
    main()
