"""
credit_balance.py v3
──────────────────────
신용공여 잔고 추이(KOFIA FreeSIS) 엑셀 다운로드 후 차트 생성, 텔레그램 전송
- 페이지가 Angular 기반이라 숫자는 화면의 엑셀 다운로드 버튼을 직접 클릭해서 받음
- 받은 엑셀의 신용거래융자 유가증권/코스닥 컬럼으로 matplotlib 차트 생성
- 매일 오전 8시 13분 KST 텔레그램 전송 (평일만)
"""
import os, re, datetime
import requests
import openpyxl
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
from playwright.sync_api import sync_playwright

URL = "https://freesis.kofia.or.kr/stat/FreeSIS.do?parentDivId=MSIS10000000000000&serviceId=STATSCU0100000070"
XLSX_PATH = "/tmp/credit_balance.xlsx"
DEBUG_SHOT = "/tmp/debug_page.png"
DEBUG_DUMP = "/tmp/debug_elements.txt"

# 실패 시 후보가 될 만한 요소들을 텍스트로 그대로 긁어오는 JS, 스크린샷보다 훨씬 정확함
DUMP_JS = """
() => {
    const els = Array.from(document.querySelectorAll(
        'img, button, a, i, [role="button"], span[class*="ico"], div[class*="btn"], div[class*="excel" i]'
    ));
    return els.map(el => {
        const cls = (el.className && typeof el.className === 'string') ? el.className : '';
        const title = el.title || '';
        const alt = el.getAttribute('alt') || '';
        const aria = el.getAttribute('aria-label') || '';
        const src = el.getAttribute('src') || '';
        const text = (el.innerText || '').trim().slice(0, 15);
        if (!cls && !title && !alt && !aria && !src && !text) return null;
        return `${el.tagName}|cls=${cls}|title=${title}|alt=${alt}|aria=${aria}|src=${src}|text=${text}`;
    }).filter(Boolean).slice(0, 120).join('\\n');
}
"""

# 엑셀 다운로드 버튼 후보 셀렉터, 위에서부터 순서대로 시도
# 실제 버튼의 title 속성은 "EXCEL저장" (영문 EXCEL) 이었음, 확인 완료
EXCEL_BUTTON_SELECTORS = [
    "[title='EXCEL저장']",
    "[title*='EXCEL']",
    "[title*='excel' i]",
    "[title*='엑셀']",
    "[aria-label*='엑셀']",
    "[alt*='엑셀']",
    "img[src*='excel']",
    "img[src*='xls']",
    "[class*='excel']",
    "[class*='Excel']",
    "button:has-text('엑셀')",
    "a:has-text('엑셀')",
]


# ── 요일 판단 (KST 기준) ────────────────────────────────────────────────────

def is_kr_business_day() -> bool:
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    return kst_now.weekday() < 5  # 월=0 ... 금=4, 토=5, 일=6


# ── 엑셀 다운로드 ────────────────────────────────────────────────────────

def download_excel() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)  # 데이터 로딩 대기

        # 버튼이 늦게 뜰 수 있어서 1초 간격으로 최대 10번 재시도
        button = None
        for _ in range(10):
            for sel in EXCEL_BUTTON_SELECTORS:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    button = loc.first
                    break
            if button:
                break
            page.wait_for_timeout(1000)

        if button is None:
            dump = page.evaluate(DUMP_JS)
            with open(DEBUG_DUMP, "w", encoding="utf-8") as f:
                f.write(dump)
            page.screenshot(path=DEBUG_SHOT, full_page=True)
            browser.close()
            raise RuntimeError("엑셀 다운로드 버튼을 찾지 못함")

        with page.expect_download(timeout=20000) as download_info:
            button.click()
        download_info.value.save_as(XLSX_PATH)
        browser.close()


# ── 엑셀 파싱 ────────────────────────────────────────────────────────────

def parse_xlsx() -> tuple:
    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
    ws = wb.active

    rows = []
    date_pattern = re.compile(r"\d{4}/\d{2}/\d{2}")
    for row in ws.iter_rows(values_only=True):
        if not row or not row[0] or not date_pattern.match(str(row[0])):
            continue
        d = datetime.datetime.strptime(str(row[0]), "%Y/%m/%d").date()
        yga = int(str(row[2]).replace(",", ""))      # 신용거래융자 유가증권
        kosdaq = int(str(row[3]).replace(",", ""))   # 신용거래융자 코스닥
        rows.append((d, yga, kosdaq))

    rows.sort(key=lambda x: x[0])
    dates = [r[0] for r in rows]
    yga_vals = [r[1] for r in rows]
    kosdaq_vals = [r[2] for r in rows]
    return dates, yga_vals, kosdaq_vals


# ── 차트 생성 ────────────────────────────────────────────────────────────

def setup_korean_font():
    font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
    if os.path.exists(font_path):
        fm.fontManager.addfont(font_path)
        plt.rcParams["font.family"] = fm.FontProperties(fname=font_path).get_name()
    plt.rcParams["axes.unicode_minus"] = False


def plot_chart(dates: list, values: list, label: str, path: str):
    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(dates, values, color="#8B2FC9", linewidth=1.8)
    ax.set_ylim(min(values) * 0.9, max(values) * 1.05)
    ax.grid(axis="y", color="#e5e5e5", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#dddddd")
    ax.yaxis.set_major_formatter(lambda x, _: f"{int(x):,}")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y%m%d"))
    plt.xticks(rotation=90, fontsize=8)
    plt.yticks(fontsize=8)
    ax.tick_params(length=0)
    ax.plot([], [], "o", color="#8B2FC9", label=label)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), frameon=False, fontsize=9)
    plt.tight_layout()
    plt.savefig(path, dpi=150, facecolor="white")
    plt.close()


# ── 텔레그램 전송 ────────────────────────────────────────────────────────

def send_photo(path: str, caption: str = ""):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[Telegram] 환경변수 TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 미설정")
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(path, "rb") as f:
        resp = requests.post(
            url, data={"chat_id": chat_id, "caption": caption}, files={"photo": f}, timeout=30
        )
    if not resp.ok:
        print(f"[Telegram] 전송 실패: {resp.text[:200]}")


def send_text(text: str):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for i in range(0, len(text), 3500):
        requests.post(url, data={"chat_id": chat_id, "text": text[i:i + 3500]}, timeout=15)


def main():
    if not is_kr_business_day():
        kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
        print(f"주말({kst_now.strftime('%Y-%m-%d %A')})이라 전송 생략")
        return

    print("엑셀 다운로드 중...")
    try:
        download_excel()
        dates, yga_vals, kosdaq_vals = parse_xlsx()
    except Exception as e:
        send_text(f"신용잔고 캡처 실패: {e}")
        if os.path.exists(DEBUG_DUMP):
            with open(DEBUG_DUMP, encoding="utf-8") as f:
                send_text(f.read())
        if os.path.exists(DEBUG_SHOT):
            send_photo(DEBUG_SHOT, caption="실패 시점 화면")
        raise

    print(f"데이터 {len(dates)}건 확보, 차트 생성 중...")
    setup_korean_font()
    plot_chart(dates, yga_vals, "신용거래융자-유가증권", "/tmp/chart_yga.png")
    plot_chart(dates, kosdaq_vals, "신용거래융자-코스닥", "/tmp/chart_kosdaq.png")

    today = dates[-1].strftime("%Y/%m/%d")
    send_photo("/tmp/chart_yga.png", caption=f"신용거래융자 유가증권 ({today} 기준)")
    send_photo("/tmp/chart_kosdaq.png", caption=f"신용거래융자 코스닥 ({today} 기준)")
    print("완료")


if __name__ == "__main__":
    main()
