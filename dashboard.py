"""
dashboard.py
────────────
글로벌 매크로 대시보드 - yfinance 데이터만 사용 (완전 무료, AI 불필요)
매일 오전 7시 KST 텔레그램 전송
"""

import os
import datetime
import requests
import yfinance as yf


# ─────────────────────────────────────────
# 1. 데이터 수집
# ─────────────────────────────────────────

def get_price(ticker: str, period: str = "5d") -> tuple:
    """(현재가, 등락률) 반환. 실패 시 (None, None)"""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if len(hist) < 2:
            return None, None
        prev = hist["Close"].iloc[-2]
        curr = hist["Close"].iloc[-1]
        chg  = (curr - prev) / prev * 100
        return float(curr), float(chg)
    except Exception:
        return None, None


def fmt(value, chg, decimals=2, comma=True) -> str:
    """숫자 포맷 + 등락 이모지"""
    if value is None:
        return "N/A"
    arrow = "🔺" if chg >= 0 else "▼"
    sign  = "+" if chg >= 0 else ""
    num   = f"{value:,.{decimals}f}" if comma else f"{value:.{decimals}f}"
    return f"{num} ({sign}{chg:.2f}% {arrow})"


def build_dashboard() -> str:
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    time_str = kst_now.strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append("🌍 *글로벌 매크로 대시보드*")
    lines.append(f"🕒 기준 시각: {time_str} (KST)")

    # ── 핵심 지표 ──────────────────────────
    lines.append("")
    lines.append("🔑 *핵심 지표 (금리/달러)*")

    v, c = get_price("^TNX")
    lines.append(f"🇺🇸 미국채 10년 (금리): {fmt(v, c, 2)}")

    v, c = get_price("DX=F")
    lines.append(f"💵 달러 인덱스 선물: {fmt(v, c, 2)}")

    # ── 주요 환율 ──────────────────────────
    lines.append("")
    lines.append("💱 *주요 환율 (FX)*")

    krw_v, krw_c = get_price("KRW=X")
    lines.append(f"🇰🇷 원/달러: {fmt(krw_v, krw_c, 2)}")

    # 엔/원 = (원/달러) ÷ (엔/달러) → 1엔당 원화
    jpy_v, _ = get_price("JPY=X")
    if krw_v and jpy_v:
        jpy_krw_v  = krw_v / jpy_v
        jpy_v2, jpy_c2 = get_price("JPYKRW=X")
        if jpy_v2 is None:
            # 직접 계산
            prev_krw, _ = None, None
            try:
                h_krw = yf.Ticker("KRW=X").history(period="5d")
                h_jpy = yf.Ticker("JPY=X").history(period="5d")
                prev_val = h_krw["Close"].iloc[-2] / h_jpy["Close"].iloc[-2]
                jpy_c2   = (jpy_krw_v - prev_val) / prev_val * 100
            except Exception:
                jpy_c2 = 0.0
            jpy_v2 = jpy_krw_v
        lines.append(f"🇯🇵 엔/원 (1엔): {fmt(jpy_v2, jpy_c2, 2)}")
    else:
        lines.append("🇯🇵 엔/원 (1엔): N/A")

    v, c = get_price("EURUSD=X")
    lines.append(f"🇪🇺 유로/달러: {fmt(v, c, 4)}")

    v, c = get_price("CNY=X")
    lines.append(f"🇨🇳 달러/위안: {fmt(v, c, 4)}")

    # ── 시장 심리 & 코인 ────────────────────
    lines.append("")
    lines.append("📉 *시장 심리 & 코인*")

    v, c = get_price("^VIX")
    lines.append(f"😨 VIX (공포지수): {fmt(v, c, 2)}")

    v, c = get_price("BTC-USD")
    lines.append(f"🪙 비트코인: {fmt(v, c, 0)}")

    v, c = get_price("ETH-USD")
    lines.append(f"💎 이더리움: {fmt(v, c, 0)}")

    # ── 미국 지수 선물 ──────────────────────
    lines.append("")
    lines.append("🇺🇸 *미국 지수 선물 (Futures)*")

    v, c = get_price("ES=F")
    lines.append(f"🇺🇸 S&P 500 선물: {fmt(v, c, 0)}")

    v, c = get_price("YM=F")
    lines.append(f"🇺🇸 다우 존스 선물: {fmt(v, c, 0)}")

    v, c = get_price("NQ=F")
    lines.append(f"🇺🇸 나스닥 100 선물: {fmt(v, c, 0)}")

    v, c = get_price("RTY=F")
    lines.append(f"🇺🇸 러셀 2000 선물: {fmt(v, c, 0)}")

    # ── 한국 & 아시아 ───────────────────────
    lines.append("")
    lines.append("🌏 *한국 & 아시아*")

    v, c = get_price("^KS11")
    lines.append(f"🇰🇷 코스피: {fmt(v, c, 0)}")

    v, c = get_price("^KQ11")
    lines.append(f"🇰🇷 코스닥: {fmt(v, c, 2)}")

    v, c = get_price("^TWII")
    lines.append(f"🇹🇼 대만 가권: {fmt(v, c, 0)}")

    v, c = get_price("^SOX")
    lines.append(f"💾 필라델피아 반도체: {fmt(v, c, 0)}")

    v, c = get_price("^N225")
    lines.append(f"🇯🇵 니케이 225: {fmt(v, c, 0)}")

    v, c = get_price("000001.SS")
    lines.append(f"🇨🇳 상해 종합: {fmt(v, c, 0)}")

    v, c = get_price("^HSI")
    lines.append(f"🇭🇰 홍콩 항셍: {fmt(v, c, 0)}")

    # ── 원자재 & 귀금속 ────────────────────
    lines.append("")
    lines.append("💢 *원자재 & 귀금속*")

    v, c = get_price("CL=F")
    lines.append(f"🛢️ WTI 유가: {fmt(v, c, 2)}")

    v, c = get_price("HG=F")
    lines.append(f"🏗️ 구리: {fmt(v, c, 2)}")

    v, c = get_price("GC=F")
    lines.append(f"🥇 국제 금: {fmt(v, c, 0)}")

    v, c = get_price("SI=F")
    lines.append(f"🥈 국제 은: {fmt(v, c, 2)}")

    v, c = get_price("ZC=F")
    lines.append(f"🌽 옥수수: {fmt(v, c, 2)}")

    return "\n".join(lines)


# ─────────────────────────────────────────
# 2. Telegram 전송
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
# 3. 메인
# ─────────────────────────────────────────

def main():
    print("대시보드 생성 중...")
    dashboard = build_dashboard()
    print(dashboard)
    print("\n텔레그램 전송 중...")
    send_telegram(dashboard)
    print("✅ 완료")


if __name__ == "__main__":
    main()
