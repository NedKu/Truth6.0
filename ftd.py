"""Replay FTD evidence without forward-filled sessions or rolling event expiry."""

from functools import lru_cache

import exchange_calendars as xcals
import numpy as np
import pandas as pd


FTD_VERSION = "FTD replay v2 · 2026-11-28"
MIN_GAIN = 0.017
CANCEL_FACTOR = 0.98
FIRST_DAY = 4
LAST_DAY = 12
BOTTOM_LOOKBACK = 25


@lru_cache(maxsize=32)
def _calendar(name, start, end):
    return xcals.get_calendar(name, start=start, end=end)


def _dated(frame, timezone):
    result = frame.copy()
    dates = pd.DatetimeIndex(pd.to_datetime(result.index))
    if dates.tz is not None:
        dates = dates.tz_convert(timezone).tz_localize(None)
    result.index = dates.normalize()
    return result.loc[~result.index.duplicated(keep="last")].sort_index()


def completed_sessions(prices, volumes=None, calendar="XNYS", now=None):
    """Inner-join native dates and verify them against actual session closes."""
    empty = pd.DataFrame(columns=["close", "low", "volume", "gap_before"])
    if prices is None or prices.empty:
        return empty, {"error": "價格歷史未取得", "stale": True}
    timezone = "Asia/Taipei" if calendar == "XTAI" else "America/New_York"
    try:
        price = _dated(prices[["Close", "Low"]], timezone)
        volume_source = prices if volumes is None else volumes
        volume = _dated(volume_source[["Volume"]], timezone)
        data = price.join(volume, how="inner").rename(
            columns={"Close": "close", "Low": "low", "Volume": "volume"}
        )
        data = data.apply(pd.to_numeric, errors="coerce")
        valid = np.isfinite(data).all(axis=1)
        valid &= (data["close"] > 0) & (data["low"] > 0)
        valid &= (data["low"] <= data["close"]) & (data["volume"] > 0)
        data = data.loc[valid]
        if data.empty:
            return empty, {"error": "有效對齊價量未取得", "stale": True}
        now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
        now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
        end = now.tz_convert(timezone).normalize().tz_localize(None)
        cal = _calendar(calendar, str(data.index.min().date()), str((end + pd.Timedelta(days=7)).date()))
        schedule = cal.schedule
        eligible = schedule.loc[schedule["close"] + pd.Timedelta(minutes=15) <= now]
        expected = pd.DatetimeIndex(eligible.index).tz_localize(None).normalize()
        data = data.loc[data.index.isin(expected)]
        if data.empty or expected.empty:
            return empty, {"error": "尚無可核實的已收盤資料", "stale": True}
        ordinals = expected.get_indexer(data.index)
        data["gap_before"] = np.r_[False, np.diff(ordinals) != 1]
        return data, {
            "error": None,
            "stale": data.index[-1] != expected[-1],
            "expected_date": expected[-1].date().isoformat(),
            "latest_date": data.index[-1].date().isoformat(),
            "start_date": data.index[0].date().isoformat(),
            "gap_count": int(data["gap_before"].sum()),
            "calendar": calendar,
        }
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return empty, {"error": f"交易日／價量資料待核實：{exc}", "stale": True}


def replay_ftd(data, quality=None):
    """A failed event remains blocked until a new rally produces a valid FTD."""
    quality = {} if quality is None else quality
    result = {
        "version": FTD_VERSION, "status": "Pending verification",
        "ftd_triggered": False, "ftd_valid": False, "stop_investing": None,
        "technical_status": "未確認", "policy_status": "待核實",
        "ftd_day": None, "ftd_low": None, "ftd_price": None,
        "cancel_price": None, "days_since_ftd": None,
        "rally_day": None, "rally_start": None, "bottom_date": None,
        "bottom_price": None, "event": None, "events": [],
        "quality": quality, "candidate": None, "failure_date": None,
        "message": quality.get("error") or "歷史不足，尚未重建有效 FTD 狀態",
    }
    if data is None or len(data) < 15:
        return result
    rows = list(data.itertuples())
    bottom, bottom_date = float(rows[0].low), rows[0].Index
    start = None
    rally_low = None
    event = None
    technical_failed = False
    policy_failed = False
    known = False
    failure_index = None
    candidate = None
    events = []
    for i in range(1, len(rows)):
        row, previous = rows[i], rows[i - 1]
        if bool(getattr(row, "gap_before", False)):
            known = False
            event = None
            technical_failed = policy_failed = False
            failure_index = None
            bottom, bottom_date = float(row.low), row.Index
            start = None
            events.append({"date": row.Index, "kind": "資料缺口，狀態待重建"})
            continue
        fresh_failure = False
        if event is not None:
            if not technical_failed and row.low < event["low"]:
                technical_failed = True
                events.append({"date": row.Index, "kind": "FTD 技術失效", "ftd_date": event["date"], "observed": float(row.low), "threshold": event["low"]})
                fresh_failure = failure_index is None
            if not policy_failed and row.close < event["price"] * CANCEL_FACTOR:
                policy_failed = True
                events.append({"date": row.Index, "kind": "2% 策略風控觸發", "ftd_date": event["date"], "observed": float(row.close), "threshold": event["price"] * CANCEL_FACTOR})
                fresh_failure = fresh_failure or failure_index is None
            if fresh_failure:
                failure_index = i
                bottom, bottom_date = float(row.low), row.Index
                start = None
                rally_low = None
        if fresh_failure:
            continue
        local_floor = min(float(r.low) for r in rows[max(0, i - BOTTOM_LOOKBACK + 1):i])
        new_low = row.low < bottom or row.low < local_floor
        attempt_broken = start is not None and row.low < rally_low
        if new_low or attempt_broken:
            if start is not None:
                events.append({"date": row.Index, "kind": "反彈嘗試跌破 Day 1 低點", "rally_start": rows[start].Index, "observed": float(row.low), "threshold": rally_low})
            bottom, bottom_date = float(row.low), row.Index
            start = None
        if start is None and row.close > previous.close:
            start, rally_low = i, float(row.low)
        if start is None:
            continue
        day = i - start + 1
        gain = float(row.close / previous.close - 1)
        candidate = {
            "date": row.Index, "rally_day": day,
            "previous_close": float(previous.close), "price": float(row.close),
            "change_pct": gain * 100, "previous_volume": float(previous.volume),
            "volume": float(row.volume), "volume_ratio": float(row.volume / previous.volume),
            "day_ok": FIRST_DAY <= day <= LAST_DAY,
            "price_ok": gain >= MIN_GAIN - 1e-12,
            "volume_ok": row.volume > previous.volume,
        }
        new_attempt = event is None or rows[start].Index > event["rally_start"]
        if new_attempt and candidate["day_ok"] and candidate["price_ok"] and candidate["volume_ok"]:
            event = dict(candidate, low=float(row.low), rally_start=rows[start].Index,
                         bottom_date=bottom_date, bottom_price=bottom, index=i)
            events.append(dict(event, kind="FTD 成立"))
            known = True
            technical_failed = policy_failed = False
            failure_index = None
    result.update({
        "event": event, "events": events, "candidate": candidate,
        "rally_day": len(rows) - start if start is not None else None,
        "rally_start": rows[start].Index if start is not None else None,
        "bottom_date": bottom_date, "bottom_price": bottom,
        "failure_date": rows[failure_index].Index if failure_index is not None else None,
    })
    if event is not None:
        result.update({"ftd_triggered": True, "ftd_day": event["date"],
                       "ftd_low": event["low"], "ftd_price": event["price"],
                       "cancel_price": event["price"] * CANCEL_FACTOR,
                       "days_since_ftd": len(rows) - event["index"] - 1})
    if quality.get("error") or quality.get("stale") or not known:
        result["message"] = quality.get("error") or (
            f"最新已收盤日應為 {quality.get('expected_date')}，取得 {quality.get('latest_date')}；Guard 待核實"
            if quality.get("stale") else "歷史起點／缺口後尚無有效 FTD，Guard 待核實"
        )
        return result
    result["technical_status"] = "失效" if technical_failed else "有效"
    result["policy_status"] = "已觸發，持續阻擋" if policy_failed else "未觸發"
    blocked = technical_failed or policy_failed
    result["stop_investing"] = blocked
    result["ftd_valid"] = not blocked
    result["status"] = "Failed" if blocked else "Confirmed"
    if blocked:
        causes = []
        if technical_failed:
            causes.append(f"盤中跌破 FTD 日低點 {event['low']:.2f}")
        if policy_failed:
            causes.append(f"收盤嚴格低於 FTD 收盤×0.98＝{event['price'] * CANCEL_FACTOR:.2f}")
        result["message"] = f"🔴 Guard 持續阻擋：{'；'.join(causes)}；失效日起 {result['failure_date'].date()}，須新一輪有效 FTD 才解除。"
    else:
        result["message"] = f"✅ FTD 有效：{event['date'].date()}，反彈第 {event['rally_day']} 個交易日，漲 {event['change_pct']:.2f}%，量比 {event['volume_ratio']:.2f}；技術低點 {event['low']:.2f}，2% 策略線 {event['price'] * CANCEL_FACTOR:.2f}。"
    return result
