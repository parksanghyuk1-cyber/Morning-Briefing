"""
시장 심리 한눈에 (sentiment.yml, cron-job.org가 평일 07:10 장 전 · 15:45 마감 뒤 KST에 실행)
- 공포·탐욕 지수 2개 (미국 S&P 500 / KOSPI 200), CNN과 같은 방식으로 자체 계산
- RSI(14) 코스피·코스닥, ADR(20일 등락비율) 코스피·코스닥, VIX, VKOSPI
- 기준선을 넘거나 크게 움직인 지표만 골라 코멘트 2~3줄
결과는 노션 「🌐 매크로 모니터」 DB의 SENTIMENT 줄(매크로 모니터 화면이 읽음)과
텔레그램(요약 + 차트 1장)으로 보낸다.

공포·탐욕 계산: 구성 요소마다 최근 1년(252거래일) 안에서 오늘 값이 몇 % 위치인지(0~100)를 구해 평균.
  미국:  S&P 500 125일선 괴리, S&P 500 RSI, VIX 50일선 괴리(반대), 주식-국채 20일 수익률 차, 하이일드-국채 20일 변화
  KOSPI 200: KOSPI 200 125일선 괴리, KOSPI 200 RSI, VKOSPI 50일선 괴리(반대), 주식-국채 20일 수익률 차
"""
import csv
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from kr_calendar import holiday_name, kst_today

KST = timezone(timedelta(hours=9))
MACRO_DS = "56087293-e126-42dc-925c-3c12da0d21e4"  # 🌐 매크로 모니터
NOTION_API = "https://api.notion.com/v1"
BREADTH_CSV = "data/breadth.csv"
OUT_PNG = "out/sentiment.png"

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
FORCE_SEND = os.environ.get("FORCE_SEND", "").lower() in ("1", "true", "yes")
DRY_RUN = "--dry-run" in sys.argv  # 노션·텔레그램 없이 계산과 차트만
# close: 국장 마감 뒤(15:45) / morning: 장 시작 전(07:10, 밤사이 미국장 반영, 국장 지표는 전날 마감 값)
SESSION = os.environ.get("SESSION", "close").strip().lower() or "close"

UA = {"User-Agent": "Mozilla/5.0"}


# ── 데이터 받기 ─────────────────────────
def get_json(url, tries=3):
    req = urllib.request.Request(url, headers=UA)
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 + 3 * i)


def yahoo(symbol):
    """{날짜: 종가} 2년치"""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=2y&interval=1d")
    res = get_json(url)["chart"]["result"][0]
    off = res["meta"].get("gmtoffset", 0)
    out = {}
    for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is not None:
            out[datetime.fromtimestamp(t + off, timezone.utc).date().isoformat()] = c
    return out


def cnbc(symbol):
    """{날짜: 종가} 2년치 (VKOSPI, KOSPI 200은 야후에 일봉이 없어 CNBC에서)"""
    start = (kst_today() - timedelta(days=730)).strftime("%Y%m%d000000")
    end = (kst_today() + timedelta(days=1)).strftime("%Y%m%d000000")
    url = (f"https://ts-api.cnbc.com/harmony/app/bars/{urllib.parse.quote(symbol)}"
           f"/1D/{start}/{end}/adjusted/EST5EDT.json")
    bars = get_json(url)["barData"]["priceBars"] or []
    out = {}
    for b in bars:
        t = b["tradeTime"]
        out[f"{t[:4]}-{t[4:6]}-{t[6:8]}"] = float(b["close"])
    return out


def series(d):
    return sorted(d.items())


# ── 계산 도구 ─────────────────────────
def rsi(pts, n=14):
    """Wilder RSI → [(날짜, 값)]"""
    out = []
    if len(pts) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = pts[i][1] - pts[i - 1][1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    ag, al = gains / n, losses / n
    for i in range(n, len(pts)):
        if i > n:
            ch = pts[i][1] - pts[i - 1][1]
            ag = (ag * (n - 1) + max(ch, 0)) / n
            al = (al * (n - 1) + max(-ch, 0)) / n
        out.append((pts[i][0], 100.0 if al == 0 else 100 - 100 / (1 + ag / al)))
    return out


def sma(pts, n):
    out, s = [], 0.0
    for i, (d, v) in enumerate(pts):
        s += v
        if i >= n:
            s -= pts[i - n][1]
        if i >= n - 1:
            out.append((d, s / n))
    return out


def gap_from_ma(pts, n):
    """종가가 n일 이동평균에서 몇 % 떨어져 있는지"""
    ma = dict(sma(pts, n))
    return [(d, v / ma[d] - 1) for d, v in pts if d in ma]


def ret(pts, n):
    return [(pts[i][0], pts[i][1] / pts[i - n][1] - 1) for i in range(n, len(pts))]


def diff(a, b):
    bd = dict(b)
    return [(d, v - bd[d]) for d, v in a if d in bd]


def ratio(a, b):
    bd = dict(b)
    return [(d, v / bd[d]) for d, v in a if d in bd and bd[d]]


def neg(pts):
    return [(d, -v) for d, v in pts]


def pct_rank(pts, window=252):
    """각 날짜 값이 최근 1년 안에서 몇 % 위치인지 (0~100)"""
    out = []
    vals = [v for _, v in pts]
    for i in range(window - 1, len(pts)):
        win = vals[i - window + 1:i + 1]
        below = sum(1 for x in win if x < vals[i])
        equal = sum(1 for x in win if x == vals[i])
        out.append((pts[i][0], 100 * (below + 0.5 * (equal - 1)) / (window - 1)))
    return out


def composite(parts):
    common = set.intersection(*(set(d for d, _ in p) for p in parts))
    maps = [dict(p) for p in parts]
    return [(d, sum(m[d] for m in maps) / len(maps)) for d in sorted(common)]


def band(v):
    if v < 25:
        return "Extreme Fear"
    if v < 45:
        return "Fear"
    if v <= 55:
        return "Neutral"
    if v <= 75:
        return "Greed"
    return "Extreme Greed"


BAND_KO = {"Extreme Fear": "극단적 공포", "Fear": "공포", "Neutral": "중립",
           "Greed": "탐욕", "Extreme Greed": "극단적 탐욕"}

STEPS = [("1달 전", 21), ("1주 전", 5), ("전날", 1), ("최근", 0)]


def history(pts):
    return [[label, round(pts[-1 - k][1], 1)] for label, k in STEPS if len(pts) > k]


# ── ADR (한국거래소, 기존 수급 봇과 같은 로그인) ─────────────────
def load_breadth():
    rows = {}
    if os.path.exists(BREADTH_CSV):
        with open(BREADTH_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["date"], r["market"])] = (int(r["up"]), int(r["down"]))
    return rows


def save_breadth(rows):
    os.makedirs(os.path.dirname(BREADTH_CSV), exist_ok=True)
    keep = sorted(rows.items())[-2 * 400:]  # 시장 2개 × 약 400거래일
    with open(BREADTH_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "market", "up", "down"])
        for (d, m), (u, dn) in keep:
            w.writerow([d, m, u, dn])


def update_breadth(trading_days):
    """최근 거래일의 상승·하락 종목 수를 채운다. 한국거래소 로그인이 없으면 None"""
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print("KRX_ID/KRX_PW 없음 → ADR 생략")
        return None
    from pykrx import stock

    rows = load_breadth()
    for d in trading_days:
        for m in ("KOSPI", "KOSDAQ"):
            if (d, m) in rows:
                continue
            try:
                df = stock.get_market_ohlcv(d.replace("-", ""), market=m)
            except Exception as e:
                print(f"{d} {m} 조회 실패: {e}")
                continue
            if df is None or df.empty or "등락률" not in df.columns:
                continue
            ch = df["등락률"]
            up, down = int((ch > 0).sum()), int((ch < 0).sum())
            if up + down == 0:
                continue  # 아직 오늘 시세가 안 올라온 경우
            rows[(d, m)] = (up, down)
            time.sleep(1)
    save_breadth(rows)
    return rows


def adr(rows, market, n=20):
    days = sorted(d for d, m in rows if m == market)
    if len(days) < n:
        return None
    last = days[-n:]
    up = sum(rows[(d, market)][0] for d in last)
    down = sum(rows[(d, market)][1] for d in last)
    return {"v": round(100 * up / down, 1) if down else None, "asOf": last[-1]}


# ── 코멘트 ─────────────────────────
def comments(fg, rsis, adrs, vols):
    out = []  # (우선순위, 문장)
    for v in vols:
        c = v["chg1d"]
        gap = (v["last"] / v["ma20"] - 1) * 100 if v["ma20"] else 0
        if c is not None and c >= 10:
            out.append((1, f"{v['name']}가 하루 새 +{c:.1f}% 급등했어요. 시장이 큰 출렁임에 대비하는 신호예요."))
        elif c is not None and c <= -10:
            out.append((2, f"{v['name']}가 하루 새 {c:.1f}% 내려 불안이 빠르게 진정됐어요."))
        elif gap >= 20:
            out.append((3, f"{v['name']}가 20일 평균보다 {gap:.0f}% 높아 경계 구간이에요."))
        elif gap <= -20:
            out.append((5, f"{v['name']}가 20일 평균보다 {abs(gap):.0f}% 낮아 안정 구간이에요."))
    for f in fg:
        h = f["hist"]
        if len(h) >= 2 and band(h[-2][1]) != band(h[-1][1]):
            out.append((2, f"{f['short']} 공포·탐욕이 {BAND_KO[band(h[-2][1])]} → {BAND_KO[band(h[-1][1])]}(으)로 바뀌었어요."))
        elif h[-1][1] < 25:
            out.append((4, f"{f['short']} 공포·탐욕 {h[-1][1]:.0f}, 극단적 공포 구간이 이어지고 있어요."))
        elif h[-1][1] > 75:
            out.append((4, f"{f['short']} 공포·탐욕 {h[-1][1]:.0f}, 극단적 탐욕 구간이에요. 과열을 조심할 때예요."))
    for name, r in rsis.items():
        if r <= 30:
            out.append((3, f"{name} RSI {r:.1f}, 과매도 구간(30 이하)이에요."))
        elif r >= 70:
            out.append((3, f"{name} RSI {r:.1f}, 과열 구간(70 이상)이에요."))
    for name, a in adrs.items():
        if a is None:
            continue
        if a <= 75:
            out.append((3, f"{name} ADR {a:.1f}, 바닥권(75 이하)이에요."))
        elif a >= 120:
            out.append((3, f"{name} ADR {a:.1f}, 과열권(120 이상)이에요."))
    out.sort(key=lambda x: x[0])
    picked = [s for _, s in out[:3]]
    return picked or ["특별히 튀는 신호는 없어요. 모든 지표가 평소 범위에 있어요."]


# ── 전체 계산 ─────────────────────────
def build():
    ks200, vkospi = series(cnbc(".KS200")), series(cnbc(".KSVKOSPI"))
    kospi, kosdaq = series(yahoo("^KS11")), series(yahoo("^KQ11"))
    spx, vix = series(yahoo("^GSPC")), series(yahoo("^VIX"))
    spy, tlt = series(yahoo("SPY")), series(yahoo("TLT"))
    hyg, ief = series(yahoo("HYG")), series(yahoo("IEF"))
    ktb = series(yahoo("148070.KS"))  # KOSEF 국고채 10년

    us = composite([
        pct_rank(gap_from_ma(spx, 125)),
        pct_rank(rsi(spx)),
        pct_rank(neg(gap_from_ma(vix, 50))),
        pct_rank(diff(ret(spy, 20), ret(tlt, 20))),
        pct_rank(ret(ratio(hyg, ief), 20)),
    ])
    kr = composite([
        pct_rank(gap_from_ma(ks200, 125)),
        pct_rank(rsi(ks200)),
        pct_rank(neg(gap_from_ma(vkospi, 50))),
        pct_rank(diff(ret(ks200, 20), ret(ktb, 20))),
    ])
    year = (kst_today() - timedelta(days=365)).isoformat()
    fg = []
    for key, name, short, note, pts in [
        ("KR", "KOSPI 200 Fear&Greed", "KOSPI 200", "KOSPI 200 기준 · 자체 계산", kr),
        ("US", "Fear&Greed (미국)", "미국", "S&P 500 기준 · CNN 방식 자체 계산", us),
    ]:
        fg.append({"key": key, "name": name, "short": short, "note": note, "asOf": pts[-1][0],
                   "hist": history(pts), "band": band(pts[-1][1]),
                   "series": [[d, round(v, 1)] for d, v in pts if d >= year]})

    rsis = {"코스피": rsi(kospi)[-1][1], "코스닥": rsi(kosdaq)[-1][1]}

    days = [d for d, _ in ks200][-30:]
    today = kst_today().isoformat()
    if SESSION == "close" and today not in days and not holiday_name(kst_today()):
        days.append(today)  # 아침에는 오늘 시세가 아직 없으니 넣지 않는다
    rows = update_breadth(days)
    adrs = {"코스피": None, "코스닥": None}
    adr_asof = None
    if rows:
        for name, m in (("코스피", "KOSPI"), ("코스닥", "KOSDAQ")):
            a = adr(rows, m)
            if a:
                adrs[name], adr_asof = a["v"], a["asOf"]

    vols = []
    for name, pts in (("VKOSPI", vkospi), ("VIX", vix)):
        ma = dict(sma(pts, 20))
        prev = pts[-2][1] if len(pts) > 1 else None
        vols.append({"name": name, "last": round(pts[-1][1], 2), "asOf": pts[-1][0],
                     "chg1d": round((pts[-1][1] / prev - 1) * 100, 2) if prev else None,
                     "ma20": round(ma.get(pts[-1][0], 0), 2),
                     "series": [[d, round(v, 2), round(ma[d], 2) if d in ma else None]
                                for d, v in pts if d >= year]})

    return {
        "generatedAt": datetime.now(KST).replace(microsecond=0).isoformat(),
        "session": SESSION,
        "fg": fg,
        "rsi": {k: round(v, 2) for k, v in rsis.items()},
        "rsiAsOf": kospi[-1][0],
        "adr": adrs,
        "adrAsOf": adr_asof,
        "vol": vols,
        "comments": comments(fg, rsis, adrs, vols),
    }


# ── 차트 (텔레그램용) ─────────────────────────
def chart(data, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    for name in ["NanumGothic", "NanumBarunGothic", "Malgun Gothic", "AppleGothic"]:
        try:
            matplotlib.font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.family"] = name
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False

    cut = (kst_today() - timedelta(days=92)).isoformat()
    to_d = lambda s: datetime.strptime(s, "%Y-%m-%d")
    ink, gray = "#142031", "#8a94a3"

    fig, axes = plt.subplots(3, 1, figsize=(8, 9.6), sharex=True)
    ax = axes[0]
    for f, style in zip(data["fg"], ["-", "--"]):
        pts = [p for p in f["series"] if p[0] >= cut]
        ax.plot([to_d(p[0]) for p in pts], [p[1] for p in pts], style, color=ink, lw=1.8,
                label=f"{f['short']} {f['hist'][-1][1]:.0f}")
    for y in (25, 45, 55, 75):
        ax.axhline(y, color=gray, lw=0.6, ls=":")
    ax.set_ylim(0, 100)
    ax.set_title("공포·탐욕 지수 (0 극단적 공포 · 100 극단적 탐욕)", fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=9, loc="upper left")

    for ax, v in zip(axes[1:], data["vol"]):
        pts = [p for p in v["series"] if p[0] >= cut]
        xs = [to_d(p[0]) for p in pts]
        ax.plot(xs, [p[1] for p in pts], color=ink, lw=1.8, label=f"{v['name']} {v['last']:.2f}")
        ax.plot(xs, [p[2] for p in pts], color=gray, lw=1.2, ls="--", label="20일 평균")
        ax.set_title(f"{v['name']} (3개월)", fontsize=11, loc="left")
        ax.legend(frameon=False, fontsize=9, loc="upper left")

    for ax in axes:
        ax.grid(True, alpha=0.25, lw=0.5)
        ax.tick_params(labelsize=8)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ── 텔레그램 ─────────────────────────
def caption(data):
    md = lambda s: f"{int(s[5:7])}/{int(s[8:10])}"
    kr_d = data["fg"][0]["asOf"]  # 실제 마지막 거래일
    us_d = data["fg"][1]["asOf"]
    if data.get("session") == "morning":
        head = f"장 시작 전 · 국장 {md(kr_d)} / 미국장 {md(us_d)} 마감 기준"
    else:
        head = f"{md(kr_d)} 국장 마감"
    lines = [f"<b>📊 시장 심리</b> ({head})", ""]
    for f in data["fg"]:
        arrow = " → ".join(f"{v:.0f}" for _, v in f["hist"])
        lines.append(f"<b>{html.escape(f['name'])}:</b> {arrow}, {f['band']}")
    lines.append("<i>(1달 전 → 1주 전 → 전날 → 최근 마감 · 자체 계산)</i>")
    lines.append("")
    r = data["rsi"]
    lines.append(f"<b>RSI:</b> 코스피 {r['코스피']:.2f} / 코스닥 {r['코스닥']:.2f}")
    a = data["adr"]
    if a["코스피"] is not None:
        lines.append(f"<b>ADR:</b> 코스피 {a['코스피']:.1f} / 코스닥 {a['코스닥']:.1f}")
    lines.append("")
    for v in data["vol"]:
        c = v["chg1d"]
        cs = "" if c is None else f" ({'+' if c > 0 else ''}{c:.1f}%)"
        lines.append(f"<b>{v['name']}:</b> {v['last']:.2f}{cs}")
    lines.append("")
    lines.append("<b>💬 코멘트</b>")
    lines += [f"· {html.escape(c)}" for c in data["comments"]]
    return "\n".join(lines)


def send_photo(path, text):
    import requests
    with open(path, "rb") as fp:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                          data={"chat_id": TG_CHAT, "caption": text[:1024], "parse_mode": "HTML"},
                          files={"photo": fp}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"텔레그램 발송 실패: {r.status_code} {r.text[:300]}")


def send_error(msg):
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": f"시장 심리 실패\n\n{msg}"[:4000]}, timeout=30)
    except Exception:
        pass


# ── 노션 ─────────────────────────
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


def save_notion(data):
    rows = notion("POST", f"/data_sources/{MACRO_DS}/query", {"page_size": 100})["results"]
    ids = {"".join(x["plain_text"] for x in r["properties"]["심볼"]["rich_text"]): r["id"] for r in rows}
    props = {
        "지표": {"title": text("시장 심리")},
        "심볼": {"rich_text": text("SENTIMENT")},
        "그룹": {"select": {"name": "시장 심리"}},
        "순서": {"number": 0},
        "기준시각": {"date": {"start": data["generatedAt"]}},
        "데이터": {"rich_text": text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))},
    }
    if "SENTIMENT" in ids:
        notion("PATCH", f"/pages/{ids['SENTIMENT']}", {"properties": props})
    else:
        notion("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": MACRO_DS},
                                  "properties": props})


def main():
    off = holiday_name(kst_today())
    if off and not FORCE_SEND and not DRY_RUN:
        print(f"오늘은 {off}, 건너뜀")
        return
    data = build()
    chart(data, OUT_PNG)
    cap = caption(data)
    print(cap)
    print(f"노션 데이터 크기: {len(json.dumps(data, ensure_ascii=False, separators=(',', ':')))}자")
    if DRY_RUN:
        with open("out/sentiment.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return
    save_notion(data)
    send_photo(OUT_PNG, cap)
    print("노션 저장 · 텔레그램 발송 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_error(repr(e))
        raise
