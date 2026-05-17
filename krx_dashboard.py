"""
krx_dashboard.py
────────────────
국내 증시 마감 정리 대시보드 v2
- pykrx: 지수/수급/밸류에이션/공매도 (무료)
- yfinance: 해외 지수 (무료)
- 금투협: 투자자 예탁금 (무료 스크래핑)
매일 오후 3:30 KST 자동 전송
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
    s     = "+" if v >= 0 else ""
    arrow = "🔺" if v >= 0 else "🔻"
    return f"{s}{v:.2f}% {arrow}"

def fmt_num(v: float, decimals=2) -> str:
    return f"{v:,.{decimals}f}"

def prev_bdays(n: int) -> list[str]:
    """최근 n 영업일 날짜 (yyyyMMdd), 최신순"""
    days, d = [], datetime.date.today()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= datetime.timedelta(days=1)
    return days


# ─────────────────────────────────────────
# 1. 지수 + 밸류에이션 (P/E, P/B)
# ─────────────────────────────────────────

INDEX_MAP = {"KOSPI": "1001", "KOSDAQ": "2001"}

def get_index_data() -> dict:
    today      = prev_bdays(1)[0]
    week_ago   = prev_bdays(8)[-1]
    year_start = datetime.date.today().strftime("%Y") + "0102"
    result     = {}

    for name, code in INDEX_MAP.items():
        try:
            df = stock.get_index_ohlcv_by_date(week_ago, today, code)
            if df.empty or len(df) < 2:
                continue
            curr = df["종가"].iloc[-1]
            prev = df["종가"].iloc[-2]
            w1   = df["종가"].iloc[0]
            vol  = df["거래대금"].iloc[-1] / 1e9

            df_y = stock.get_index_ohlcv_by_date(year_start, today, code)
            ytd  = (curr - df_y["종가"].iloc[0]) / df_y["종가"].iloc[0] * 100 if not df_y.empty else 0.0

            # P/E, P/B
            try:
                fnd = stock.get_index_fundamental(today, today, code)
                per = float(fnd["PER"].iloc[-1]) if not fnd.empty else None
                pbr = float(fnd["PBR"].iloc[-1]) if not fnd.empty else None
            except Exception:
                per, pbr = None, None

            result[name] = {
                "현재":   curr,
                "1D":     (curr - prev) / prev * 100,
                "1W":     (curr - w1)   / w1   * 100,
                "YTD":    ytd,
                "거래대금": vol,
                "PER":    per,
                "PBR":    pbr,
            }
        except Exception as e:
            result[name] = {"error": str(e)}
    return result


# ─────────────────────────────────────────
# 2. 수급 (기관/외국인/개인)
# ─────────────────────────────────────────

INVESTOR_MAP = {"기관합계": "기관", "외국인합계": "외국인", "개인": "개인"}

def get_trading_by_investor() -> dict:
    today  = prev_bdays(1)[0]
    result = {}
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df  = stock.get_market_trading_value_by_investor(today, today, market)
            row = {}
            for col in df.index:
                label = INVESTOR_MAP.get(col)
                if label:
                    row[label] = round(df.loc[col, "순매수"] / 1e9, 2)
            result[market] = row
        except Exception as e:
            result[market] = {"error": str(e)}
    return result


# ─────────────────────────────────────────
# 3. 투자자별 순매수 상위 종목
# ─────────────────────────────────────────

def get_top_stocks_by_investor(market="KOSPI", top_n=3) -> dict:
    today  = prev_bdays(1)[0]
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
                val  = round(row["순매수대금"] / 1e9, 2)
                tops.append(f"{name} ({'+' if val>=0 else ''}{val:.1f}십억)")
            result[inv_name] = tops
        except Exception as e:
            result[inv_name] = [f"오류: {e}"]
    return result


# ─────────────────────────────────────────
# 4. 공매도 상위 종목 (비중 기준)
# ─────────────────────────────────────────

def get_short_selling_top(market="KOSPI", top_n=5) -> list:
    today  = prev_bdays(1)[0]
    result = []
    try:
        df = stock.get_shorting_value_by_ticker(today, market)
        if df.empty:
            return []
        df = df.sort_values("공매도비중", ascending=False).head(top_n)
        for ticker, row in df.iterrows():
            name  = stock.get_market_ticker_name(ticker)
            ratio = row.get("공매도비중", 0)
            chg   = row.get("공매도비중증가율", 0)
            result.append({
                "종목": name,
                "공매도비중": ratio,
                "비중증가율": chg,
            })
    except Exception:
        pass
    return result


# ─────────────────────────────────────────
# 5. 52주 신고가 / 신저가
# ─────────────────────────────────────────

def get_52w_highs_lows(market="KOSPI", min_cap_bn=300, top_n=5) -> tuple:
    today    = prev_bdays(1)[0]
    year_ago = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y%m%d")
    highs, lows = [], []
    try:
        tickers = stock.get_market_ticker_list(today, market=market)
        cap_df  = stock.get_market_cap_by_ticker(today, market=market)
        ohlcv   = stock.get_market_ohlcv_by_ticker(today, market=market)

        for ticker in tickers:
            if len(highs) >= top_n and len(lows) >= top_n:
                break
            try:
                cap = cap_df.loc[ticker, "시가총액"] / 1e9 if ticker in cap_df.index else 0
                if cap < min_cap_bn:
                    continue
                df = stock.get_market_ohlcv_by_date(year_ago, today, ticker)
                if df.empty or len(df) < 20:
                    continue
                curr = df["종가"].iloc[-1]
                hi52 = df["고가"].max()
                lo52 = df["저가"].min()
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
# 6. 시총 상위 등락
# ─────────────────────────────────────────

def get_top_cap_movers(market="KOSPI", top_n=5) -> list:
    today  = prev_bdays(1)[0]
    result = []
    try:
        cap_df = stock.get_market_cap_by_ticker(today, market=market)
        cap_df = cap_df.sort_values("시가총액", ascending=False).head(20)
        ohlcv  = stock.get_market_ohlcv_by_ticker(today, market=market)
        for ticker in cap_df.index:
            if ticker not in ohlcv.index:
                continue
            chg  = ohlcv.loc[ticker].get("등락률", 0)
            name = stock.get_market_ticker_name(ticker)
            result.append((name, chg))
        result.sort(key=lambda x: abs(x[1]), reverse=True)
    except Exception:
        pass
    return result[:top_n]


# ─────────────────────────────────────────
# 7. 투자자 예탁금 (금투협 스크래핑)
# ─────────────────────────────────────────

def get_investor_deposit() -> str:
    """금융투자협회에서 투자자 예탁금 스크래핑"""
    try:
        url  = "https://dis.kofia.or.kr/proframeWeb/XMLSERVICES/"
        body = """<?xml version="1.0" encoding="utf-8"?>
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
</message>""".format(today=prev_bdays(1)[0])
        resp = requests.post(
            url,
            data=body.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=10,
        )
        # 금액 추출 (단위: 백만원)
        match = re.search(r"<invstdDeposit>([\d,]+)</invstdDeposit>", resp.text)
        if match:
            val_mn = int(match.group(1).replace(",", ""))
            val_bn = val_mn / 1000  # 십억원으로 변환
            return f"{val_bn:,.1f}십억원"
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
            curr = hist["Close"].iloc[-1]
            prev = hist["Close"].iloc[-2]
            result[name] = {"현재": curr, "등락률": (curr - prev) / prev * 100}
        except Exception:
            pass
    return result


# ─────────────────────────────────────────
# 9. 메시지 조립
# ─────────────────────────────────────────

def build_message() -> str:
    kst_now  = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    today    = prev_bdays(1)[0]
    date_fmt = f"{today[:4]}-{today[4:6]}-{today[6:]}"

    L = []  # lines
    L.append(f"📋 *일일 시장 브리핑* ({date_fmt} 마감 기준)")
    L.append("━━━━━━━━━━━━━━━━━━━━")

    # ── 국내 증시 ──
    L.append("🇰🇷 *국내 증시 동향*")

    idx = get_index_data()
    for mkt in ["KOSPI", "KOSDAQ"]:
        d = idx.get(mkt, {})
        if "error" in d:
            L.append(f"🔸 {mkt}: 데이터 오류")
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

    # 투자자 예탁금
    deposit = get_investor_deposit()
    L.append(f"🔸 투자자 예탁금: {deposit}")

    # ── 수급 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("💰 *수급 (순매수, 십억원)*")

    inv = get_trading_by_investor()
    for mkt in ["KOSPI", "KOSDAQ"]:
        d = inv.get(mkt, {})
        if "error" in d:
            L.append(f"🔸 {mkt}: 데이터 오류")
        else:
            parts = []
            for k in ["기관", "외국인", "개인"]:
                v = d.get(k, 0)
                parts.append(f"{k} {'+' if v>=0 else ''}{v:.1f}")
            L.append(f"🔸 {mkt}: {' | '.join(parts)}")

    L.append("")
    L.append("🔹 *투자자별 순매수 상위 (KOSPI)*")
    top = get_top_stocks_by_investor("KOSPI", top_n=3)
    for inv_name, stocks_list in top.items():
        L.append(f"🔸 {inv_name}: {', '.join(stocks_list)}")

    # ── 공매도 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🩳 *공매도 비중 상위 (KOSPI)*")

    shorts = get_short_selling_top("KOSPI", top_n=5)
    if shorts:
        for s in shorts:
            chg_str = f", 비중증가율 {s['비중증가율']:+.2f}%" if s['비중증가율'] else ""
            L.append(f"🔸 {s['종목']}: 공매도비중 {s['공매도비중']:.2f}%{chg_str}")
    else:
        L.append("🔸 데이터 없음")

    # ── 특징 종목 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🔥 *특징 종목*")

    movers = get_top_cap_movers("KOSPI", top_n=5)
    if movers:
        L.append("🔹 *시총 상위 주요 등락*")
        for name, chg in movers:
            L.append(f"🔸 {name}: {fmt_pct(chg)}")

    highs, lows = get_52w_highs_lows("KOSPI", min_cap_bn=300, top_n=5)
    L.append("")
    L.append(f"📈 *52주 신고가*: {', '.join(highs) if highs else '없음'}")
    L.append(f"📉 *52주 신저가*: {', '.join(lows)  if lows  else '없음'}")

    # ── 해외 ──
    L.append("")
    L.append("━━━━━━━━━━━━━━━━━━━━")
    L.append("🌎 *해외 증시 (전영업일)*")

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
    print(msg)
    send_telegram(msg)
    print("✅ 완료")


if __name__ == "__main__":
    main()
