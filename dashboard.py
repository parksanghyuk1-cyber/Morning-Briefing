"""
dashboard.py v3
────────────────
글로벌 매크로 대시보드
- yfinance: 시장 데이터 (무료)
- Gemini 2.0 Flash: 시장 코멘트 (무료)
매일 오전 7시 KST 텔레그램 전송
"""

import os
import math
import datetime
import requests
import yfinance as yf


# ─────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────

def get_price(ticker: str, period: str = "5d") -> tuple:
    """(현재가, 등락률) 반환. NaN/실패 시 (None, None)"""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if len(hist) < 2:
            return None, None
        prev = float(hist["Close"].iloc[-2])
        curr = float(hist["Close"].iloc[-1])
        if math.isnan(curr) or math.isnan(prev) or prev == 0:
            return None, None
        return curr, (curr - prev) / prev * 100
    except Exception:
        return None, None


def fmt(value, chg, decimals=2, comma=True) -> str:
    if value is None or math.isnan(value):
        return "N/A"
    arrow = "🔺" if chg >= 0 else "▼"
    sign  = "+" if chg >= 0 else ""
    num   = f"{value:,.{decimals}f}" if comma else f"{value:.{decimals}f}"
    return f"{num} ({sign}{chg:.2f}% {arrow})"


# ─────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────

def call_gemini(prompt: str, max_tokens: int = 600, temperature: float = 0.3) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    return resp.text.strip()


def get_ai_commentary(market_data: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "⚠️ GEMINI_API_KEY 미설정"
    try:
        prompt = f"""아래는 오늘 글로벌 시장 주요 지표입니다.

{market_data}

이 데이터를 종합해 오늘 장 전반에 대한 코멘트를 작성하세요.

규칙:
- 4~6줄 이내
- 모든 문장은 명사형으로 끝낼 것 (예: ~우세, ~확대, ~주목, ~전망)
- 실제 수치를 인용해 근거 제시
- 아래 순서로 작성:
  1) 리스크온/오프 판단 (근거 포함)
  2) 금리/달러 방향성 해석
  3) 원화 및 한국 증시 시사점
  4) 오늘 주목할 포인트
- 과도한 수식어 지양, 전문적 문체
- 한국어로 작성
- 줄바꿈으로 구분, 번호나 불릿 없이"""
        return call_gemini(prompt, max_tokens=500, temperature=0.3)
    except Exception as e:
        print(f"Gemini 오류: {e}")
        return f"⚠️ AI 코멘트 생성 실패: {e}"


# ─────────────────────────────────────────
# 대시보드 조립
# ─────────────────────────────────────────

def build_dashboard() -> str:
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    time_str = kst_now.strftime("%Y-%m-%d %H:%M:%S")

    L  = []   # 출력 라인
    SL = []   # Gemini에 넘길 요약

    def add(label: str, v, c, decimals=2):
        """값이 있으면 출력 + 요약 추가, None이면 줄 생략"""
        if v is None:
            return
        L.append(f"{label}: {fmt(v, c, decimals)}")
        SL.append(f"{label.split()[-1].replace('*','')}: {v:.4f} ({c:+.2f}%)")

    L.append("🌍 *글로벌 매크로 대시보드*")
    L.append(f"🕒 기준 시각: {time_str} (KST)")

    # ── 핵심 지표 ──
    L.append(""); L.append("🔑 *핵심 지표 (금리/달러)*")
    v, c = get_price("^IRX");     add("🇺🇸 미국채 2년", v, c)
    v, c = get_price("^TNX");     add("🇺🇸 미국채 10년", v, c)
    v, c = get_price("^TYX");     add("🇺🇸 미국채 30년", v, c)
    v, c = get_price("DX-Y.NYB"); add("💵 달러 인덱스", v, c)

    # ── 주요 환율 ──
    L.append(""); L.append("💱 *주요 환율 (FX)*")
    krw_v, krw_c = get_price("KRW=X")
    add("🇰🇷 원/달러", krw_v, krw_c)

    jpy_v, _ = get_price("JPY=X")
    if krw_v and jpy_v:
        jpy_krw = krw_v / jpy_v
        try:
            h_k  = yf.Ticker("KRW=X").history(period="5d")
            h_j  = yf.Ticker("JPY=X").history(period="5d")
            prev = float(h_k["Close"].iloc[-2]) / float(h_j["Close"].iloc[-2])
            jc   = (jpy_krw - prev) / prev * 100
        except Exception:
            jc = 0.0
        if not math.isnan(jpy_krw):
            L.append(f"🇯🇵 엔/원 (1엔): {fmt(jpy_krw, jc, 2)}")
            SL.append(f"엔원: {jpy_krw:.2f} ({jc:+.2f}%)")

    v, c = get_price("EURUSD=X"); add("🇪🇺 유로/달러", v, c, 4)
    v, c = get_price("CNY=X");    add("🇨🇳 달러/위안", v, c, 4)

    # ── 시장 심리 & 코인 ──
    L.append(""); L.append("📉 *시장 심리 & 코인*")
    v, c = get_price("^VIX");    add("😨 VIX", v, c)
    v, c = get_price("BTC-USD"); add("🪙 비트코인", v, c, 0)
    v, c = get_price("ETH-USD"); add("💎 이더리움", v, c, 0)

    # ── 미국 지수 선물 ──
    L.append(""); L.append("🇺🇸 *미국 지수 선물 (Futures)*")
    v, c = get_price("ES=F");  add("🇺🇸 S&P 500 선물", v, c, 0)
    v, c = get_price("YM=F");  add("🇺🇸 다우 존스 선물", v, c, 0)
    v, c = get_price("NQ=F");  add("🇺🇸 나스닥 100 선물", v, c, 0)
    v, c = get_price("RTY=F"); add("🇺🇸 러셀 2000 선물", v, c, 0)

    # ── 한국 & 아시아 ──
    L.append(""); L.append("🌏 *한국 & 아시아*")
    v, c = get_price("^KS11")
    if v:
        add("🇰🇷 코스피", v, c, 0)
    else:
        L.append("🇰🇷 코스피: 장중 확인 필요")

    v, c = get_price("^KQ11")
    if v:
        add("🇰🇷 코스닥", v, c, 2)
    else:
        L.append("🇰🇷 코스닥: 장중 확인 필요")

    v, c = get_price("^TWII");     add("🇹🇼 대만 가권", v, c, 0)
    v, c = get_price("^SOX");      add("💾 필라델피아 반도체", v, c, 0)
    v, c = get_price("^N225");     add("🇯🇵 니케이 225", v, c, 0)
    v, c = get_price("000001.SS"); add("🇨🇳 상해 종합", v, c, 0)
    v, c = get_price("^HSI");      add("🇭🇰 홍콩 항셍", v, c, 0)

    # ── 원자재 & 귀금속 ──
    L.append(""); L.append("💢 *원자재 & 귀금속*")
    v, c = get_price("CL=F"); add("🛢️ WTI 유가", v, c)
    v, c = get_price("HG=F"); add("🏗️ 구리", v, c)
    v, c = get_price("GC=F"); add("🥇 국제 금", v, c, 0)
    v, c = get_price("SI=F"); add("🥈 국제 은", v, c)
    v, c = get_price("ZC=F"); add("🌽 옥수수", v, c)

    # ── Gemini 코멘트 ──
    print("  → Gemini 코멘트 생성 중...")
    commentary = get_ai_commentary("\n".join(SL))
    if commentary:
        L.append("")
        L.append("━━━━━━━━━━━━━━━━━━━━")
        L.append("🤖 *AI 시장 코멘트*")
        L.append("")
        for line in commentary.split("\n"):
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


# ─────────────────────────────────────────
# 메인
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
