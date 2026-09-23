"""
한국 영업일 판단 (KST 기준)
- 주말, 법정 공휴일(설날, 추석 등 음력 명절과 대체공휴일, 선거일 포함) 제외
- 12월 31일은 공휴일은 아니지만 증권시장 휴장일이라 함께 제외
"""
import datetime

import holidays

KST = datetime.timezone(datetime.timedelta(hours=9))


def kst_today() -> datetime.date:
    return datetime.datetime.now(KST).date()


def holiday_name(d: datetime.date) -> str | None:
    """휴일이면 이름, 영업일이면 None"""
    if d.weekday() >= 5:
        return "주말"
    if d.month == 12 and d.day == 31:
        return "연말 휴장일"
    return holidays.KR(years=d.year).get(d)


def is_kr_business_day(d: datetime.date | None = None) -> bool:
    return holiday_name(d or kst_today()) is None
