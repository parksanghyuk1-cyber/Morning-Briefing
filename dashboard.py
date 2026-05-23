"""
dashboard.py v5
────────────────
글로벌 매크로 대시보드
- yfinance: 시장 데이터 (무료)
- Gemini 2.5 Flash: 시장 코멘트 (무료)
- parse_mode HTML (본문) / plain text (AI 코멘트)
매일 오전 7시 KST 텔레그램 전송
"""
import os
import re
import math
import datetime
import requests
import yfinance as yf

# google-genai 선택적 임포트 (설치 여부 확인)
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
            # 비현실적으로 낮은 값(캐시 오류) 필터: 코스피 > 500, 코스닥 > 200
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
    """HTML 볼드"""
    return f"<b>{text}</b>"


def html_escape(text: str) -> str:
    """HTML 특수문자 이스케이프 (Telegram HTML 파싱 오류 방지)"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    """Gemini 출력에서 문장 끝마다 줄바꿈 삽입"""
    # 한국어 문장 종결 어미 뒤 공백을 줄바꿈으로 변환
    text = re.sub(r'([다요됩니다습니다니다판단전망우려확대주목\.]) ', r'\1\n', text)
    # 연속 줄바꿈 정리
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def get_ai_commentary(market_data: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "⚠️ GEMINI_API_KEY 미설정"
    if not GENAI_AVAILABLE:
        return "⚠️ google-genai 패키지 미설치 (requirements.txt 확인)"
    try:
        prompt = f"""당신은 한국 기관투자자를 위한 시장 전략가입니다.
아래 지표를 바탕으로 오늘 시장에 대한 심층 분석을 작성하세요.

{market_data}

아래 5개 항목을 각각 2~3문장으로 충분히 서술하세요:

1. 시장 국면 판단
🟢 리스크온 / 🔴 리스크오프 / 🟡 중립 중 하나를 선택하고,
오늘 지표들이 종합적으로 어떤 국면을 시사하는지 설명

2. 금리 커브 해석
단기/장기 금리 수준과 스프레드가 경기 국면에 대해 무엇을 말하는지,
성장주·가치주 밸류에이션에 어떤 영향을 주는지

3. 달러 및 원화 시사점
달러 강약이 글로벌 자금 흐름과 신흥국에 주는 의미,
원달러 환율이 외국인 수급과 국내 증시에 주는 함의

4. 원자재 매크로 신호
유가·금·구리 등 원자재 가격 움직임이 보내는 인플레이션/경기 신호,
에너지·소재 섹터 및 관련 한국 기업에 주는 시사점

5. 오늘 한국 투자자 주목 포인트
위 지표들을 종합할 때 오늘 국내 증시에서 가장 주의해야 할 것,
포지션 관리나 섹터 접근에서 고려할 사항

작성 규칙:
- 각 항목 제목은 생략하고 내용만 작성
- 항목 사이에 빈 줄 하나씩
- 수치 단순 나열 금지, 반드시 해석과 시사점 포함
- 모든 문장은 명사형으로 마무리 (예: ~우려, ~전망, ~주목, ~확대, ~판단)
- HTML 태그, 마크다운 기호, 대괄호 사용 금지
- 전문적이고 간결한 한국어"""
        raw = call_gemini(prompt, max_tokens=1500, temperature=0.5)
        return normalize_newlines(raw)
    except Exception as e:
        print(f"Gemini 오류: {e}")
        return f"⚠️ AI 코멘트 생성 실패: {str(e)[:150]}"


# ─────────────────────────────────────────
# 대시보드 조립
# ─────────────────────────────────────────
def build_dashboard() -> tuple[str, str]:
    """(본문 HTML, AI코멘트 plaintext) 튜플 반환"""
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    time_str = kst_now.strftime("%Y-%m-%d %H:%M:%S")

    L  = []   # 텔레그램 메시지 라인 (HTML)
    SL = []   # Gemini 요약용 plain text

    def add(label: str, v, c, decimals=2):
        """값이 있을 때만 라인 추가"""
        if v is None or c is None:
            return
        L.append(f"{label}: {fmt(v, c, decimals)}")
        key = label.split()[-1]  # 이모지 제거 후 마지막 단어
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
            prev = (float(h_k["Close"].dropna().iloc[-2])
                    / float(h_j["Close"].dropna().iloc[-2]))
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

    # AI 코멘트 생성
    print("  → Gemini 코멘트 생성 중...")
    commentary = get_ai_commentary("\n".join(SL))

    ai_msg = ""
    if commentary:
        lines = ["━━━━━━━━━━━━━━━━━━━━",
                 "🤖 AI 시장 코멘트", ""]
        for line in commentary.split("\n"):
            if line.strip():
                lines.append(line.strip())
        ai_msg = "\n".join(lines)

    return "\n".join(L), ai_msg


# ─────────────────────────────────────────
# Telegram 전송 (HTML 모드)
# ─────────────────────────────────────────
def split_into_chunks(text: str, limit: int = 3800) -> list[str]:
    """줄바꿈 단위로 안전하게 분할, limit 초과 시 문장 단위 추가 분할"""
    chunks = []
    current = ""
    for line in text.split("\n"):
        # 한 줄이 limit보다 길면 마침표 단위로 추가 분할
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
                print(f"[Telegram] 전송 실패 (parse_mode={parse_mode}): {resp.text[:200]}")
                # HTML 파싱 오류 시 plain text로 재시도
                resp2 = requests.post(
                    url,
                    json={"chat_id": chat_id, "text": chunk},
                    timeout=15,
                )
                if not resp2.ok:
                    print(f"[Telegram] plain text 재시도도 실패: {resp2.text[:200]}")
        except requests.RequestException as e:
            print(f"[Telegram] 네트워크 오류: {e}")


def main():
    print("대시보드 생성 중...")
    dashboard, ai_comment = build_dashboard()
    print(dashboard)
    print("\n텔레그램 전송 중...")
    send_telegram(dashboard, parse_mode="HTML")           # 본문: HTML 모드
    if ai_comment:
        send_telegram(ai_comment, parse_mode="")          # AI 코멘트: plain text
    print("✅ 완료")


if __name__ == "__main__":
    main()
