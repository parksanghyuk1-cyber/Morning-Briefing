"""
노션 「📈 시장 지표」를 1시간마다 새 값으로 갱신한다 (채용 터미널 대시보드가 읽음)
- 아침 브리핑(dashboard.py)과 같은 지표·같은 이름·같은 순서로 저장한다
- 텔레그램 전송·뉴스·Gemini 코멘트는 하지 않는다 → 🤖 AI 브리핑 줄은 아침 것을 그대로 둔다
워크플로: .github/workflows/market_hourly.yml
"""
from dashboard import get_price, get_rate, get_kr_index, TICKER_LABELS
from notion_market import push_market

# (티커, 이름, 가져오는 방법) — dashboard.build_dashboard()의 add(...) 순서·이름과 같게 유지
ITEMS = [
    ("^IRX", "🇺🇸 미국채 3개월", get_rate),
    ("^TNX", "🇺🇸 미국채 10년", get_rate),
    ("^TYX", "🇺🇸 미국채 30년", get_rate),
    ("DX-Y.NYB", "💵 달러 인덱스", get_price),
    ("KRW=X", "🇰🇷 원/달러", get_price),
    ("EURUSD=X", "🇪🇺 유로/달러", get_price),
    ("CNY=X", "🇨🇳 달러/위안", get_price),
    ("^VIX", "😨 VIX", get_price),
    ("BTC-USD", "🪙 비트코인", get_price),
    ("ETH-USD", "💎 이더리움", get_price),
    ("ES=F", "S&P 500 선물", get_price),
    ("YM=F", "다우 존스 선물", get_price),
    ("NQ=F", "나스닥 100 선물", get_price),
    ("RTY=F", "러셀 2000 선물", get_price),
    ("^KS11", "🇰🇷 코스피", get_kr_index),
    ("^KQ11", "🇰🇷 코스닥", get_kr_index),
    ("^TWII", "🇹🇼 대만 가권", get_price),
    ("^SOX", "💾 필라델피아 반도체", get_price),
    ("^N225", "🇯🇵 니케이 225", get_price),
    ("000001.SS", "🇨🇳 상해 종합", get_price),
    ("^HSI", "🇭🇰 홍콩 항셍", get_price),
    ("CL=F", "🛢️ WTI 유가", get_price),
    ("HG=F", "🏗️ 구리", get_price),
    ("GC=F", "🥇 국제 금", get_price),
    ("SI=F", "🥈 국제 은", get_price),
    ("ZC=F", "🌽 옥수수", get_price),
]


def main():
    records = []
    for ticker, label, fetch in ITEMS:
        v, c = fetch(ticker)
        if v is None or c is None:
            print(f"[skip] {ticker}: 값 없음 → 노션의 이전 값 유지")
            continue
        records.append({"ticker": ticker, "label": TICKER_LABELS.get(ticker, label), "value": v, "chg": c})
    if not records:
        raise SystemExit("지표를 하나도 받지 못했어요")
    push_market(records, "")   # 코멘트가 비어 있으면 AI 브리핑 줄은 건드리지 않음


if __name__ == "__main__":
    main()
