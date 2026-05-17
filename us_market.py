"""
us_market.py v2
────────────────
미국 시장 아침 브리핑
- 주요 지수 + 필라델피아 반도체
- 리스크온/오프 신호등
- 섹터 Top3 / Bottom3
- 빅테크 개별 등락
- 특징주 (yfinance 수치 + Groq 해설)
- 공포탐욕지수 + VIX
매일 오전 7시 KST (평일)
"""

import os
import datetime
import requests
import yfinance as yf
from groq import Groq


# ─────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────

def pct(curr, prev):
    return (curr - prev) / prev * 100

def fmt(v, c):
    arrow = "🔺" if c >= 0 else "🔻"
    return f"{v:,.2f} ({'+' if c>=0 else ''}{c:.2f}% {arrow})"

def get_price(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        if len(hist) < 2:
            return None, None
        curr = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])
        return curr, pct(curr, prev)
    except Exception:
        return None, None


# ─────────────────────────────────────────
# 1. 주요 지수
# ─────────────────────────────────────────

def get_indices():
    items = [
        ("S&P500",         "^GSPC"),
        ("나스닥",          "^IXIC"),
        ("다우",            "^DJI"),
        ("러셀2000",        "^RUT"),
        ("필라델피아반도체", "^SOX"),
    ]
    result = []
    for name, ticker in items:
        v, c = get_price(ticker)
        if v:
            result.append((name, v, c))
    return result


# ─────────────────────────────────────────
# 2. 리스크온/오프 신호등
# ─────────────────────────────────────────

def get_risk_signal():
    signals = {}

    _, c = get_price("^IXIC")
    if c is not None: signals["nasdaq"] = c

    v, c = get_price("^VIX")
    if v is not None: signals["vix_level"] = v; signals["vix_chg"] = c

    _, c = get_price("DX-Y.NYB")
    if c is not None: signals["dollar"] = c

    v, c = get_price("^TNX")
    if v is not None: signals["bond_yield"] = v; signals["bond_chg"] = c

    _, c = get_price("GC=F")
    if c is not None: signals["gold"] = c

    score = 0
    if signals.get("nasdaq", 0)   >  0: score += 1
    if signals.get("vix_chg", 0)  <  0: score += 1
    if signals.get("dollar", 0)   <  0: score += 1
    if signals.get("gold", 0)     <  0: score += 1
    if signals.get("bond_chg", 0) >  0: score -= 1

    if   score >= 3: signal, desc = "🟢 리스크온",  "나스닥↑ VIX↓ 달러↓ — 위험자산 선호"
    elif score <= 1: signal, desc = "🔴 리스크오프", "안전자산 수요↑ 위험자산 회피"
    else:            signal, desc = "🟡 혼조",       "방향성 불명확, 선별적 접근"

    return signal, desc, signals


# ─────────────────────────────────────────
# 3. 섹터 ETF
# ─────────────────────────────────────────

SECTOR_ETFS = {
    "기술":       "XLK",
    "임의소비재":  "XLY",
    "에너지":     "XLE",
    "금융":       "XLF",
    "헬스케어":   "XLV",
    "산업재":     "XLI",
    "소재":       "XLB",
    "필수소비재":  "XLP",
    "부동산":     "XLRE",
    "유틸리티":   "XLU",
    "커뮤니케이션": "XLC",
}

def get_sectors():
    results = []
    for name, ticker in SECTOR_ETFS.items():
        _, c = get_price(ticker)
        if c is not None:
            results.append((name, c))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ─────────────────────────────────────────
# 4. 빅테크
# ─────────────────────────────────────────

BIGTECH = [
    ("엔비디아",       "NVDA"),
    ("애플",           "AAPL"),
    ("마이크로소프트", "MSFT"),
    ("메타",           "META"),
    ("아마존",         "AMZN"),
    ("테슬라",         "TSLA"),
    ("알파벳",         "GOOGL"),
    ("TSMC",           "TSM"),
    ("브로드컴",       "AVGO"),
    ("AMD",            "AMD"),
]

def get_bigtech():
    results = []
    for name, ticker in BIGTECH:
        _, c = get_price(ticker)
        if c is not None:
            results.append((name, c))
    return results


# ─────────────────────────────────────────
# 5. 특징주 (수치 추출 + Groq 해설)
# ─────────────────────────────────────────

SP500_SAMPLE = [
    "NVDA","AAPL","MSFT","AMZN","GOOGL","META","TSLA","AVGO","TSM","AMD",
    "NFLX","CRM","ORCL","ADBE","QCOM","MU","INTC","ARM","PLTR","SNOW",
    "JPM","GS","MS","BAC","WFC","V","MA",
    "XOM","CVX","COP","LNG",
    "UNH","LLY","JNJ","PFE","MRNA",
    "CAT","BA","RTX","LMT","GE",
    "COST","WMT","TGT","SBUX","NKE",
]

# 기업 한줄 설명
COMPANY_DESC = {
    "NVDA":  "AI GPU 설계 1위",
    "AAPL":  "아이폰/맥 제조",
    "MSFT":  "클라우드/오피스",
    "AMZN":  "이커머스/AWS",
    "GOOGL": "구글 검색/유튜브",
    "META":  "페이스북/인스타그램",
    "TSLA":  "전기차/에너지",
    "AVGO":  "반도체/네트워킹",
    "TSM":   "파운드리(위탁생산)",
    "AMD":   "CPU/GPU 설계",
    "NFLX":  "스트리밍",
    "CRM":   "기업용 CRM",
    "ORCL":  "DB/클라우드",
    "ADBE":  "크리에이티브 소프트웨어",
    "QCOM":  "모바일 AP/통신칩",
    "MU":    "DRAM/낸드플래시",
    "INTC":  "CPU/파운드리",
    "ARM":   "반도체 IP 설계",
    "PLTR":  "AI 데이터 분석",
    "SNOW":  "클라우드 데이터",
    "JPM":   "미국 최대 은행",
    "GS":    "투자은행",
    "MS":    "투자은행/자산관리",
    "BAC":   "상업은행",
    "WFC":   "상업은행",
    "V":     "글로벌 결제 네트워크",
    "MA":    "글로벌 결제 네트워크",
    "XOM":   "석유/가스 메이저",
    "CVX":   "석유/가스 메이저",
    "COP":   "독립 석유개발사",
    "LNG":   "LNG 수출 터미널",
    "UNH":   "미국 최대 보험사",
    "LLY":   "비만/당뇨 신약",
    "JNJ":   "제약/의료기기",
    "PFE":   "글로벌 제약",
    "MRNA":  "mRNA 백신/치료제",
    "CAT":   "건설/광산 장비",
    "BA":    "항공기 제조",
    "RTX":   "방산/항공우주",
    "LMT":   "방산 1위",
    "GE":    "항공엔진",
    "COST":  "창고형 할인마트",
    "WMT":   "미국 최대 유통",
    "TGT":   "미국 대형 유통",
    "SBUX":  "글로벌 커피 체인",
    "NKE":   "글로벌 스포츠웨어",
}

def get_featured_stocks():
    """등락률 상위/하위 + 거래량 급증 종목 추출"""
    movers = []
    vol_surges = []

    for ticker in SP500_SAMPLE:
        try:
            hist = yf.Ticker(ticker).history(period="10d")
            if len(hist) < 6:
                continue

            curr     = float(hist["Close"].iloc[-1])
            prev     = float(hist["Close"].iloc[-2])
            chg      = pct(curr, prev)
            vol_today = float(hist["Volume"].iloc[-1])
            vol_avg   = float(hist["Volume"].iloc[-6:-1].mean())

            movers.append((ticker, chg))

            if vol_avg > 0 and vol_today / vol_avg >= 2.5:
                vol_surges.append((ticker, chg, vol_today / vol_avg))

        except Exception:
            continue

    movers.sort(key=lambda x: x[1], reverse=True)
    top    = movers[:5]
    bottom = movers[-5:][::-1]

    vol_surges.sort(key=lambda x: x[2], reverse=True)
    vol_top = vol_surges[:4]

    return top, bottom, vol_top


def get_groq_commentary(top, bottom, vol_top) -> str:
    """yfinance 수치를 주고 Groq에게 이유 해설 요청"""
    top_str    = ", ".join([f"{t}({c:+.1f}%)" for t, c in top])
    bottom_str = ", ".join([f"{t}({c:+.1f}%)" for t, c in bottom])
    vol_str    = ", ".join([f"{t}({c:+.1f}%, 거래량 {r:.1f}배)" for t, c, r in vol_top])

    prompt = f"""어제 미국 증시 특징주 데이터입니다.

상승 상위: {top_str}
하락 상위: {bottom_str}
거래량 급증: {vol_str}

각 종목의 주가 움직임 이유를 구체적으로 설명하세요.

규칙:
- 반드시 알고 있는 실제 이유만 작성 (추측/지어내기 금지)
- 이유를 모르는 종목은 완전히 생략
- 모든 문장은 명사형으로 끝낼 것
  좋은 예: "블랙웰 GPU 출하량 상향 조정 소식에 따른 급등"
  좋은 예: "분기 실적 EPS 컨센서스 15% 하회에 따른 급락"
  나쁜 예: "실적 호조로 상승" (너무 모호)
  나쁜 예: "시장 우려로 하락" (이유 없음)
- 구체적 수치/이벤트/촉매(catalyst)를 반드시 포함
- 종목당 1줄
- 한국어로 작성
- 형식: [티커] 구체적 설명"""

    try:
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        msg = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.2,
        )
        return msg.choices[0].message.content.strip()
    except Exception:
        return ""


# ─────────────────────────────────────────
# 6. 공포탐욕 + VIX
# ─────────────────────────────────────────

def get_fear_greed():
    try:
        data = requests.get(
            "https://api.alternative.me/fng/?limit=2", timeout=10
        ).json()["data"]
        val   = int(data[0]["value"])
        prev  = int(data[1]["value"])
        label = data[0]["value_classification"]
        diff  = val - prev

        if   val >= 75: emoji = "😍"
        elif val >= 55: emoji = "😊"
        elif val >= 45: emoji = "😐"
        elif val >= 25: emoji = "😨"
        else:           emoji = "😱"

        trend = f"({'↑' if diff>0 else '↓'}{abs(diff)})" if diff else "(변동없음)"
        return f"{emoji} {val} ({label}) {trend}"
    except Exception:
        return "N/A"


# ─────────────────────────────────────────
# 7. 메시지 조립
# ─────────────────────────────────────────

def build_message():
    kst      = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    date_str = kst.strftime("%Y-%m-%d")

    L = []
    L.append(f"🌅 *미국 시장 브리핑* — {date_str}")
    L.append("━━━━━━━━━━━━━━━━━━━━")

    # ── 주요 지수 ──
    L.append("📊 *주요 지수*")
    for name, v, c in get_indices():
        L.append(f"🔸 {name}: {fmt(v, c)}")

    # ── 리스크 신호 ──
    L.append("")
    signal, desc, raw = get_risk_signal()
    L.append(f"*시장 신호: {signal}*")
    L.append(f"└ {desc}")
    if raw.get("bond_yield"):
        L.append(
            f"└ 미국10년채 {raw['bond_yield']:.2f}% "
            f"({'+' if raw.get('bond_chg',0)>=0 else ''}{raw.get('bond_chg',0):.2f}%)"
        )

    # ── 섹터 ──
    L.append("")
    L.append("📂 *섹터 등락*")
    sectors = get_sectors()
    if sectors:
        L.append("🏆 Top 5")
        for name, c in sectors[:5]:
            L.append(f"  {'+' if c>=0 else ''}{c:.2f}%  {name}")
        L.append("💀 Bottom 5")
        for name, c in sectors[-5:]:
            L.append(f"  {c:.2f}%  {name}")

    # ── 빅테크 ──
    L.append("")
    L.append("💻 *빅테크*")
    tech = get_bigtech()
    row  = []
    for name, c in tech:
        row.append(f"{name} {'▲' if c>=0 else '▼'}{abs(c):.1f}%")
        if len(row) == 2:
            L.append("  " + "   |   ".join(row))
            row = []
    if row:
        L.append("  " + row[0])

    # ── 특징주 ──
    L.append("")
    L.append("⭐ *특징주*")
    top, bottom, vol_top = get_featured_stocks()

    if top:
        L.append("🔺 상승")
        for t, c in top:
            desc = COMPANY_DESC.get(t, "")
            desc_str = f"  _{desc}_" if desc else ""
            L.append(f"  *{t}* {'+' if c>=0 else ''}{c:.1f}%{desc_str}")
    if bottom:
        L.append("🔻 하락")
        for t, c in bottom:
            desc = COMPANY_DESC.get(t, "")
            desc_str = f"  _{desc}_" if desc else ""
            L.append(f"  *{t}* {c:.1f}%{desc_str}")
    if vol_top:
        L.append("📈 거래량 급증")
        for t, c, r in vol_top:
            desc = COMPANY_DESC.get(t, "")
            desc_str = f"  _{desc}_" if desc else ""
            L.append(f"  *{t}* {'+' if c>=0 else ''}{c:.1f}%  (평균 {r:.1f}배){desc_str}")

    # Groq 해설
    commentary = get_groq_commentary(top, bottom, vol_top)
    if commentary:
        L.append("")
        L.append("💬 *특징주 해설*")
        for line in commentary.split("\n"):
            if line.strip():
                L.append(f"  {line.strip()}")

    # ── 심리 지표 ──
    L.append("")
    L.append("🧠 *심리 지표*")
    L.append(f"공포탐욕지수: {get_fear_greed()}")
    v, c = get_price("^VIX")
    if v:
        zone = "안정" if v < 15 else "경계" if v < 20 else "위험" if v < 30 else "패닉"
        L.append(
            f"VIX: {v:.2f} ({'+' if c>=0 else ''}{c:.2f}%) → {zone} 구간"
        )

    L.append("")
    L.append(f"🕒 _{kst.strftime('%H:%M')} KST_")
    return "\n".join(L)


# ─────────────────────────────────────────
# 8. Telegram 전송
# ─────────────────────────────────────────

def send_telegram(text):
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
# 9. 메인
# ─────────────────────────────────────────

def main():
    print("미국 시장 브리핑 생성 중...")
    msg = build_message()
    print(msg)
    send_telegram(msg)
    print("✅ 완료")


if __name__ == "__main__":
    main()
