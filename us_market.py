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
# 5. 특징주 — Groq가 종목 선정 → yfinance로 수치 검증
# ─────────────────────────────────────────

def groq_pick_tickers(kst_date: str) -> list[tuple[str, str]]:
    """
    Groq에게 오늘 날짜 기준 주목할 종목 선정 요청.
    반환: [(ticker, 기업설명), ...]
    """
    prompt = f"""오늘은 {kst_date}입니다. 미국 증시 기준 어제 또는 최근 며칠간
실적 발표, 가이던스 변경, 대형 뉴스, 급등락, 거래량 급증 등으로
주목받은 종목을 골라주세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 절대 금지:
[
  {{"ticker": "NVDA", "name": "엔비디아", "desc": "AI GPU 설계", "reason": "블랙웰 수요 급증으로 실적 가이던스 상향"}},
  {{"ticker": "INTC", "name": "인텔", "desc": "CPU/파운드리", "reason": "파운드리 사업부 분기 손실 지속으로 투자심리 악화"}}
]

규칙:
- 종목 수: 7~10개 (상승/하락 혼합)
- ticker는 미국 증시 실제 티커 심볼
- reason은 구체적 수치/이벤트 포함, 명사형으로 끝낼 것
- 확실히 아는 종목만 포함 (모르면 생략)
- JSON 외 텍스트 절대 불가"""

    try:
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        msg = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.1,
        )
        import json, re
        raw = msg.choices[0].message.content.strip()
        # JSON 배열 추출
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group())
        return [(d["ticker"], d.get("name",""), d.get("desc",""), d.get("reason","")) for d in data]
    except Exception:
        return []


def verify_with_yfinance(picks: list) -> list[dict]:
    """
    Groq가 선정한 종목을 yfinance로 실제 수치 검증.
    수치 조회 실패 종목은 제외.
    """
    verified = []
    for item in picks:
        ticker, name, desc, reason = item
        v, c = get_price(ticker)
        if v is None:
            continue
        # 거래량 급증 여부
        try:
            hist = yf.Ticker(ticker).history(period="10d")
            vol_today = float(hist["Volume"].iloc[-1])
            vol_avg   = float(hist["Volume"].iloc[-6:-1].mean())
            vol_ratio = vol_today / vol_avg if vol_avg > 0 else 1.0
        except Exception:
            vol_ratio = 1.0

        verified.append({
            "ticker":    ticker,
            "name":      name,
            "desc":      desc,
            "reason":    reason,
            "price":     v,
            "chg":       c,
            "vol_ratio": vol_ratio,
        })
    return verified


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
    kst_date = kst.strftime("%Y년 %m월 %d일")
    picks    = groq_pick_tickers(kst_date)
    stocks   = verify_with_yfinance(picks)

    if stocks:
        for s in stocks:
            arrow = "🔺" if s["chg"] >= 0 else "🔻"
            vol_str = f"  _(거래량 {s['vol_ratio']:.1f}배)_" if s["vol_ratio"] >= 2.0 else ""
            L.append(
                f"{arrow} *{s['ticker']}* ({s['name']}) "
                f"{'+' if s['chg']>=0 else ''}{s['chg']:.1f}%  "
                f"_{s['desc']}_{vol_str}"
            )
            if s["reason"]:
                L.append(f"   └ {s['reason']}")
    else:
        L.append("  데이터 수집 실패")

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
