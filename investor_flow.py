"""
KRX 투자자별 누적 순매수 텔레그램 리포트
매 거래일 장마감 후 실행, 롤링 24개월 기준 차트 2장 발송
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import pandas as pd
import requests
from pykrx import stock

# ------------------------------------------------------------------
# 설정
# ------------------------------------------------------------------
CSV_PATH = "data/investor_flow.csv"
OUT_DIR = "out"
MARKETS = ["KOSPI", "KOSDAQ"]
ROLL_MONTHS = 24
BACKFILL_MONTHS = 25  # 롤링 24개월 + 여유 1개월, 최초 실행 시에만 사용
UNIT = 1e8  # 원 -> 억원
CHUNK_DAYS = 180     # 장기 구간 분할 단위
CHUNK_SLEEP = 3      # 청크 사이 대기 초

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
FORCE_SEND = os.environ.get("FORCE_SEND", "").lower() in ("1", "true", "yes")

INSTITUTION_PARTS = ["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금"]
FOREIGN_PARTS = ["외국인", "기타외국인"]

COLORS = {
    "개인": "#00b050",
    "외국인": "#ff0000",
    "기관": "#0070c0",
    "연기금": "#808080",
}


# ------------------------------------------------------------------
# 데이터 수집
# ------------------------------------------------------------------
def check_env():
    """
    KRX 정보데이터시스템이 2025-12-27부터 회원제로 전환됨
    로그인 없이는 JSON 대신 로그아웃 페이지가 내려와서 파싱이 터짐
    """
    missing = [k for k in ("KRX_ID", "KRX_PW") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} 미설정. data.krx.co.kr 회원가입 후 "
            "리포지토리 Secrets에 등록 필요 (가입 및 조회 모두 무료)"
        )

    missing = [k for k in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} 미설정. Secrets 이름이 정확히 일치하는지 확인 필요"
        )


def fetch_chunk(fromdate, todate, market, retries=4):
    """단일 구간 조회, 억원 단위 반환"""
    last_err = None
    for i in range(retries):
        try:
            df = stock.get_market_trading_value_by_date(
                fromdate, todate, market, detail=True
            )
            if df is None or df.empty:
                raise ValueError("빈 응답 (로그인 세션 확인 필요)")

            out = pd.DataFrame(index=df.index)
            out["개인"] = df["개인"]
            out["외국인"] = sum(
                df[c] for c in FOREIGN_PARTS if c in df.columns
            )
            out["기관"] = sum(
                df[c] for c in INSTITUTION_PARTS if c in df.columns
            )
            out["연기금"] = df["연기금"] if "연기금" in df.columns else 0

            out = out / UNIT
            out = out.round(2)
            out.index = pd.to_datetime(out.index)
            out.index.name = "date"
            out.insert(0, "market", market)
            return out.reset_index()
        except Exception as e:
            last_err = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"{market} {fromdate}~{todate} 조회 실패: {last_err}")


def fetch_range(fromdate, todate, market):
    """
    긴 구간은 6개월 청크로 쪼개서 순차 조회
    KRX가 과도한 접속에 대해 계정 단위 차단을 하므로 사이에 텀을 둠
    """
    start = datetime.strptime(fromdate, "%Y%m%d")
    end = datetime.strptime(todate, "%Y%m%d")

    parts, cursor = [], start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS), end)
        parts.append(
            fetch_chunk(cursor.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"), market)
        )
        cursor = chunk_end + timedelta(days=1)
        if cursor <= end:
            time.sleep(CHUNK_SLEEP)

    return pd.concat(parts, ignore_index=True)


def load_csv():
    if not os.path.exists(CSV_PATH):
        return pd.DataFrame(
            columns=["date", "market", "개인", "외국인", "기관", "연기금"]
        )
    df = pd.read_csv(CSV_PATH, parse_dates=["date"])
    return df


def save_csv(df):
    df = df.sort_values(["date", "market"]).drop_duplicates(
        ["date", "market"], keep="last"
    )
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    return df


def update():
    """
    CSV가 없으면 전 기간 백필, 있으면 최근 데이터만 append
    직전 실행 대비 새 거래일이 붙었을 때만 발송, 없으면 (None, None)
    """
    hist = load_csv()
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    first_run = hist.empty
    prev_latest = None if first_run else hist["date"].max()

    if first_run:
        start = (now - timedelta(days=int(BACKFILL_MONTHS * 30.5))).strftime("%Y%m%d")
        print(f"CSV 없음, {start} ~ {today} 백필")
    else:
        start = (now - timedelta(days=7)).strftime("%Y%m%d")

    new = pd.concat(
        [fetch_range(start, today, mkt) for mkt in MARKETS], ignore_index=True
    )
    if new.empty:
        if not first_run and FORCE_SEND:
            print("신규 조회분 없음, 기존 CSV로 강제 발송")
            return hist, hist["date"].max()
        print("조회 결과 없음, 종료")
        return None, None

    merged = save_csv(pd.concat([hist, new], ignore_index=True))
    latest = merged["date"].max()

    if FORCE_SEND:
        print(f"강제 발송 모드, 최신 {latest.date()}")
        return merged, latest

    if not first_run and latest <= prev_latest:
        print(f"신규 거래일 없음 (최신 {latest.date()}), 발송 없이 종료")
        return None, None

    return merged, latest


# ------------------------------------------------------------------
# 가공
# ------------------------------------------------------------------
def build_frames(df):
    """시장별 일별 프레임 + 합산 프레임 생성, 롤링 24개월 컷"""
    cutoff = df["date"].max() - pd.DateOffset(months=ROLL_MONTHS)
    df = df[df["date"] >= cutoff].copy()

    cols = ["개인", "외국인", "기관", "연기금"]
    frames = {}
    for mkt in MARKETS:
        sub = df[df["market"] == mkt].set_index("date")[cols].sort_index()
        frames[mkt] = sub

    total = (
        df.groupby("date")[cols].sum().sort_index()
    )
    frames["합산"] = total
    return frames


def weekly_foreign(daily):
    """외국인 주간 누적 + 4주 이동평균"""
    wk = daily["외국인"].resample("W-FRI").sum()
    cum = wk.cumsum()
    ma = cum.rolling(4).mean()
    return cum, ma


# ------------------------------------------------------------------
# 차트
# ------------------------------------------------------------------
def setup_font():
    for name in ["NanumGothic", "NanumBarunGothic", "Malgun Gothic", "AppleGothic"]:
        try:
            matplotlib.font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.family"] = name
            break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def fmt_axis(ax):
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"{x:,.0f}")
    )
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y-%m"))
    ax.tick_params(axis="both", labelsize=8)
    ax.axhline(0, color="#999999", linewidth=0.7)
    ax.grid(True, alpha=0.25, linewidth=0.5)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)


def chart_all_investors(frames, path, asof):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    labels = ["KOSPI", "KOSDAQ", "합산"]
    for ax, key in zip(axes, labels):
        d = frames[key].cumsum()
        for col in ["연기금", "외국인", "기관", "개인"]:
            ax.plot(d.index, d[col], label=col, color=COLORS[col], linewidth=1.4)
        ax.set_title(key, fontsize=11, fontweight="bold", pad=8)
        fmt_axis(ax)
        ax.legend(fontsize=8, ncol=2, loc="best",
                  framealpha=0.8, edgecolor="none")
    fig.suptitle(
        f"투자자별 누적 순매수 (억원, 최근 {ROLL_MONTHS}개월)   기준일 {asof}",
        fontsize=13, fontweight="bold", y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(path, dpi=130)
    plt.close(fig)


def chart_foreign(frames, path, asof):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    labels = ["KOSPI", "KOSDAQ", "합산"]
    for ax, key in zip(axes, labels):
        cum, ma = weekly_foreign(frames[key])
        ax.plot(cum.index, cum, color="#ff0000", linewidth=1.0,
                marker="s", markersize=2.6, label="외국인 누적순매수")
        ax.plot(ma.index, ma, color="#0070c0", linewidth=1.6, label="4w ma")
        ax.set_title(key, fontsize=11, fontweight="bold", pad=8)
        fmt_axis(ax)
        ax.legend(fontsize=8, loc="best", framealpha=0.8, edgecolor="none")
    fig.suptitle(
        f"외국인 누적 순매수 주간 (억원, 최근 {ROLL_MONTHS}개월)   기준일 {asof}",
        fontsize=13, fontweight="bold", y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(path, dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------
# 캡션
# ------------------------------------------------------------------
def build_caption(frames, asof):
    lines = [f"<b>투자자 수급 {asof}</b>", ""]
    for key in ["KOSPI", "KOSDAQ", "합산"]:
        f = frames[key]["외국인"]
        d1, d5, d20 = f.iloc[-1], f.tail(5).sum(), f.tail(20).sum()
        lines.append(
            f"{key} 외국인 {d1:+,.0f}억원 (5일 {d5:+,.0f}억원 / 20일 {d20:+,.0f}억원)"
        )
    lines.append("")
    for key in ["KOSPI", "KOSDAQ"]:
        g = frames[key]
        lines.append(
            f"{key} 당일 개인 {g['개인'].iloc[-1]:+,.0f}억원 / "
            f"기관 {g['기관'].iloc[-1]:+,.0f}억원 / "
            f"연기금 {g['연기금'].iloc[-1]:+,.0f}억원"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------
# 발송
# ------------------------------------------------------------------
def send(paths, caption):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMediaGroup"
    media, files = [], {}
    for i, p in enumerate(paths):
        key = f"photo{i}"
        files[key] = open(p, "rb")
        item = {"type": "photo", "media": f"attach://{key}"}
        if i == 0:
            item["caption"] = caption[:1024]
            item["parse_mode"] = "HTML"
        media.append(item)

    r = requests.post(
        url,
        data={"chat_id": TG_CHAT, "media": json.dumps(media)},
        files=files,
        timeout=60,
    )
    for f in files.values():
        f.close()
    if not r.ok:
        raise RuntimeError(f"텔레그램 발송 실패: {r.status_code} {r.text}")
    print("발송 완료")


def send_error(msg):
    """실패를 조용히 넘기지 않고 텔레그램으로 알림"""
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={
                "chat_id": TG_CHAT,
                "text": f"investor_flow 실패\n\n{msg}"[:4000],
            },
            timeout=30,
        )
    except Exception:
        pass


# ------------------------------------------------------------------
def main():
    check_env()
    setup_font()
    os.makedirs(OUT_DIR, exist_ok=True)

    df, latest = update()
    if df is None:
        sys.exit(0)

    asof = latest.strftime("%Y-%m-%d")
    frames = build_frames(df)

    p1 = f"{OUT_DIR}/investors.png"
    p2 = f"{OUT_DIR}/foreign.png"
    chart_all_investors(frames, p1, asof)
    chart_foreign(frames, p2, asof)

    send([p1, p2], build_caption(frames, asof))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_error(f"{type(e).__name__}: {e}")
        raise
