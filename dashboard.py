"""
dashboard.py v2
────────────────
글로벌 매크로 대시보드
- yfinance: 시장 데이터 (무료)
- Groq: 전체 지표 기반 시장 평가 (무료)
매일 오전 7시 KST 텔레그램 전송
"""

import os
import datetime
import requests
import yfinance as yf


def get_price(ticker: str, period: str = "5d") -> tuple:
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if len(hist) < 2:
            return None, None
        prev = hist["Close"].iloc[-2]
        curr = hist["Close"].iloc[-1]
        return float(curr), float((curr - prev) / prev * 100)
    except Exception:
        return None, None


def fmt(value, chg, decimals=2, comma=True) -> str:
    if value is None:
        return "N/A"
    arrow = "🔺" if chg >= 0 else "▼"
    sign  = "+" if chg >= 0 else ""
    num   = f"{value:,.{decimals}f}" if comma else f"{value:.{decimals}f}"
    return f"{num} ({sign}{chg:.2f}% {arrow})"


# ─────────────────────────────────────────
# Groq 시장 평가
# ─────────────────────────────────────────


def call_gemini(prompt: str, max_tokens: int = 1200, temperature: float = 0.3) -> str:
    """Gemini 2.0 Flash API 호출 (무료)"""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    return response.text.strip()

def get_groq_assessment(summary: str) -> str:
    try:
        
        return call_gemini(prompt, max_tokens=400, temperature=0.3)
    except Exception:
        return ""


# ─────────────────────────────────────────
# 대시보드 조립
# ─────────────────────────────────────────

def build_dashboard() -> str:
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    time_str = kst_now.strftime("%Y-%m-%d %H:%M:%S")

    L  = []   # 출력 라인
    SL = []   # Groq에 넘길 요약

    L.append("🌍 *글로벌 매크로 대시보드*")
    L.append(f"🕒 기준 시각: {time_str} (KST)")

    # ── 핵심 지표 ──
    L.append(""); L.append("🔑 *핵심 지표 (금리/달러)*")

    v, c = get_price("^IRX")
    L.append(f"🇺🇸 미국채 2년: {fmt(v, c, 2)}")
    if v: SL.append(f"미국채 2년: {v:.2f}% ({c:+.2f}%)")

    v, c = get_price("^TNX")
    L.append(f"🇺🇸 미국채 10년: {fmt(v, c, 2)}")
    if v: SL.append(f"미국채 10년: {v:.2f}% ({c:+.2f}%)")

    v, c = get_price("^TYX")
    L.append(f"🇺🇸 미국채 30년: {fmt(v, c, 2)}")
    if v: SL.append(f"미국채 30년: {v:.2f}% ({c:+.2f}%)")

    v, c = get_price("DX-Y.NYB")
    L.append(f"💵 달러 인덱스: {fmt(v, c, 2)}")
    if v: SL.append(f"달러인덱스: {v:.2f} ({c:+.2f}%)")

    # ── 주요 환율 ──
    L.append(""); L.append("💱 *주요 환율 (FX)*")

    krw_v, krw_c = get_price("KRW=X")
    L.append(f"🇰🇷 원/달러: {fmt(krw_v, krw_c, 2)}")
    if krw_v: SL.append(f"원달러: {krw_v:.2f} ({krw_c:+.2f}%)")

    jpy_v, _ = get_price("JPY=X")
    if krw_v and jpy_v:
        jpy_krw = krw_v / jpy_v
        try:
            h_k = yf.Ticker("KRW=X").history(period="5d")
            h_j = yf.Ticker("JPY=X").history(period="5d")
            prev = h_k["Close"].iloc[-2] / h_j["Close"].iloc[-2]
            jc   = (jpy_krw - prev) / prev * 100
        except Exception:
            jc = 0.0
        L.append(f"🇯🇵 엔/원 (1엔): {fmt(jpy_krw, jc, 2)}")
    else:
        L.append("🇯🇵 엔/원 (1엔): N/A")

    v, c = get_price("EURUSD=X")
    L.append(f"🇪🇺 유로/달러: {fmt(v, c, 4)}")

    v, c = get_price("CNY=X")
    L.append(f"🇨🇳 달러/위안: {fmt(v, c, 4)}")

    # ── 시장 심리 & 코인 ──
    L.append(""); L.append("📉 *시장 심리 & 코인*")

    v, c = get_price("^VIX")
    L.append(f"😨 VIX (공포지수): {fmt(v, c, 2)}")
    if v: SL.append(f"VIX: {v:.2f} ({c:+.2f}%)")

    v, c = get_price("BTC-USD")
    L.append(f"🪙 비트코인: {fmt(v, c, 0)}")
    if v: SL.append(f"비트코인: {v:,.0f} ({c:+.2f}%)")

    v, c = get_price("ETH-USD")
    L.append(f"💎 이더리움: {fmt(v, c, 0)}")

    # ── 미국 지수 선물 ──
    L.append(""); L.append("🇺🇸 *미국 지수 선물 (Futures)*")

    v, c = get_price("ES=F")
    L.append(f"🇺🇸 S&P 500 선물: {fmt(v, c, 0)}")
    if v: SL.append(f"S&P선물: {v:,.0f} ({c:+.2f}%)")

    v, c = get_price("YM=F")
    L.append(f"🇺🇸 다우 존스 선물: {fmt(v, c, 0)}")

    v, c = get_price("NQ=F")
    L.append(f"🇺🇸 나스닥 100 선물: {fmt(v, c, 0)}")
    if v: SL.append(f"나스닥선물: {v:,.0f} ({c:+.2f}%)")

    v, c = get_price("RTY=F")
    L.append(f"🇺🇸 러셀 2000 선물: {fmt(v, c, 0)}")

    # ── 한국 & 아시아 ──
    L.append(""); L.append("🌏 *한국 & 아시아*")

    v, c = get_price("^KS11")
    L.append(f"🇰🇷 코스피: {fmt(v, c, 0)}")

    v, c = get_price("^KQ11")
    L.append(f"🇰🇷 코스닥: {fmt(v, c, 2)}")

    v, c = get_price("^TWII")
    L.append(f"🇹🇼 대만 가권: {fmt(v, c, 0)}")

    v, c = get_price("^SOX")
    L.append(f"💾 필라델피아 반도체: {fmt(v, c, 0)}")
    if v: SL.append(f"필라델피아반도체: {v:,.0f} ({c:+.2f}%)")

    v, c = get_price("^N225")
    L.append(f"🇯🇵 니케이 225: {fmt(v, c, 0)}")

    v, c = get_price("000001.SS")
    L.append(f"🇨🇳 상해 종합: {fmt(v, c, 0)}")

    v, c = get_price("^HSI")
    L.append(f"🇭🇰 홍콩 항셍: {fmt(v, c, 0)}")

    # ── 원자재 & 귀금속 ──
    L.append(""); L.append("💢 *원자재 & 귀금속*")

    v, c = get_price("CL=F")
    L.append(f"🛢️ WTI 유가: {fmt(v, c, 2)}")
    if v: SL.append(f"WTI: {v:.2f} ({c:+.2f}%)")

    v, c = get_price("HG=F")
    L.append(f"🏗️ 구리: {fmt(v, c, 2)}")
    if v: SL.append(f"구리: {v:.2f} ({c:+.2f}%)")

    v, c = get_price("GC=F")
    L.append(f"🥇 국제 금: {fmt(v, c, 0)}")
    if v: SL.append(f"금: {v:,.0f} ({c:+.2f}%)")

    v, c = get_price("SI=F")
    L.append(f"🥈 국제 은: {fmt(v, c, 2)}")

    v, c = get_price("ZC=F")
    L.append(f"🌽 옥수수: {fmt(v, c, 2)}")

    # ── AI 시장 평가 ──
    assessment = get_groq_assessment("\n".join(SL))
    if assessment:
        L.append("")
        L.append("━━━━━━━━━━━━━━━━━━━━")
        L.append("🤖 *AI 시장 평가*")
        for line in assessment.split("\n"):
            if line.strip():
                L.append(line.strip())

    return "\n".join(L)


# ─────────────────────────────────────────
# Telegram 전송
# ─────────────────────────────────────────

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


def main():
    print("대시보드 생성 중...")
    dashboard = build_dashboard()
    print(dashboard)
    send_telegram(dashboard)
    print("✅ 완료")


if __name__ == "__main__":
    main()
