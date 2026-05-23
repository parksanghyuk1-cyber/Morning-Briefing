"""
dashboard.py v6
────────────────
글로벌 매크로 대시보드
- yfinance: 시장 데이터 (무료)
- Gemini 2.5 Flash: 시장 코멘트 (무료)
- 특이사항 자동 감지 (급등락 알림 포함)
- AI 코멘트 본문 통합 → 단일 메시지 전송
매일 오전 7시 KST 텔레그램 전송
"""
import os
import re
import datetime
import requests
import yfinance as yf

# google-genai 선택적 임포트
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

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
    except Exception as e:
        print(f"[get_price] {ticker} 오류: {e}")
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
            if prev == 0:
                continue
            if ticker == "^KS11" and curr < 500:
                continue
            if ticker == "^KQ11" and curr < 200:
                continue
            return curr, (curr - prev) / prev * 100
        except Exception as e:
            print(f"[get_kr_index] {ticker}/{p} 오류: {e}")
            continue
    return None, None


def fmt(value, chg, decimals: int = 2, comma: bool = True) -> str:
    if value is None or chg is None:
        return "N/A"
    arrow = "🔺" if chg >= 0 else "▼"
    sign  = "+" if chg >= 0 else ""
    num   = f"{value:,.{decimals}f}" if comma else f"{value:.{decimals}f}"
    return f"{num} ({sign}{chg:.2f}% {arrow})"


def b(text: str) -> str:
    return f"<b>{text}</b>"


# ─────────────────────────────────────────
# 특이사항 감지
# ─────────────────────────────────────────
# 지표별 급등락 임계값 (절대값 %)
ALERT_THRESHOLDS = {
    "^IRX": 5.0, "^TNX": 3.0, "^TYX": 3.0,   # 금리: 5bp 이상 변동도 큼 → % 기준 완화
    "DX-Y.NYB": 1.0,
    "KRW=X": 1.0,
    "^VIX": 10.0,
    "BTC-USD": 5.0, "ETH-USD": 5.0,
    "ES=F": 1.5, "NQ=F": 2.0, "YM=F": 1.5, "RTY=F": 2.0,
    "^KS11": 1.5, "^KQ11": 2.0,
    "CL=F": 3.0, "GC=F": 2.0, "HG=F": 2.0,
}
DEFAULT_THRESHOLD = 2.0  # 기타 지표

TICKER_LABELS = {
    "^IRX": "미국채 2년금리", "^TNX": "미국채 10년금리", "^TYX": "미국채 30년금리",
    "DX-Y.NYB": "달러인덱스", "KRW=X": "원/달러",
    "^VIX": "VIX(공포지수)", "BTC-USD": "비트코인", "ETH-USD": "이더리움",
    "ES=F": "S&P500선물", "NQ=F": "나스닥선물", "YM=F": "다우선물", "RTY=F": "러셀2000선물",
    "^KS11": "코스피", "^KQ11": "코스닥",
    "CL=F": "WTI유가", "GC=F": "국제금", "HG=F": "구리",
}

def detect_anomalies(data_records: list[dict]) -> list[str]:
    """
    data_records: [{"ticker": ..., "label": ..., "value": ..., "chg": ...}, ...]
    급등락 항목을 문자열 리스트로 반환
    """
    alerts = []
    for rec in data_records:
        if rec["chg"] is None:
            continue
        threshold = ALERT_THRESHOLDS.get(rec["ticker"], DEFAULT_THRESHOLD)
        if abs(rec["chg"]) >= threshold:
            direction = "급등" if rec["chg"] > 0 else "급락"
            alerts.append(
                f"{rec['label']} {direction} ({rec['chg']:+.2f}%)"
            )
    return alerts


# ─────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────
def call_gemini(prompt: str, max_tokens: int = 600, temperature: float = 0.3) -> str:
    if not GENAI_AVAILABLE:
        raise RuntimeError("google-genai 패키지가 설치되지 않았습니다.")
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


def normalize_newlines(text: str) -> str:
    text = re.sub(r'([다요됩니다습니다니다판단전망우려확대주목\.]) ', r'\1\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_ai_commentary(market_data: str, anomalies: list[str]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "⚠️ GEMINI_API_KEY 미설정"
    if not GENAI_AVAILABLE:
        return "⚠️ google-genai 패키지 미설치"

    anomaly_section = ""
    if anomalies:
        anomaly_section = "\n[오늘의 특이 급등락 항목]\n" + "\n".join(f"- {a}" for a in anomalies)

    try:
        prompt = f"""당신은 한국 기관투자자를 위한 시장 전략가입니다.
아래 지표와 특이사항을 바탕으로 오늘 시장 브리핑을 작성하세요.

[지표 데이터]
{market_data}
{anomaly_section}

아래 4개 섹션을 순서대로 작성하세요. 각 섹션 사이에 빈 줄 하나.

[섹션 1 — 오늘의 특이사항]
급등락 항목이 있으면 반드시 언급. 왜 그 지표가 움직였는지 가능한 원인(금리 결정, 지정학, 수급)과 함께 설명.
특이사항이 없으면 "특이 급등락 없음" 한 줄로 마무리.
2~4문장.

[섹션 2 — 매크로 → 증시 파급 경로]
금리·달러·원화·VIX·원자재가 국내 증시(코스피/코스닥)에 미치는 구체적 경로를 설명.
예: "10년물 금리 급등 → 성장주 밸류에이션 압박 → 코스닥 낙폭 확대 우려" 식으로.
2~3문장.

[섹션 3 — 섹터·종목 시사점]
오늘 지표 흐름상 유리한 섹터와 불리한 섹터를 각 1~2개 구체적으로 언급.
한국 상장 관련 업종이나 대표 섹터 기준으로.
2~3문장.

[섹션 4 — 오늘 대응 전략]
위 내용을 종합해 오늘 국내 투자자가 취해야 할 포지션·리스크 관리 방향을 구체적으로 제시.
리스크온/오프 판단과 함께 비중 조절 방향이나 헤지 아이디어 포함.
3~4문장.

작성 규칙:
- 섹션 제목([섹션 N — ...]) 포함해서 작성
- 수치 단순 나열 금지, 반드시 해석과 시사점 포함
- 모든 문장은 명사형으로 마무리 (예: ~우려, ~전망, ~주목, ~확대, ~판단)
- HTML 태그, 마크다운 기호 사용 금지
- 전문적이고 간결한 한국어"""

        raw = call_gemini(prompt, max_tokens=1800, temperature=0.5)
        return normalize_newlines(raw)
    except Exception as e:
        print(f"Gemini 오류: {e}")
        return f"⚠️ AI 코멘트 생성 실패: {str(e)[:150]}"


# ─────────────────────────────────────────
# 대시보드 조립
# ─────────────────────────────────────────
def build_dashboard() -> str:
    """완성된 단일 메시지 반환 (지표 + AI 코멘트 통합)"""
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    time_str = kst_now.strftime("%Y-%m-%d %H:%M:%S")

    L            = []   # 텔레그램 메시지 라인 (HTML)
    SL           = []   # Gemini 요약용 plain text
    data_records = []   # 특이사항 감지용

    def add(ticker: str, label: str, v, c, decimals=2):
        if v is None or c is None:
            return
        L.append(f"{label}: {fmt(v, c, decimals)}")
        key = label.split()[-1]
        SL.append(f"{key}: {v:.4f} ({c:+.2f}%)")
        data_records.append({
            "ticker": ticker,
            "label": TICKER_LABELS.get(ticker, key),
            "value": v,
            "chg": c,
        })

    L.append(b("🌍 글로벌 매크로 대시보드"))
    L.append(f"🕒 기준 시각: {time_str} (KST)")

    # ── 핵심 지표 ──
    L.append(""); L.append(b("🔑 핵심 지표 (금리/달러)"))
    v, c = get_price("^IRX");     add("^IRX",     "🇺🇸 미국채 2년",   v, c)
    v, c = get_price("^TNX");     add("^TNX",     "🇺🇸 미국채 10년",  v, c)
    v, c = get_price("^TYX");     add("^TYX",     "🇺🇸 미국채 30년",  v, c)
    v, c = get_price("DX-Y.NYB"); add("DX-Y.NYB", "💵 달러 인덱스",   v, c)

    # ── 주요 환율 ──
    L.append(""); L.append(b("💱 주요 환율 (FX)"))
    krw_v, krw_c = get_price("KRW=X")
    add("KRW=X", "🇰🇷 원/달러", krw_v, krw_c)

    jpy_v, _ = get_price("JPY=X")
    if krw_v and jpy_v:
        jpy_krw = krw_v / jpy_v
        try:
            h_k  = yf.Ticker("KRW=X").history(period="5d")
            h_j  = yf.Ticker("JPY=X").history(period="5d")
            prev = (float(h_k["Close"].dropna().iloc[-2])
                    / float(h_j["Close"].dropna().iloc[-2]))
            jc   = (jpy_krw - prev) / prev * 100
        except Exception:
            jc = 0.0
        L.append(f"🇯🇵 엔/원 (1엔): {fmt(jpy_krw, jc, 2)}")
        SL.append(f"엔원: {jpy_krw:.2f} ({jc:+.2f}%)")

    v, c = get_price("EURUSD=X"); add("EURUSD=X", "🇪🇺 유로/달러",  v, c, 4)
    v, c = get_price("CNY=X");    add("CNY=X",    "🇨🇳 달러/위안",  v, c, 4)

    # ── 시장 심리 & 코인 ──
    L.append(""); L.append(b("📉 시장 심리 & 코인"))
    v, c = get_price("^VIX");    add("^VIX",    "😨 VIX",    v, c)
    v, c = get_price("BTC-USD"); add("BTC-USD", "🪙 비트코인", v, c, 0)
    v, c = get_price("ETH-USD"); add("ETH-USD", "💎 이더리움", v, c, 0)

    # ── 미국 지수 선물 ──
    L.append(""); L.append(b("🇺🇸 미국 지수 선물 (Futures)"))
    v, c = get_price("ES=F");  add("ES=F",  "🇺🇸 S&P 500 선물",    v, c, 0)
    v, c = get_price("YM=F");  add("YM=F",  "🇺🇸 다우 존스 선물",  v, c, 0)
    v, c = get_price("NQ=F");  add("NQ=F",  "🇺🇸 나스닥 100 선물", v, c, 0)
    v, c = get_price("RTY=F"); add("RTY=F", "🇺🇸 러셀 2000 선물",  v, c, 0)

    # ── 한국 & 아시아 ──
    L.append(""); L.append(b("🌏 한국 & 아시아"))
    v, c = get_kr_index("^KS11")
    if v: add("^KS11", "🇰🇷 코스피", v, c, 0)
    else: L.append("🇰🇷 코스피: 데이터 없음")
    v, c = get_kr_index("^KQ11")
    if v: add("^KQ11", "🇰🇷 코스닥", v, c, 2)
    else: L.append("🇰🇷 코스닥: 데이터 없음")
    v, c = get_price("^TWII");     add("^TWII",     "🇹🇼 대만 가권",       v, c, 0)
    v, c = get_price("^SOX");      add("^SOX",      "💾 필라델피아 반도체", v, c, 0)
    v, c = get_price("^N225");     add("^N225",     "🇯🇵 니케이 225",      v, c, 0)
    v, c = get_price("000001.SS"); add("000001.SS", "🇨🇳 상해 종합",       v, c, 0)
    v, c = get_price("^HSI");      add("^HSI",      "🇭🇰 홍콩 항셍",       v, c, 0)

    # ── 원자재 & 귀금속 ──
    L.append(""); L.append(b("💢 원자재 & 귀금속"))
    v, c = get_price("CL=F"); add("CL=F", "🛢️ WTI 유가", v, c)
    v, c = get_price("HG=F"); add("HG=F", "🏗️ 구리",     v, c)
    v, c = get_price("GC=F"); add("GC=F", "🥇 국제 금",  v, c, 0)
    v, c = get_price("SI=F"); add("SI=F", "🥈 국제 은",  v, c)
    v, c = get_price("ZC=F"); add("ZC=F", "🌽 옥수수",   v, c)

    # ── 특이사항 감지 ──
    anomalies = detect_anomalies(data_records)

    if anomalies:
        L.append("")
        L.append(b("⚡ 오늘의 급등락 알림"))
        for a in anomalies:
            L.append(f"• {a}")

    # ── AI 코멘트 생성 및 통합 ──
    print("  → Gemini 코멘트 생성 중...")
    commentary = get_ai_commentary("\n".join(SL), anomalies)

    if commentary:
        L.append("")
        L.append("━━━━━━━━━━━━━━━━━━━━")
        L.append(b("🤖 AI 시장 브리핑"))
        L.append("")
        for line in commentary.split("\n"):
            stripped = line.strip()
            if not stripped:
                L.append("")
                continue
            # 섹션 헤더는 볼드 처리
            if stripped.startswith("[섹션"):
                L.append(b(stripped))
            else:
                L.append(stripped)

    return "\n".join(L)


# ─────────────────────────────────────────
# Telegram 전송 (HTML 모드)
# ─────────────────────────────────────────
def split_into_chunks(text: str, limit: int = 3800) -> list[str]:
    chunks  = []
    current = ""
    for line in text.split("\n"):
        if len(line) > limit:
            sentences = line.replace(". ", ".\n").split("\n")
        else:
            sentences = [line]
        for sentence in sentences:
            candidate = current + "\n" + sentence if current else sentence
            if len(candidate) > limit and current:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_telegram(text: str, parse_mode: str = "HTML"):
    token   = os.environ["TELEGRAM_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url     = f"https://api.telegram.org/bot{token}/sendMessage"

    for chunk in split_into_chunks(text):
        payload = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                print(f"[Telegram] 전송 실패 ({parse_mode}): {resp.text[:200]}")
                # HTML 파싱 오류 시 plain text 재시도
                resp2 = requests.post(
                    url,
                    json={"chat_id": chat_id, "text": chunk},
                    timeout=15,
                )
                if not resp2.ok:
                    print(f"[Telegram] plain text 재시도 실패: {resp2.text[:200]}")
        except requests.RequestException as e:
            print(f"[Telegram] 네트워크 오류: {e}")


def main():
    print("대시보드 생성 중...")
    message = build_dashboard()
    print(message)
    print("\n텔레그램 전송 중...")
    send_telegram(message, parse_mode="HTML")
    print("✅ 완료")


if __name__ == "__main__":
    main()
