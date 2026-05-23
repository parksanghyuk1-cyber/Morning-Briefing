"""
dashboard.py v4
────────────────
글로벌 매크로 대시보드
- yfinance: 시장 데이터 (무료)
- Gemini 2.5 Flash: 시장 코멘트 (무료)
- parse_mode HTML로 변경 (Markdown 파싱 오류 방지)
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
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty:
            return None, None
        close = hist["Close"].dropna()
        if len(close) < 2:
            return None, None
        curr = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        if prev == 0:
            return None, None
        return curr, (curr - prev) / prev * 100
    except Exception:
        return None, None


def get_kr_index(ticker: str) -> tuple:
    """한국 지수 전용 — 여러 기간 시도"""
    for p in ["2d", "5d", "10d", "1mo"]:
        try:
            hist = yf.Ticker(ticker).history(period=p)
            if hist.empty:
                continue
            close = hist["Close"].dropna()
            if len(close) < 2:
                continue
            curr = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            if prev == 0 or curr < 100:
                continue
            return curr, (curr - prev) / prev * 100
        except Exception:
            continue
    return None, None


def fmt(value, chg, decimals=2, comma=True) -> str:
    if value is None:
        return "N/A"
    arrow = "🔺" if chg >= 0 else "▼"
    sign  = "+" if chg >= 0 else ""
    num   = f"{value:,.{decimals}f}" if comma else f"{value:.{decimals}f}"
    return f"{num} ({sign}{chg:.2f}% {arrow})"


def b(text: str) -> str:
    """HTML 볼드"""
    return f"<b>{text}</b>"


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
        prompt = f"""당신은 한국 기관투자자를 위한 시장 전략가입니다.
아래 지표를 보고 오늘 시장 국면과 해석을 작성하세요.

{market_data}

출력 형식 (반드시 이 형식 그대로):

첫 줄: 시장 국면 판단
🟢 리스크온 / 🔴 리스크오프 / 🟡 중립 중 하나 선택 후 대시(—)로 핵심 근거 한 줄 작성
예: 🔴 리스크오프 — 장기금리 급등과 달러 강세가 동반되며 위험자산 회피 국면

빈 줄 하나

나머지 4~5줄: 시장 해석
- 수치 나열 절대 금지. 움직임의 의미와 투자 시사점 위주
- 금리 커브, 달러, 원자재, 선물이 보내는 매크로 신호
- 한국 투자자 입장에서 오늘 가장 주의할 포인트
- 모든 문장 명사형 마무리
- 줄바꿈으로 구분, 불릿/번호/대괄호 절대 사용 금지
- 특수기호 사용 금지 (HTML 태그, 마크다운 기호 등)
- 한국어, 전문적이고 간결한 문체"""
        return call_gemini(prompt, max_tokens=600, temperature=0.5)
    except Exception as e:
        print(f"Gemini 오류: {e}")
        return f"⚠️ AI 코멘트 생성 실패: {str(e)[:100]}"


# ─────────────────────────────────────────
# 대시보드 조립
# ─────────────────────────────────────────

def build_dashboard() -> tuple[str, str]:
    """(본문, AI코멘트) 튜플 반환"""
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    time_str = kst_now.strftime("%Y-%m-%d %H:%M:%S")

    L  = []
    SL = []

    def add(label: str, v, c, decimals=2):
        if v is None:
            return
        L.append(f"{label}: {fmt(v, c, decimals)}")
        key = label.split()[-1]
        SL.append(f"{key}: {v:.4f} ({c:+.2f}%)")

    L.append(b("🌍 글로벌 매크로 대시보드"))
    L.append(f"🕒 기준 시각: {time_str} (KST)")

    # ── 핵심 지표 ──
    L.append(""); L.append(b("🔑 핵심 지표 (금리/달러)"))
    v, c = get_price("^IRX");     add("🇺🇸 미국채 2년", v, c)
    v, c = get_price("^TNX");     add("🇺🇸 미국채 10년", v, c)
    v, c = get_price("^TYX");     add("🇺🇸 미국채 30년", v, c)
    v, c = get_price("DX-Y.NYB"); add("💵 달러 인덱스", v, c)

    # ── 주요 환율 ──
    L.append(""); L.append(b("💱 주요 환율 (FX)"))
    krw_v, krw_c = get_price("KRW=X")
    add("🇰🇷 원/달러", krw_v, krw_c)

    jpy_v, _ = get_price("JPY=X")
    if krw_v and jpy_v:
        jpy_krw = krw_v / jpy_v
        try:
            h_k  = yf.Ticker("KRW=X").history(period="5d")
            h_j  = yf.Ticker("JPY=X").history(period="5d")
            prev = float(h_k["Close"].dropna().iloc[-2]) / float(h_j["Close"].dropna().iloc[-2])
            jc   = (jpy_krw - prev) / prev * 100
        except Exception:
            jc = 0.0
        L.append(f"🇯🇵 엔/원 (1엔): {fmt(jpy_krw, jc, 2)}")
        SL.append(f"엔원: {jpy_krw:.2f} ({jc:+.2f}%)")

    v, c = get_price("EURUSD=X"); add("🇪🇺 유로/달러", v, c, 4)
    v, c = get_price("CNY=X");    add("🇨🇳 달러/위안", v, c, 4)

    # ── 시장 심리 & 코인 ──
    L.append(""); L.append(b("📉 시장 심리 & 코인"))
    v, c = get_price("^VIX");    add("😨 VIX", v, c)
    v, c = get_price("BTC-USD"); add("🪙 비트코인", v, c, 0)
    v, c = get_price("ETH-USD"); add("💎 이더리움", v, c, 0)

    # ── 미국 지수 선물 ──
    L.append(""); L.append(b("🇺🇸 미국 지수 선물 (Futures)"))
    v, c = get_price("ES=F");  add("🇺🇸 S&P 500 선물", v, c, 0)
    v, c = get_price("YM=F");  add("🇺🇸 다우 존스 선물", v, c, 0)
    v, c = get_price("NQ=F");  add("🇺🇸 나스닥 100 선물", v, c, 0)
    v, c = get_price("RTY=F"); add("🇺🇸 러셀 2000 선물", v, c, 0)

    # ── 한국 & 아시아 ──
    L.append(""); L.append(b("🌏 한국 & 아시아"))
    v, c = get_kr_index("^KS11")
    if v: add("🇰🇷 코스피", v, c, 0)
    else: L.append("🇰🇷 코스피: 데이터 없음")

    v, c = get_kr_index("^KQ11")
    if v: add("🇰🇷 코스닥", v, c, 2)
    else: L.append("🇰🇷 코스닥: 데이터 없음")

    v, c = get_price("^TWII");     add("🇹🇼 대만 가권", v, c, 0)
    v, c = get_price("^SOX");      add("💾 필라델피아 반도체", v, c, 0)
    v, c = get_price("^N225");     add("🇯🇵 니케이 225", v, c, 0)
    v, c = get_price("000001.SS"); add("🇨🇳 상해 종합", v, c, 0)
    v, c = get_price("^HSI");      add("🇭🇰 홍콩 항셍", v, c, 0)

    # ── 원자재 & 귀금속 ──
    L.append(""); L.append(b("💢 원자재 & 귀금속"))
    v, c = get_price("CL=F"); add("🛢️ WTI 유가", v, c)
    v, c = get_price("HG=F"); add("🏗️ 구리", v, c)
    v, c = get_price("GC=F"); add("🥇 국제 금", v, c, 0)
    v, c = get_price("SI=F"); add("🥈 국제 은", v, c)
    v, c = get_price("ZC=F"); add("🌽 옥수수", v, c)

    # AI 코멘트는 별도 생성
    print("  → Gemini 코멘트 생성 중...")
    commentary = get_ai_commentary("\n".join(SL))

    ai_msg = ""
    if commentary:
        lines = ["━━━━━━━━━━━━━━━━━━━━",
                 b("🤖 AI 시장 코멘트"), ""]
        for line in commentary.split("\n"):
            if line.strip():
                lines.append(line.strip())
        ai_msg = "\n".join(lines)

    return "\n".join(L), ai_msg


# ─────────────────────────────────────────
# Telegram 전송 (HTML 모드)
# ─────────────────────────────────────────

def send_telegram(text: str):
    token   = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    # 4000자 강제 절단 대신 줄바꿈 기준으로 청크 분할
    chunks = []
    current = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1은 줄바꿈
        if current_len + line_len > 3800 and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    for chunk in chunks:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
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
    dashboard, ai_comment = build_dashboard()
    print(dashboard)
    print("\n텔레그램 전송 중...")
    send_telegram(dashboard)
    if ai_comment:
        send_telegram(ai_comment)
    print("✅ 완료")


if __name__ == "__main__":
    main()
