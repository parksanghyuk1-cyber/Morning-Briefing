"""us_market.py v3 - 미국 시장 아침 브리핑 (매일 07:00 KST)"""

import os, re, json, datetime, requests
import yfinance as yf
from google import genai
from google.genai import types

# ── 설정 ──────────────────────────────────
INDICES  = [("S&P500","^GSPC"),("나스닥","^IXIC"),("다우","^DJI"),("러셀2000","^RUT"),("필라델피아반도체","^SOX")]
SECTORS  = {"기술":"XLK","임의소비재":"XLY","에너지":"XLE","금융":"XLF","헬스케어":"XLV","산업재":"XLI","소재":"XLB","필수소비재":"XLP","부동산":"XLRE","유틸리티":"XLU","커뮤니케이션":"XLC"}
BIGTECH  = [("엔비디아","NVDA"),("애플","AAPL"),("마이크로소프트","MSFT"),("메타","META"),("아마존","AMZN"),("테슬라","TSLA"),("알파벳","GOOGL"),("TSMC","TSM"),("브로드컴","AVGO"),("AMD","AMD")]

# ── 공통 ──────────────────────────────────
def get_price(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d")
        if len(h) < 2 or h.index[-1].date() == h.index[-2].date(): return None, None
        c, p = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
        return c, (c - p) / p * 100
    except: return None, None

def fmt(v, c):
    return f"{v:,.2f} ({'+' if c>=0 else ''}{c:.2f}% {'🔺' if c>=0 else '🔻'})"

def call_gemini(prompt, max_tokens=1200, temperature=0.3):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_tokens, temperature=temperature)
    ).text.strip()

# ── 섹션별 데이터 ──────────────────────────
def get_risk_signal():
    data = {k: get_price(t) for k, t in [("nasdaq","^IXIC"),("vix","^VIX"),("dollar","DX-Y.NYB"),("bond","^TNX"),("gold","GC=F")]}
    s = sum([
        (data["nasdaq"][1] or 0)  >  0,
        (data["vix"][1]    or 0)  <  0,
        (data["dollar"][1] or 0)  <  0,
        (data["gold"][1]   or 0)  <  0,
        -((data["bond"][1] or 0)  >  0),
    ])
    if   s >= 3: sig, desc = "🟢 리스크온",  "나스닥↑ VIX↓ 달러↓ - 위험자산 선호"
    elif s <= 1: sig, desc = "🔴 리스크오프", "안전자산 수요↑ 위험자산 회피"
    else:        sig, desc = "🟡 혼조",       "방향성 불명확, 선별적 접근"
    return sig, desc, data["bond"]

def get_fear_greed():
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=2", timeout=10).json()["data"]
        v, p = int(d[0]["value"]), int(d[1]["value"])
        emoji = "😍" if v>=75 else "😊" if v>=55 else "😐" if v>=45 else "😨" if v>=25 else "😱"
        diff  = v - p
        return f"{emoji} {v} ({d[0]['value_classification']}) ({'↑' if diff>0 else '↓'}{abs(diff)})"
    except: return "N/A"

def pick_stocks(kst_date):
    prompt = f"""오늘은 {kst_date}입니다. 미국 증시 기준 최근 주목받은 종목을 아래 JSON으로만 응답하세요. 다른 텍스트 절대 금지.
[{{"ticker":"NVDA","name":"엔비디아","desc":"AI GPU","reason":"블랙웰 수요 급증으로 가이던스 상향"}}]
규칙: 7~10개, 상승/하락 혼합, reason은 구체적 수치/이벤트 포함 명사형, 확실한 종목만"""
    try:
        raw = call_gemini(prompt, max_tokens=800, temperature=0.1)
        m   = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m: return []
        return [(d["ticker"], d.get("name",""), d.get("desc",""), d.get("reason","")) for d in json.loads(m.group())]
    except: return []

def verify_stocks(picks):
    result = []
    for ticker, name, desc, reason in picks:
        v, c = get_price(ticker)
        if v is None: continue
        try:
            h = yf.Ticker(ticker).history(period="10d")
            vol_ratio = float(h["Volume"].iloc[-1]) / float(h["Volume"].iloc[-6:-1].mean())
        except: vol_ratio = 1.0
        result.append({"ticker":ticker,"name":name,"desc":desc,"reason":reason,"chg":c,"vol_ratio":vol_ratio})
    return result

# ── 메시지 조립 ────────────────────────────
def build_message():
    kst = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    L   = [f"🌅 *미국 시장 브리핑* - {kst.strftime('%Y-%m-%d')}", "━━━━━━━━━━━━━━━━━━━━"]

    L.append("📊 *주요 지수*")
    for name, t in INDICES:
        v, c = get_price(t)
        if v: L.append(f"🔸 {name}: {fmt(v, c)}")

    sig, desc, bond = get_risk_signal()
    L += ["", f"*시장 신호: {sig}*", f"└ {desc}"]
    if bond[0]: L.append(f"└ 미국10년채 {bond[0]:.2f}% ({'+' if bond[1]>=0 else ''}{bond[1]:.2f}%)")

    sectors = sorted([(n, get_price(t)[1]) for n, t in SECTORS.items() if get_price(t)[1] is not None], key=lambda x: x[1], reverse=True)
    L += ["", "📂 *섹터 등락*", "🏆 Top 5"]
    for n, c in sectors[:5]:  L.append(f"  {'+' if c>=0 else ''}{c:.2f}%  {n}")
    L.append("💀 Bottom 5")
    for n, c in sectors[-5:]: L.append(f"  {c:.2f}%  {n}")

    L += ["", "💻 *빅테크*"]
    row = []
    for name, t in BIGTECH:
        _, c = get_price(t)
        if c is None: continue
        row.append(f"{name} {'▲' if c>=0 else '▼'}{abs(c):.1f}%")
        if len(row) == 2: L.append("  " + "   |   ".join(row)); row = []
    if row: L.append("  " + row[0])

    L += ["", "⭐ *특징주*"]
    for s in verify_stocks(pick_stocks(kst.strftime("%Y년 %m월 %d일"))):
        vol = f"  _(거래량 {s['vol_ratio']:.1f}배)_" if s["vol_ratio"] >= 2.0 else ""
        L.append(f"{'🔺' if s['chg']>=0 else '🔻'} *{s['ticker']}* ({s['name']}) {'+' if s['chg']>=0 else ''}{s['chg']:.1f}%  _{s['desc']}_{vol}")
        if s["reason"]: L.append(f"   └ {s['reason']}")

    v, c = get_price("^VIX")
    zone = "안정" if v and v<15 else "경계" if v and v<20 else "위험" if v and v<30 else "패닉"
    L += ["", "🧠 *심리 지표*", f"공포탐욕지수: {get_fear_greed()}"]
    if v: L.append(f"VIX: {v:.2f} ({'+' if c>=0 else ''}{c:.2f}%) → {zone} 구간")

    L += ["", f"🕒 _{kst.strftime('%H:%M')} KST_"]
    return "\n".join(L)

# ── Telegram 전송 ──────────────────────────
def send_telegram(text):
    token, chat_id = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}, timeout=15)
        if not r.ok:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": chunk}, timeout=15)

# ── 메인 ──────────────────────────────────
if __name__ == "__main__":
    print("미국 시장 브리핑 생성 중...")
    msg = build_message()
    print(msg)
    send_telegram(msg)
    print("✅ 완료")
