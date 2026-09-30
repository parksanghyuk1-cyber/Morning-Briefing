"""
시장 지표를 노션 「📈 시장 지표」 DB에 저장한다 (노션 대시보드가 읽음)
- 지표마다 한 줄: 값, 전일 대비 변동(%/bp), 최근 한 달 종가(작은 추이 차트용)
- '🤖 AI 브리핑' 한 줄: Gemini 코멘트
NOTION_TOKEN이 없으면 조용히 건너뛴다. 실패해도 텔레그램 전송에는 영향 없음.
"""
import datetime
import os

import requests
import yfinance as yf

MARKET_DS = "f4d5c974-a581-4315-b912-5ae909c5dd62"
NOTION_API = "https://api.notion.com/v1"

GROUPS = {
    "^IRX": "금리", "^TNX": "금리", "^TYX": "금리",
    "DX-Y.NYB": "환율", "KRW=X": "환율", "EURUSD=X": "환율", "CNY=X": "환율",
    "^VIX": "심리", "BTC-USD": "코인", "ETH-USD": "코인",
    "ES=F": "선물", "YM=F": "선물", "NQ=F": "선물", "RTY=F": "선물",
    "^KS11": "지수", "^KQ11": "지수", "^TWII": "지수", "^SOX": "지수",
    "^N225": "지수", "000001.SS": "지수", "^HSI": "지수",
    "CL=F": "원자재", "HG=F": "원자재", "GC=F": "원자재", "SI=F": "원자재", "ZC=F": "원자재",
}
RATE_TICKERS = {"^IRX", "^TNX", "^TYX"}


def _headers():
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": "2025-09-03",
        "Content-Type": "application/json",
    }


def _notion(method, path, body=None):
    r = requests.request(method, NOTION_API + path, headers=_headers(), json=body, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"노션 {r.status_code}: {r.text[:200]}")
    return r.json()


def _history(ticker: str) -> str:
    """최근 한 달 종가 (쉼표로 이어 붙임)"""
    try:
        close = yf.Ticker(ticker).history(period="1mo")["Close"].dropna()
        return ",".join(f"{v:.4g}" for v in close.tolist()[-22:])
    except Exception:
        return ""


def _text(s: str) -> list:
    s = s or ""
    return [{"type": "text", "text": {"content": s[i:i + 2000]}} for i in range(0, min(len(s), 6000), 2000)]


def _existing() -> dict:
    rows = _notion("POST", f"/data_sources/{MARKET_DS}/query", {"page_size": 100})["results"]
    out = {}
    for r in rows:
        t = "".join(x["plain_text"] for x in r["properties"]["티커"]["rich_text"])
        out[t] = r["id"]
    return out


def push_market(records: list[dict], commentary: str):
    if not os.environ.get("NOTION_TOKEN"):
        print("[notion] NOTION_TOKEN 없음 → 건너뜀")
        return
    now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).replace(microsecond=0)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    existing = _existing()

    def upsert(ticker, props):
        props["티커"] = {"rich_text": _text(ticker)}
        props["기준시각"] = {"date": {"start": stamp}}
        if ticker in existing:
            _notion("PATCH", f"/pages/{existing[ticker]}", {"properties": props})
        else:
            _notion("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": MARKET_DS},
                                       "properties": props})

    for order, rec in enumerate(records, start=1):
        t = rec["ticker"]
        upsert(t, {
            "지표": {"title": _text(rec["label"])},
            "그룹": {"select": {"name": GROUPS.get(t, "지수")}},
            "값": {"number": round(float(rec["value"]), 4)},
            "변동": {"number": round(float(rec["chg"]), 2)},
            "단위": {"select": {"name": "bp" if t in RATE_TICKERS else "%"}},
            "순서": {"number": order},
            "추이": {"rich_text": _text(_history(t))},
        })
    if commentary and not commentary.lstrip().startswith("⚠️"):   # Gemini 오류 문구는 저장하지 않고 이전 코멘트 유지
        upsert("AI", {
            "지표": {"title": _text("🤖 AI 브리핑")},
            "그룹": {"select": {"name": "브리핑"}},
            "순서": {"number": 0},
            "메모": {"rich_text": _text(commentary)},
        })
    print(f"[notion] 시장 지표 {len(records)}개 저장")
