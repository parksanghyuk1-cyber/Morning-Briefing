"""
매크로 모니터 지표를 노션 「🌐 매크로 모니터」 DB에 저장한다 (macro_monitor.yml이 1시간마다 실행)
- 지표마다 한 줄: 데이터 칸에 현재값·기간별 변화·52주 범위·1년 일봉을 JSON으로
- 매크로 모니터 페이지가 열릴 때마다 이 DB를 읽어 그린다
외부 패키지 없이 표준 라이브러리만 사용. NOTION_TOKEN 필요.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

MACRO_DS = "56087293-e126-42dc-925c-3c12da0d21e4"
NOTION_API = "https://api.notion.com/v1"
KST = timezone(timedelta(hours=9))

# (심볼, 표시 이름, 단위, 종류)  종류: "pct" → % 변화, "bp" → 금리 bp 변화
GROUPS = [
    ("주식", [
        ("^KS11", "코스피", "pt", "pct"),
        ("^KQ11", "코스닥", "pt", "pct"),
        ("^GSPC", "S&P 500", "pt", "pct"),
        ("^IXIC", "나스닥 종합", "pt", "pct"),
        ("^N225", "닛케이 225", "pt", "pct"),
        ("000001.SS", "상하이 종합", "pt", "pct"),
    ]),
    ("미국 금리", [
        ("^IRX", "미국채 3개월", "%", "bp"),
        ("^FVX", "미국채 5년", "%", "bp"),
        ("^TNX", "미국채 10년", "%", "bp"),
        ("^TYX", "미국채 30년", "%", "bp"),
    ]),
    ("환율", [
        ("KRW=X", "원/달러", "원", "pct"),
        ("DX-Y.NYB", "달러인덱스", "pt", "pct"),
        ("JPY=X", "엔/달러", "엔", "pct"),
        ("EURUSD=X", "달러/유로", "$", "pct"),
        ("CNY=X", "위안/달러", "위안", "pct"),
    ]),
    ("원자재 · 위험지표", [
        ("CL=F", "WTI 원유", "$/배럴", "pct"),
        ("GC=F", "금", "$/온스", "pct"),
        ("HG=F", "구리", "$/파운드", "pct"),
        ("^VIX", "VIX 변동성", "pt", "pct"),
        ("BTC-USD", "비트코인", "$", "pct"),
    ]),
]


# ── 야후 파이낸스 ─────────────────────────
def fetch(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=1y&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)["chart"]["result"][0]
        except Exception as e:
            if attempt == 2:
                print(f"  [실패] {symbol}: {e}")
                return None
            time.sleep(1.5)


def to_series(res):
    ts = res.get("timestamp") or []
    closes = res["indicators"]["quote"][0].get("close") or []
    points = [(t, c) for t, c in zip(ts, closes) if c is not None]
    meta = res["meta"]
    tz = timezone(timedelta(seconds=meta.get("gmtoffset") or 0))  # 거래소 현지 시간
    live = meta.get("regularMarketPrice")
    live_t = meta.get("regularMarketTime")
    local_date = lambda t: datetime.fromtimestamp(t, tz).date()
    # 오늘 봉이 이미 있으면 현재가로 갱신하고, 없으면 새로 붙인다
    if live is not None and live_t:
        if points and local_date(points[-1][0]) == local_date(live_t):
            points[-1] = (points[-1][0], live)
        elif not points or live_t > points[-1][0]:
            points.append((live_t, live))
    return points, live_t, tz


def value_at_or_before(points, cutoff):
    best = None
    for t, v in points:
        if t <= cutoff:
            best = v
        else:
            break
    return best


def change(now, then, kind):
    if then is None or now is None:
        return None
    if kind == "bp":
        return round((now - then) * 100, 1)
    if then == 0:
        return None
    return round((now / then - 1) * 100, 2)


def build_item(symbol, name, unit, kind):
    res = fetch(symbol)
    if not res:
        return None
    points, live_t, tz = to_series(res)
    if len(points) < 2:
        return None
    last_t, last = points[-1]
    year_start = datetime(datetime.fromtimestamp(last_t, tz).year, 1, 1, tzinfo=tz).timestamp()
    values = [v for _, v in points]
    return {
        "symbol": symbol,
        "name": name,
        "unit": unit,
        "kind": kind,
        "last": last,
        "asOf": live_t or last_t,
        "chg": {
            "1D": change(last, points[-2][1], kind),
            "1W": change(last, value_at_or_before(points, last_t - 7 * 86400), kind),
            "1M": change(last, value_at_or_before(points, last_t - 30 * 86400), kind),
            "YTD": change(last, value_at_or_before(points, year_start - 1), kind),
            "1Y": change(last, points[0][1], kind),
        },
        "hi52": max(values),
        "lo52": min(values),
        "series": [[datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d"), float(f"{v:.6g}")]
                   for t, v in points],
    }


# ── 노션 ──────────────────────────────────
def notion(method, path, body=None):
    req = urllib.request.Request(
        NOTION_API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
                 "Notion-Version": "2025-09-03", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503) and attempt < 2:
                time.sleep(2 + attempt * 3)
                continue
            raise RuntimeError(f"노션 {e.code}: {e.read()[:200]!r}")


def text(s):
    return [{"type": "text", "text": {"content": s[i:i + 2000]}} for i in range(0, len(s), 2000)]


def existing():
    rows = notion("POST", f"/data_sources/{MACRO_DS}/query", {"page_size": 100})["results"]
    return {"".join(x["plain_text"] for x in r["properties"]["심볼"]["rich_text"]): r["id"] for r in rows}


def main():
    jobs = [(g, spec) for g, specs in GROUPS for spec in specs]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda j: build_item(*j[1]), jobs))

    stamp = datetime.now(KST).replace(microsecond=0).isoformat()
    rows = existing()
    ok = 0
    for order, ((group, _), item) in enumerate(zip(jobs, results), start=1):
        if not item:
            continue  # 받아오지 못한 지표는 이전 값을 그대로 둔다
        data = {k: item[k] for k in ("unit", "kind", "last", "asOf", "chg", "hi52", "lo52", "series")}
        props = {
            "지표": {"title": text(item["name"])},
            "심볼": {"rich_text": text(item["symbol"])},
            "그룹": {"select": {"name": group}},
            "순서": {"number": order},
            "기준시각": {"date": {"start": stamp}},
            "데이터": {"rich_text": text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))},
        }
        if item["symbol"] in rows:
            notion("PATCH", f"/pages/{rows[item['symbol']]}", {"properties": props})
        else:
            notion("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": MACRO_DS},
                                      "properties": props})
        ok += 1
    print(f"완료: {ok}/{len(jobs)}개 지표 → 노션 ({stamp})")
    if ok == 0:
        raise SystemExit("지표를 하나도 받아오지 못했어요")


if __name__ == "__main__":
    main()
