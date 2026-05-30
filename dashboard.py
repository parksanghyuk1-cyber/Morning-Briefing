"""
dashboard.py v7
─────────────────
글로벌 매크로 대시보드
- yfinance: 시장 데이터
- Gemini 2.5 Flash: 시장 코멘트
- 매일 오전 7시 KST 텔레그램 전송 (평일만)
"""
import os, re, datetime, requests
import yfinance as yf

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


# ── 설정 ──────────────────────────────────────────────────────────────────────

TICKER_LABELS = {
    "^IRX": "미국채 2년금리", "^TNX": "미국채 10년금리", "^TYX": "미국채 30년금리",
    "DX-Y.NYB": "달러인덱스", "KRW=X": "원/달러",
    "^VIX": "VIX", "BTC-USD": "비트코인", "ETH-USD": "이더리움",
    "ES=F": "S&P500선물", "NQ=F": "나스닥선물", "YM=F": "다우선물", "RTY=F": "러셀2000선물",
    "^KS11": "코스피", "^KQ11": "코스닥",
    "CL=F": "WTI유가", "GC=F": "국제금", "HG=F": "구리",
}

ALERT_THRESHOLDS = {
    "^IRX": 5.0, "^TNX": 3.0, "^TYX": 3.0,
    "DX-Y.NYB": 1.0, "KRW=X": 1.0,
    "^VIX": 10.0,
    "BTC-USD": 5.0, "ETH-USD": 5.0,
    "ES=F": 1.5, "NQ=F": 2.0, "YM=F": 1.5, "RTY=F": 2.0,
    "^KS11": 1.5, "^KQ11": 2.0,
    "CL=F": 3.0, "GC=F": 2.0, "HG=F": 2.0,
}
DEFAULT_THRESHOLD = 2.0
RATE_TICKERS = {"^IRX", "^TNX", "^TYX"}


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def get_price(ticker: str) -> tuple:
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        close = hist["Close"].dropna()
        if len(close) < 2:
            return None, None
        curr, prev = float(close.iloc[-1]), float(close.iloc[-2])
        if prev == 0:
            return None, None
        return curr, (curr - prev) / prev * 100
    except Exception as e:
        print(f"[get_price] {ticker}: {e}")
        return None, None


def get_rate(ticker: str) -> tuple:
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        close = hist["Close"].dropna()
        if len(close) < 2:
            return None, None
        curr, prev = float(close.iloc[-1]), float(close.iloc[-2])
        return curr, (curr - prev) * 100  # bp
    except Exception as e:
        print(f"[get_rate] {ticker}: {e}")
        return None, None


def get_kr_index(ticker: str) -> tuple:
    try:
        end   = datetime.date.today() + datetime.timedelta(days=1)
        start = end - datetime.timedelta(days=10)
        hist  = yf.Ticker(ticker).history(start=str(start), end=str(end))
        close = hist["Close"].dropna()
        min_valid = {"^KS11": 1500, "^KQ11": 400}
        close = close[close > min_valid.get(ticker, 0)]
        if len(close) < 2:
            return None, None
        curr, prev = float(close.iloc[-1]), float(close.iloc[-2])
        if prev == 0:
            return None, None
        return curr, (curr - prev) / prev * 100
    except Exception as e:
        print(f"[get_kr_index] {ticker}: {e}")
        return None, None


# ── 포맷 ──────────────────────────────────────────────────────────────────────

def fmt_pct(value, chg, decimals=2, comma=True) -> str:
    if value is None or chg is None:
        return "N/A"
    arrow = "🔺" if chg >= 0 else "▼"
    sign  = "+" if chg >= 0 else ""
    num   = f"{value:,.{decimals}f}" if comma else f"{value:.{decimals}f}"
    return f"{num} ({sign}{chg:.2f}% {arrow})"


def fmt_bp(value, bp) -> str:
    if value is None or bp is None:
        return "N/A"
    arrow = "🔺" if bp >= 0 else "▼"
    sign  = "+" if bp >= 0 else ""
    return f"{value:.2f}% ({sign}{bp:.1f}bp {arrow})"


def b(text: str) -> str:
    return f"<b>{text}</b>"


# ── 특이사항 감지 ─────────────────────────────────────────────────────────────

def detect_anomalies(records: list[dict]) -> list[str]:
    alerts = []
    for r in records:
        if r["chg"] is None:
            continue
        if r["ticker"] in RATE_TICKERS:
            if abs(r["chg"]) >= 10:
                direction = "급등" if r["chg"] > 0 else "급락"
                alerts.append(f"{r['label']} {direction} ({r['chg']:+.1f}bp)")
        else:
            threshold = ALERT_THRESHOLDS.get(r["ticker"], DEFAULT_THRESHOLD)
            if abs(r["chg"]) >= threshold:
                direction = "급등" if r["chg"] > 0 else "급락"
                alerts.append(f"{r['label']} {direction} ({r['chg']:+.2f}%)")
    return alerts


# ── Gemini ────────────────────────────────────────────────────────────────────

def get_ai_commentary(market_data: str, anomalies: list[str]) -> str:
    if not os.environ.get("GEMINI_API_KEY"):
        return "⚠️ GEMINI_API_KEY 미설정"
    if not GENAI_AVAILABLE:
        return "⚠️ google-genai 패키지 미설치"

    anomaly_section = (
        "\n[오늘의 특이 급등락 항목]\n" + "\n".join(f"- {a}" for a in anomalies)
        if anomalies else ""
    )

    prompt = f"""당신은 한국 기관투자자를 위한 시장 전략가입니다.
아래 지표와 특이사항을 바탕으로 오늘 시장 브리핑을 작성하세요.

[지표 데이터]
{market_data}
{anomaly_section}

아래 4개 섹션을 순서대로 작성하세요. 각 섹션 사이에 빈 줄 하나.
각 섹션은 반드시 2문장 이내로 간결하게 작성.

[섹션 1 — 오늘의 특이사항]
급등락 항목이 있으면 지표명, 변동폭, 가능한 원인(금리 결정, 지정학, 수급) 포함.
특이사항이 없으면 "특이 급등락 없음" 한 줄.

[섹션 2 — 매크로 → 증시 파급 경로]
금리, 달러, 원화, VIX가 코스피/코스닥에 미치는 구체적 인과 경로를 화살표(→) 형식으로.

[섹션 3 — 섹터 시사점]
유리한 섹터 1개, 불리한 섹터 1개를 한국 상장 업종 기준으로 명시.

[섹션 4 — 오늘 대응 전략]
🟢/🔴/🟡 리스크 판단 명시 후 비중 조절, 헤지 방향을 2문장 이내로.

작성 규칙:
- 섹션 제목([섹션 N — ...]) 반드시 포함
- 수치 단순 나열 금지, 해석 포함
- 모든 문장 명사형 마무리 (~우려, ~전망, ~주목, ~판단)
- HTML 태그, 마크다운 기호 절대 금지
- 전문적 간결한 한국어"""

    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=8192, temperature=0.5),
        )
        return re.sub(r'\n{3,}', '\n\n', resp.text.strip())
    except Exception as e:
        print(f"Gemini 오류: {e}")
        return f"⚠️ AI 코멘트 생성 실패: {str(e)[:150]}"


# ── 대시보드 조립 ─────────────────────────────────────────────────────────────

def build_dashboard() -> str:
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    time_str = kst_now.strftime("%Y-%m-%d %H:%M:%S")

    L       = []
    SL      = []
    records = []

    def add(ticker: str, label: str, v, c, decimals=2, is_rate=False):
        if v is None or c is None:
            return
        line = f"{label}: " + (fmt_bp(v, c) if is_rate else fmt_pct(v, c, decimals))
        L.append(line)
        SL.append(f"{TICKER_LABELS.get(ticker, label)}: {v:.4f} ({c:+.2f})")
        records.append({"ticker": ticker, "label": TICKER_LABELS.get(ticker, label), "value": v, "chg": c})

    L.append(b("🌍 글로벌 매크로 대시보드"))
    L.append(f"🕒 기준 시각: {time_str} (KST)")

    L.append(""); L.append(b("🔑 핵심 지표 (금리/달러)"))
    for ticker, label in [("^IRX", "🇺🇸 미국채 2년"), ("^TNX", "🇺🇸 미국채 10년"), ("^TYX", "🇺🇸 미국채 30년")]:
        add(ticker, label, *get_rate(ticker), is_rate=True)
    add("DX-Y.NYB", "💵 달러 인덱스", *get_price("DX-Y.NYB"))

    L.append(""); L.append(b("💱 주요 환율 (FX)"))
    krw_v, krw_c = get_price("KRW=X")
    add("KRW=X", "🇰🇷 원/달러", krw_v, krw_c)

    jpy_v, _ = get_price("JPY=X")
    if krw_v and jpy_v:
        jpy_krw = krw_v / jpy_v
        jpy_chg = None
        try:
            h_k = yf.Ticker("KRW=X").history(period="5d")["Close"].dropna()
            h_j = yf.Ticker("JPY=X").history(period="5d")["Close"].dropna()
            prev = float(h_k.iloc[-2]) / float(h_j.iloc[-2])
            jpy_chg = (jpy_krw - prev) / prev * 100
        except Exception:
            pass
        if jpy_chg is not None:
            L.append(f"🇯🇵 엔/원 (1엔): {fmt_pct(jpy_krw, jpy_chg, 2)}")
            SL.append(f"엔원: {jpy_krw:.2f} ({jpy_chg:+.2f}%)")

    add("EURUSD=X", "🇪🇺 유로/달러", *get_price("EURUSD=X"), decimals=4)
    add("CNY=X",    "🇨🇳 달러/위안", *get_price("CNY=X"),    decimals=4)

    L.append(""); L.append(b("📉 시장 심리 & 코인"))
    add("^VIX",    "😨 VIX",    *get_price("^VIX"))
    add("BTC-USD", "🪙 비트코인", *get_price("BTC-USD"), decimals=0)
    add("ETH-USD", "💎 이더리움", *get_price("ETH-USD"), decimals=0)

    L.append(""); L.append(b("🇺🇸 미국 지수 선물"))
    add("ES=F",  "S&P 500 선물",    *get_price("ES=F"),  decimals=0)
    add("YM=F",  "다우 존스 선물",  *get_price("YM=F"),  decimals=0)
    add("NQ=F",  "나스닥 100 선물", *get_price("NQ=F"),  decimals=0)
    add("RTY=F", "러셀 2000 선물",  *get_price("RTY=F"), decimals=0)

    L.append(""); L.append(b("🌏 한국 & 아시아"))
    v, c = get_kr_index("^KS11")
    add("^KS11", "🇰🇷 코스피", v, c, decimals=0) if v else L.append("🇰🇷 코스피: 데이터 없음")
    v, c = get_kr_index("^KQ11")
    add("^KQ11", "🇰🇷 코스닥", v, c, decimals=2) if v else L.append("🇰🇷 코스닥: 데이터 없음")
    add("^TWII",     "🇹🇼 대만 가권",       *get_price("^TWII"),     decimals=0)
    add("^SOX",      "💾 필라델피아 반도체", *get_price("^SOX"),      decimals=0)
    add("^N225",     "🇯🇵 니케이 225",      *get_price("^N225"),     decimals=0)
    add("000001.SS", "🇨🇳 상해 종합",       *get_price("000001.SS"), decimals=0)
    add("^HSI",      "🇭🇰 홍콩 항셍",       *get_price("^HSI"),      decimals=0)

    L.append(""); L.append(b("💢 원자재 & 귀금속"))
    add("CL=F", "🛢️ WTI 유가", *get_price("CL=F"))
    add("HG=F", "🏗️ 구리",     *get_price("HG=F"))
    add("GC=F", "🥇 국제 금",  *get_price("GC=F"), decimals=0)
    add("SI=F", "🥈 국제 은",  *get_price("SI=F"))
    add("ZC=F", "🌽 옥수수",   *get_price("ZC=F"))

    anomalies = detect_anomalies(records)
    if anomalies:
        L.append(""); L.append(b("⚡ 오늘의 급등락 알림"))
        L.extend(f"• {a}" for a in anomalies)

    print("  → Gemini 코멘트 생성 중...")
    commentary = get_ai_commentary("\n".join(SL), anomalies)

    ai_lines = [b("🤖 AI 시장 브리핑"), ""]
    for line in commentary.split("\n"):
        stripped = line.strip()
        ai_lines.append(b(stripped) if stripped.startswith("[섹션") else stripped)

    return "\n".join(L) + "\n\n" + "\n".join(ai_lines)


# ── Telegram 전송 ─────────────────────────────────────────────────────────────

def split_chunks(text: str, limit: int = 4096) -> list[str]:
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_telegram(text: str):
    token   = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[Telegram] 환경변수 TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 미설정")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_chunks(text):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                print(f"[Telegram] HTML 전송 실패: {resp.text[:200]}")
                resp2 = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=15)
                if not resp2.ok:
                    print(f"[Telegram] plain text 재시도 실패: {resp2.text[:200]}")
        except requests.RequestException as e:
            print(f"[Telegram] 네트워크 오류: {e}")


def main():
    print("대시보드 생성 중...")
    msg = build_dashboard()
    print(msg)
    print("\n텔레그램 전송 중...")
    send_telegram(msg)
    print("✅ 완료")


if __name__ == "__main__":
    main()
