"""
credit_balance.py v1
──────────────────────
신용공여 잔고 추이 차트 캡처 후 텔레그램 전송
- FreeSIS(금융투자협회)는 Angular 기반이라 데이터가 JS로 렌더링됨
- 숫자 API 대신 실제 화면 차트를 스크린샷으로 캡처해서 그대로 전송
- 매일 오전 8시 13분 KST 텔레그램 전송 (평일만)
"""
import os, datetime
import requests
from playwright.sync_api import sync_playwright

URL = "https://freesis.kofia.or.kr/stat/FreeSIS.do?parentDivId=MSIS10000000000000&serviceId=STATSCU0100000070"


# ── 요일 판단 (KST 기준) ────────────────────────────────────────────────────

def is_kr_business_day() -> bool:
    kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    return kst_now.weekday() < 5  # 월=0 ... 금=4, 토=5, 일=6


# ── 차트 캡처 ────────────────────────────────────────────────────────────

def capture() -> list:
    """차트 영역을 스크린샷으로 저장하고 파일 경로 리스트 반환"""
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(4000)  # 차트 렌더링 대기

        # 국내 금융 통계 사이트 대부분 Highcharts 사용, 우선 이 셀렉터로 시도
        charts = page.locator(".highcharts-container")
        count = charts.count()

        if count > 0:
            for i in range(count):
                path = f"/tmp/credit_chart_{i}.png"
                charts.nth(i).screenshot(path=path)
                paths.append(path)
        else:
            # 셀렉터가 안 맞아도 전체 화면으로 대체, 최소한 실패는 안 하게
            path = "/tmp/credit_chart_full.png"
            page.screenshot(path=path, full_page=True)
            paths.append(path)

        browser.close()
    return paths


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
    requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)


def main():
    if not is_kr_business_day():
        kst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
        print(f"주말({kst_now.strftime('%Y-%m-%d %A')})이라 전송 생략")
        return

    print("차트 캡처 중...")
    try:
        paths = capture()
    except Exception as e:
        send_text(f"신용잔고 차트 캡처 실패: {e}")
        raise

    print(f"캡처 완료, {len(paths)}개 이미지 전송 중...")
    for path in paths:
        send_photo(path, caption="신용공여 잔고 추이 (KOFIA FreeSIS)")
    print("완료")


if __name__ == "__main__":
    main()
