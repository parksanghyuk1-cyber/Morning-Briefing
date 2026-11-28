"""
장 시작 전 뉴스 클리핑 (news_clipping.yml, cron-job.org가 평일 07:30 KST에 실행)
- 1, 2번: [뉴욕증시] [뉴욕유가] 제목 + 마감 숫자 (숫자는 야후 시세에서 직접, AI가 쓰지 않음)
- 3~10번: 전 영업일 국장 마감(15:30) 이후 국내 경제·산업·증권 기사 중 Gemini가 8건 선별·요약
- 형식: ★(시장 영향 큰 기사) "제목" / -요약 / 요약 / 요약  (음슴체)
기사 원문은 옮기지 않고 제목·짧은 요약·링크만 보낸다.
"""
import datetime
import email.utils
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests

from kr_calendar import holiday_name, kst_today

KST = datetime.timezone(datetime.timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
FORCE_SEND = os.environ.get("FORCE_SEND", "").lower() in ("1", "true", "yes")
DRY_RUN = "--dry-run" in sys.argv
N_DOMESTIC = 8
PER_FEED = 40  # 피드마다 최신 40건까지 (한 매체가 목록을 독차지하지 않게)

FEEDS = [
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/industry.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/market.xml"),
    ("한국경제", "https://www.hankyung.com/feed/finance"),
    ("한국경제", "https://www.hankyung.com/feed/economy"),
    ("매일경제", "https://www.mk.co.kr/rss/50200011/"),
    ("매일경제", "https://www.mk.co.kr/rss/50100032/"),
    ("매일경제", "https://www.mk.co.kr/rss/30100041/"),
    ("이데일리", "http://rss.edaily.co.kr/stock_news.xml"),
    ("머니투데이", "https://rss.mt.co.kr/mt_news.xml"),
    ("서울경제", "https://www.sedaily.com/rss/finance"),
]
GNEWS = "https://news.google.com/rss/search?q={q}+when:1d&hl=ko&gl=KR&ceid=KR:ko"


def clean(t):
    t = re.sub(r"<[^>]+>", " ", html.unescape(t or ""))
    return re.sub(r"\s+", " ", t).strip()


def since_cutoff():
    """직전 영업일 15:30 KST (월요일이면 금요일 15:30)"""
    d = kst_today() - datetime.timedelta(days=1)
    while holiday_name(d):
        d -= datetime.timedelta(days=1)
    return datetime.datetime(d.year, d.month, d.day, 15, 30, tzinfo=KST)


# ── 기사 모으기 ─────────────────────────
def collect(cutoff):
    out, seen = [], set()
    for source, url in FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"[rss] {source} {url} 실패: {e}")
            continue
        taken = 0
        for it in root.iter("item"):
            if taken >= PER_FEED:
                break
            title = clean(it.findtext("title"))
            pub = it.findtext("pubDate")
            try:
                when = email.utils.parsedate_to_datetime(pub) if pub else None
                if when and when.tzinfo is None:
                    when = when.replace(tzinfo=KST)  # 시간대 없는 피드는 한국 시간
            except Exception:
                when = None
            if not title or (when and when < cutoff):
                continue
            key = re.sub(r"\W", "", title)[:40]
            if key in seen:
                continue
            seen.add(key)
            taken += 1
            out.append({"source": source, "title": title,
                        "desc": clean(it.findtext("description"))[:200],
                        "link": (it.findtext("link") or "").strip()})
    return out


def gnews_title(query):
    """구글 뉴스에서 [뉴욕증시]·[뉴욕유가] 제목 하나"""
    try:
        r = requests.get(GNEWS.format(q=requests.utils.quote(query)), headers=UA, timeout=15)
        for it in ET.fromstring(r.content).iter("item"):
            t = re.sub(r"\s+-\s+[^-]+$", "", clean(it.findtext("title")))
            if query.strip('"') in t:
                return t
    except Exception as e:
        print(f"[gnews] {query} 실패: {e}")
    return None


def article_text(url):
    """기사 본문 (요약 재료로만 쓴다)"""
    if not url.startswith("http"):
        return ""
    try:
        import trafilatura
        r = requests.get(url, headers=UA, timeout=15)
        txt = trafilatura.extract(r.text) or ""
        return re.sub(r"\s+", " ", txt)[:2500]
    except Exception as e:
        print(f"[body] {url[:60]} 실패: {e}")
        return ""


# ── 뉴욕증시·유가 숫자 ─────────────────────────
def closes(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{requests.utils.quote(symbol)}?range=1mo&interval=1d")
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()["chart"]["result"][0]
    return [c for c in res["indicators"]["quote"][0]["close"] if c is not None]


def streak(cs):
    """마지막 날과 같은 방향이 며칠째인지"""
    sign = lambda i: (cs[i] > cs[i - 1]) - (cs[i] < cs[i - 1])
    s, n = sign(len(cs) - 1), 0
    for i in range(len(cs) - 1, 0, -1):
        if sign(i) != s:
            break
        n += 1
    return s, n


KO_N = {1: "", 2: "이틀", 3: "사흘", 4: "나흘", 5: "닷새"}


def run_phrase(what, s, n, verb):
    word = {1: "상승", -1: "하락", 0: "보합"}[s]
    if n <= 1 or s == 0:
        return f"{what} {word} 마감"
    return f"{what} {KO_N.get(n, f'{n}거래일')}{'째' if verb else ' 연속'} {word} 마감"


def pct(cs):
    return (cs[-1] / cs[-2] - 1) * 100


def fmt_pct(p):
    return f"{'+' if p > 0 else ''}{p:.2f}%"


def us_items():
    items = []
    try:
        dji, spx, ixic = closes("^DJI"), closes("^GSPC"), closes("^IXIC")
        s, n = streak(spx)
        line = (f"{run_phrase('뉴욕증시', s, n, True)} / DOW30 {fmt_pct(pct(dji))}, "
                f"S&P 500 {fmt_pct(pct(spx))}, NASDAQ {fmt_pct(pct(ixic))}")
        title = gnews_title('"[뉴욕증시]"') or f"[뉴욕증시] 3대 지수 {'상승' if s > 0 else '하락' if s < 0 else '혼조'} 마감"
        items.append({"title": title, "lines": [line], "star": False, "link": ""})
    except Exception as e:
        print(f"[us] 증시 실패: {e}")
    try:
        wti, brent = closes("CL=F"), closes("BZ=F")
        s, n = streak(wti)
        line = (f"{run_phrase('국제유가', s, n, False)} / WTI {fmt_pct(pct(wti))} (${wti[-1]:.2f}), "
                f"브렌트유 {fmt_pct(pct(brent))} (${brent[-1]:.2f})")
        title = gnews_title('"[뉴욕유가]"') or f"[뉴욕유가] WTI {fmt_pct(pct(wti))}"
        items.append({"title": title, "lines": [line], "star": False, "link": ""})
    except Exception as e:
        print(f"[us] 유가 실패: {e}")
    return items


# ── Gemini ─────────────────────────
def gemini_json(prompt):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])  # 변수로 잡아둬야 도중에 닫히지 않음
    last = None
    for model in ("gemini-2.5-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
        try:
            resp = client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=8192,
                                                   response_mime_type="application/json"))
            return json.loads(resp.text)
        except Exception as e:
            last = e
            print(f"[gemini] {model} 실패: {e}")
            time.sleep(5)
    raise RuntimeError(f"Gemini 실패: {last}")


def pick(articles):
    listing = "\n".join(f"[{i}] ({a['source']}) {a['title']}" + (f" — {a['desc'][:120]}" if a["desc"] else "")
                        for i, a in enumerate(articles))
    prompt = f"""당신은 증권사 리서치센터 인턴입니다. 아래는 어제 국장 마감 이후 나온 국내 경제 기사 목록입니다.
오늘 장 시작 전 투자자가 꼭 알아야 할 기사 {N_DOMESTIC}건을 고르세요.

규칙:
- 서로 다른 이슈로 {N_DOMESTIC}건. 같은 사건은 1건만, [뉴욕증시]·[뉴욕유가]·해외증시 마감 기사는 제외
- 개별 기업·산업 소식(실적, 수주, M&A, 지배구조, 자금조달, 규제·정책, 가격 인상 등)과 국내 정책·금융 이슈를 고루
- 포토·인사·부고·칼럼·사설·광고성 기사, 주가 시황 나열 기사는 제외
- star: 주가나 업종 흐름에 직접 영향이 클 기사만 true (전체의 절반 이하)

JSON 배열만 출력: [{{"id": 번호, "star": true/false}}]

[기사 목록]
{listing}"""
    picks, used = [], set()
    for p in gemini_json(prompt):
        i = int(p.get("id", -1))
        if 0 <= i < len(articles) and i not in used:
            used.add(i)
            picks.append({**articles[i], "star": bool(p.get("star"))})
    return picks[:N_DOMESTIC]


def summarize(picks):
    blocks = []
    for i, p in enumerate(picks):
        body = p.get("body") or p["desc"]
        blocks.append(f"[{i}] 제목: {p['title']}\n본문: {body}")
    prompt = """아래 기사마다 핵심 내용을 2~4개 짧은 문장으로 요약하세요.

규칙:
- 각 문장은 "~함", "~임", "~전망", "~계획" 같은 음슴체로 끝낼 것 (예: "실적 회복 기대감이 커지고 있음")
- 누가·무엇을·얼마나(숫자)·왜를 우선. 본문에 있는 사실과 숫자만 쓰고, 없는 내용은 추가하지 말 것
- 문장마다 70자 이내, 원문 문장을 그대로 옮기지 말고 다시 쓸 것

JSON 배열만 출력: [{"id": 번호, "lines": ["문장1", "문장2", ...]}]

""" + "\n\n".join(blocks)
    out = {int(x["id"]): [str(s).strip() for s in x.get("lines", []) if str(s).strip()]
           for x in gemini_json(prompt) if "id" in x}
    for i, p in enumerate(picks):
        p["lines"] = out.get(i) or ([p["desc"][:120]] if p["desc"] else [])
    return picks


# ── 텔레그램 ─────────────────────────
def render(items, cutoff):
    d = kst_today()
    head = (f"<b>📰 장 시작 전 뉴스 클리핑</b> ({d.month}/{d.day} · "
            f"{cutoff.month}/{cutoff.day} 15:30 이후)")
    blocks = []
    for it in items:
        t = html.escape(it["title"], quote=False)
        if it.get("link", "").startswith("http"):
            t = f'<a href="{html.escape(it["link"])}">{t}</a>'
        mark = "★" if it.get("star") else "  "
        body = " / ".join(html.escape(s, quote=False) for s in it["lines"])
        blocks.append(f'{mark} "{t}"\n-{body}')
    return head, blocks


def send(head, blocks):
    """4096자 넘지 않게 나눠 보낸다"""
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + 2 > 3900:
            msgs.append(cur)
            cur = b
        else:
            cur += "\n\n" + b
    msgs.append(cur)
    for m in msgs:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          data={"chat_id": TG_CHAT, "text": m, "parse_mode": "HTML",
                                "disable_web_page_preview": "true"}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"텔레그램 발송 실패: {r.status_code} {r.text[:300]}")


def send_error(msg):
    if TG_TOKEN and TG_CHAT:
        try:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          data={"chat_id": TG_CHAT, "text": f"뉴스 클리핑 실패\n\n{msg}"[:4000]}, timeout=30)
        except Exception:
            pass


def main():
    off = holiday_name(kst_today())
    if off and not FORCE_SEND and not DRY_RUN:
        print(f"오늘은 {off}, 건너뜀")
        return
    cutoff = since_cutoff()
    articles = collect(cutoff)
    print(f"기사 {len(articles)}건 수집 ({cutoff:%m/%d %H:%M} 이후)")
    domestic = [a for a in articles if not re.search(r"\[(뉴욕증시|뉴욕유가|美증시|유럽증시)", a["title"])]
    try:
        picks = pick(domestic)
        for p in picks:
            p["body"] = article_text(p["link"])
        picks = summarize(picks)
    except Exception as e:
        print(f"AI 선별·요약 실패, 최신 기사 제목으로 대체: {e}")
        picks = [{**a, "star": False, "lines": [a["desc"][:120]] if a["desc"] else []} for a in domestic[:N_DOMESTIC]]
    items = us_items() + picks
    head, blocks = render(items, cutoff)
    print(head + "\n\n" + "\n\n".join(blocks))
    if DRY_RUN:
        return
    send(head, blocks)
    print("발송 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_error(repr(e))
        raise
