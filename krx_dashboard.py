"""
krx_dashboard.py v3
────────────────────
국내 증시 마감 정리 대시보드
- 날짜 로직 개선: 오후 4시 이전이면 전일 데이터 사용
- 52주 고저는 시총 상위 50종목만 체크 (속도 개선)
- 각 항목별 독립적 오류 처리
"""

import os
import re
import datetime
import requests
from pykrx import stock
import yfinance as yf


# ─────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────

def fmt_pct(v: float) -> str:
    arrow = "🔺" if v >= 0 else "🔻"
    return f"{'+' if v>=0 else ''}{v:.2f}% {arrow}"

def fmt_num(v: float, decimals=2) -> str:
    return f"{v:,.{decimals}f}"

def get_target_date() -> str:
    """
    기준일 결정:
    - KST 오후 4시 이후 → 오늘 (당일 마감 데이터 있음)
    - KST 오후 4시 이전 → 가장 최근 영업일 (전일)
    """
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    d = kst_now.date()

    # 오후 4시 이전이거나 주말이면 이전 영업일로
    if kst_now.hour < 16 or d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
        while d.weekday() >= 5:
            d -= datetime.timedelta(days=1)

    return d.strftime("%Y%m%d")

def fmt_date(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"

def prev_bday(base: str, n: int = 1) -> str:
    """base 날짜로부터 n 영업일 이전"""
    d = datetime.date(int(base[:4]), int(base[4:6]), int(base[6:]))
    count = 0
    while count < n:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime("%Y%m%d")

def week_ago(base: str) -> str:
    d = datetime.date(int(base[:4]), int(base[4:6]), int(base[6:]))
    d -= datetime.timedelta(days=7)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")

def year_start(base: str) -> str:
    return base[:4] + "0102"

def year_ago(base: str) -> str:
    d = datetime.date(int(base[:4]), int(base[4:6]), int(base[6:]))
    d = d.replace(year=d.year - 1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d.strftime("%Y%m%d")


# ─────────────────────────────────────────
# 1. 지수 + 밸류에이션
# ─────────────────────────────────────────

INDEX_MAP = {"KOSPI": "1001", "KOSDAQ": "2001"}

def get_index_data(today: str) -> dict:
    result = {}
    prev   = prev_bday(today)
    w_ago  = week_ago(today)
    y_st   = year_start(today)

    for name, code in INDEX_MAP.items():
        try:
            df = stock.get_index_ohlcv_by_date(w_ago, today, code)
            if df.empty or len(df) < 2:
                result[name] = {"error": "데이터 없음"}
                continue

            curr = float(df["종가"].iloc[-1])
            prev_v = float(df["종가"].iloc[-2])
            w1   = float(df["종가"].iloc[0])
            vol  = float(df["거래대금"].iloc[-1]) / 1e9

            df_y = stock.get_index_ohlcv_by_date(y_st, today, code)
            ytd  = (curr - float(df_y["종가"].iloc[0])) / float(df_y["종가"].iloc[0]) * 100 if not df_y.empty else 0.0

            # P/E, P/B
            per, pbr = None, None
            try:
                fnd = stock.get_index_fundamental(today, today, code)
                if not fnd.empty:
                    per = float(fnd["PER"].iloc[-1])
                    pbr = float(fnd["PBR"].iloc[-1])
            except Exception:
                pass

            result[name] = {
                "현재": curr,
                "1D":   (curr - prev_v) / prev_v * 100,
                "1W":   (curr - w1) / w1 * 100,
                "YTD":  ytd,
                "거래대금": vol,
                "PER":  per,
                "PBR":  pbr,
            }
        except Exception as e:
            result[name] = {"error": str(e)[:50]}
    return result


# ─────────────────────────────────────────
# 2. 수급
# ─────────────────────────────────────────

INVESTOR_MAP = {"기관합계": "기관", "외국인합계": "외국인", "개인": "개인"}

def get_trading_by_investor(today: str) -> dict:
    result = {}
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df  = stock.get_market_trading_value_by_investor(today, today, market)
            row = {}
            for col in df.index:
                label = INVESTOR_MAP.get(str(col))
                if label:
                    row[label] = round(float(df.loc[col, "순매수"]) / 1e9, 2)
            result[market] = row if row else {"error": "데이터 없음"}
        except Exception as e:
            result[market] = {"error": str(e)[:50]}
    return result


# ─────────────────────────────────────────
# 3. 투자자별 순매수 상위 종목
# ─────────────────────────────────────────

def get_top_stocks_by_investor(today: str, market="KOSPI", top_n=3) -> dict:
    result = {}
    for inv_code, inv_name in [("2000", "기관"), ("9999", "외국인"), ("1000", "개인")]:
        try:
            df = stock.get_market_net_purchases_of_equities_by_ticker(
                today, today, market, inv_code
            )
            if df.empty:
                continue
            df   = df.sort_values("순매수대금", ascending=False)
            tops = []
            for ticker, row in df.head(top_n).iterrows():
                name = stock.get_market_ticker_name(ticker)
                val  = round(float(row["순매수대금"]) / 1e9, 2)
                tops.append(f"{name} ({'+' if val>=0 else ''}{val:.1f}십억)")
            if tops:
                result[inv_name] = tops
        except Exception:
            pass
    return result


# ─────────────────────────────────────────
# 4. 공매도 상위
# ─────────────────────────────────────────

def get_short_selling_top(today: str, market="KOSPI", top_n=5) -> list:
    try:
        df = stock.get_shorting_value_by_ticker(today, market)
        if df.empty:
            return []
        df = df.sort_values("공매도비중", ascending=False).head(top_n)
        result = []
        for ticker, row in df.iterrows():
            name  = stock.get_market_ticker_name(ticker)
            ratio = float(row.get("공매도비중", 0))
            chg   = float(row.get("공매도비중증가율", 0))
            result.append({"종목": name, "공매도비중": ratio, "비중증가율": chg})
        return result
    except Exception:
        return []


# ─────────────────────────────────────────
# 5. 시총 상위 등락
# ─────────────────────────────────────────

def get_top_cap_movers(today: str, market="KOSPI", top_n=5) -> list:
    try:
        cap_df = stock.get_market_cap_by_ticker(today, market=market)
        if cap_df.empty:
            return []
        cap_df = cap_df.sort_values("시가총액", ascending=False).head(20)
        ohlcv  = stock.get_market_ohlcv_by_ticker(today, market=market)
        result = []
        for ticker in cap_df.index:
            if ticker not in ohlcv.index:
                continue
            chg  = float(ohlcv.loc[ticker].get("등락률", 0))
            name = stock.get_market_ticker_name(ticker)
            result.append((name, chg))
        result.sort(key=lambda x: abs(x[1]), reverse=True)
        return result[:top_n]
    except Exception:
        return []


# ─────────────────────────────────────────
# 6. 52주 신고가/신저가 (시총 상위 50만)
# ─────────────────────────────────────────

def get_52w_highs_lows(today: str, market="KOSPI", top_n=5) -> tuple:
    highs, lows = [], []
    try:
        y_ago  = year_ago(today)
        cap_df = stock.get_market_cap_by_ticker(today, market=market)
        if cap_df.empty:
            return [], []

        # 시총 상위 50개만 체크 (속도 개선)
        top50 = cap_df.sort_values("시가총액", ascending=False).head(50)
        ohlcv_today = stock.get_market_ohlcv_by_ticker(today, market=market)

        for ticker in top50.index:
            if len(highs) >= top_n and len(lows) >= top_n:
                break
            try:
                df = stock.get_market_ohlcv_by_date(y_ago, today, ticker)
                if df.empty or len(df) < 20:
                    continue
                curr = float(df["종가"].iloc[-1])
                hi52 = float(df["고가"].max())
                lo52 = float(df["저가"].min())
                name = stock.get_market_ticker_name(ticker)
                if curr >= hi52 * 0.995 and len(highs) < top_n:
                    highs.append(name)
                elif curr <= lo52 * 1.005 and len(lows) < top_n:
                    lows.append(name)
            except Exception:
                continue
    except Exception:
        pass
    return highs, lows


# ─────────────────────────────────────────
# 7. 투자자 예탁금 (금투협)
# ─────────────────────────────────────────

def get_investor_deposit(today: str) -> str:
    try:
        url  = "https://dis.kofia.or.kr/proframeWeb/XMLSERVICES/"
        body = f"""<?xml version="1.0" encoding="utf-8"?>
<message>
  <proframeHeader>
    <pfmAppName>DIS</pfmAppName>
    <pfmSvcName>COMDataInquirySVC</pfmSvcName>
    <pfmFnName>getInvstdDeposit</pfmFnName>
  </proframeHeader>
  <parameter>
    <reqDay>{today}</reqDay>
    <gubun>1</gubun>
  </parameter>
</message>"""
        resp = requests.post(
            url,
            data=body.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=10,
        )
        match = re.search(r"<invstdDeposit>([\d,]+)</invstdDeposit>", resp.text)
        if match:
            val_mn = int(match.group(1).replace(",", ""))
            return f"{val_mn / 1000:,.1f}십억원"
    except Exception:
        pass
    return "N/A"


# ─────────────────────────────────────────
# 8. 해외 지수
# ─────────────────────────────────────────

def get_us_indices() -> dict:
    result = {}
    for name, ticker in [
        ("S&P500", "^GSPC"), ("나스닥", "^IXIC"),
        ("다우", "^DJI"), ("필라델피아반도체", "^SOX"),
    ]:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if len(hist) < 2:
                continue
            curr = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            result[name] = {"현재": curr, "등락률": (curr - prev) / prev * 100}
        except Exception:
            pass
    return result


# ─────────────────────────────────────────
# 9. 메시지 조립
# ─────────────────────────────────────────

def build_message() -> str:
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    today   = get_target_date()
    print(f"기준일: {today} / 실행시각: {kst_now.strftime('%H:%M')} KST")

    L = []
    L.append(f"📋 *일일 시장 브리핑* ({fmt_date(today)} 마감 기준)")
    L.append("━━━━━━━━━━━━━━━━━━━━")

    # ── 국내 증시 ──
    L.append("🇰🇷 *국내 증시 동향*")
    print("  → 지수 데이터 수집 중...")
    idx = get_index_data(today)

    for mkt in ["KOSPI", "KOSDAQ"]:
        d = idx.get(mkt, {})
        if "error" in d:
            L.append(f"🔸 {mkt}: {d['error']}")
        else:
            L.append(f"🔸 {mkt}: {fmt_num(d['현재'], 2)} ({fmt_pct(d['1D'])})")

    L.append("")
    L.append("🔹 *기간별 성과*")
    for mkt in ["KOSPI", "KOSDAQ"]:
        d = idx.get(mkt, {})
        if "error" not in d:
            L.append(
                f"🔸 {mkt}  "
                f"1D {fmt_pct(d['1D'])} | "
                f"1W {fmt_pct(d['1W'])} | "
                f"YTD {fmt_pct(d['YTD'])}"
            )

    L.append("")
    L.append("🔹 *거래대금 & 밸류에이션*")
    for mkt in ["KOSPI", "KOSDAQ"]:
        d = idx.get(mkt, {})
        if "error" not in d:
            per_str = f"{d['PER']:.1f}x" if d.get("PER") else "N/A"
            pbr_str = f"{d['PBR']:.2f}x" if d.get("PBR") else "N/A"
            L.append(
                f"🔸 {mkt}: 거래대금 {fmt_num(d['거래대금'], 0)}십억 | "
                f"P/E {per_str} | P/B {pbr_str}"
            )

    print("  → 투자자 예탁금 수집 중...")
    deposit = get_investor_deposit(today)
    L.append(f"🔸 투자자 예탁금: {deposit}")

    # ── 수급 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("💰 *수급 (순매수, 십억원)*")
    print("  → 수급 데이터 수집 중...")
    inv = get_trading_by_investor(today)

    for mkt in ["KOSPI", "KOSDAQ"]:
        d = inv.get(mkt, {})
        if "error" in d:
            L.append(f"🔸 {mkt}: {d['error']}")
        else:
            parts = [
                f"{k} {'+' if d.get(k,0)>=0 else ''}{d.get(k,0):.1f}"
                for k in ["기관", "외국인", "개인"]
            ]
            L.append(f"🔸 {mkt}: {' | '.join(parts)}")

    L.append("")
    L.append("🔹 *투자자별 순매수 상위 (KOSPI)*")
    print("  → 순매수 상위 종목 수집 중...")
    top = get_top_stocks_by_investor(today, "KOSPI", top_n=3)
    if top:
        for inv_name, stocks_list in top.items():
            L.append(f"🔸 {inv_name}: {', '.join(stocks_list)}")
    else:
        L.append("🔸 데이터 없음")

    # ── 공매도 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🩳 *공매도 비중 상위 (KOSPI)*")
    print("  → 공매도 데이터 수집 중...")
    shorts = get_short_selling_top(today, "KOSPI", top_n=5)
    if shorts:
        for s in shorts:
            chg_str = f", 증가율 {s['비중증가율']:+.2f}%" if s["비중증가율"] else ""
            L.append(f"🔸 {s['종목']}: {s['공매도비중']:.2f}%{chg_str}")
    else:
        L.append("🔸 데이터 없음")

    # ── 특징 종목 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🔥 *특징 종목*")
    print("  → 시총 상위 등락 수집 중...")
    movers = get_top_cap_movers(today, "KOSPI", top_n=5)
    if movers:
        L.append("🔹 *시총 상위 주요 등락*")
        for name, chg in movers:
            L.append(f"🔸 {name}: {fmt_pct(chg)}")

    print("  → 52주 신고가/신저가 수집 중 (시총 상위 50개)...")
    highs, lows = get_52w_highs_lows(today, "KOSPI", top_n=5)
    L.append("")
    L.append(f"📈 *52주 신고가*: {', '.join(highs) if highs else '없음'}")
    L.append(f"📉 *52주 신저가*: {', '.join(lows)  if lows  else '없음'}")

    # ── 해외 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🌎 *해외 증시 (전영업일)*")
    print("  → 해외 지수 수집 중...")
    for name, d in get_us_indices().items():
        L.append(f"🔸 {name}: {fmt_num(d['현재'], 0)} ({fmt_pct(d['등락률'])})")

    L.append("")
    L.append(f"🕒 _{kst_now.strftime('%H:%M')} KST_")
    return "\n".join(L)


# ─────────────────────────────────────────
# 10. Telegram 전송
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
# 11. 메인
# ─────────────────────────────────────────

def main():
    print("국내 증시 마감 브리핑 생성 중...")
    msg = build_message()
    print("\n--- 결과 ---")
    print(msg)
    send_telegram(msg)
    print("\n✅ 완료")


if __name__ == "__main__":
    main()
