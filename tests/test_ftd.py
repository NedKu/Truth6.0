import pandas as pd
import pytest

from ftd import completed_sessions, replay_ftd


def sequence(extra=()):
    closes = [100, 98, 96, 90, 91, 92, 93, 95] + [96] * 10 + list(extra)
    data = pd.DataFrame({
        "close": [float(x) for x in closes], "low": [x - 0.5 for x in closes],
        "volume": [100] * len(closes), "gap_before": False,
    }, index=pd.bdate_range("2026-01-05", periods=len(closes)))
    data.iloc[7, data.columns.get_loc("volume")] = 200
    return data


def test_confirmed_event_survives_more_than_25_sessions():
    result = replay_ftd(sequence([96] * 40))
    assert result["ftd_valid"] is True
    assert result["days_since_ftd"] > 25
    assert len([e for e in result["events"] if e["kind"] == "FTD 成立"]) == 1


def test_failure_remains_blocked_after_price_recovers_and_event_leaves_window():
    result = replay_ftd(sequence([92, 96] + [96] * 40))
    assert result["stop_investing"] is True
    assert result["policy_status"] == "已觸發，持續阻擋"
    assert result["failure_date"] == sequence([92]).index[-1]


def test_technical_low_failure_is_separate_from_two_percent_policy():
    result = replay_ftd(sequence([94]))
    assert result["technical_status"] == "失效"
    assert result["policy_status"] == "未觸發"
    assert result["stop_investing"] is True


def test_equal_low_and_equal_policy_line_are_not_strict_breaks():
    data = sequence([95])
    data.iloc[-1, data.columns.get_loc("low")] = 94.5
    assert replay_ftd(data)["stop_investing"] is False
    data = sequence([95 * .98])
    result = replay_ftd(data)
    assert result["policy_status"] == "未觸發"
    assert result["technical_status"] == "失效"


def test_new_ftd_in_a_new_rally_releases_latched_failure():
    data = sequence([92, 93, 94, 95, 97])
    data.iloc[-1, data.columns.get_loc("volume")] = 201
    result = replay_ftd(data)
    assert result["ftd_valid"] is True
    assert result["ftd_day"] == data.index[-1]
    assert result["failure_date"] is None
    assert result["event"]["rally_start"] > data.index[-5]
    assert len([e for e in result["events"] if e["kind"] == "FTD 成立"]) == 2


def test_two_percent_rule_can_trigger_after_earlier_technical_failure():
    result = replay_ftd(sequence([94, 92]))
    assert result["technical_status"] == "失效"
    assert result["policy_status"] == "已觸發，持續阻擋"
    assert len([e for e in result["events"] if e["kind"] == "2% 策略風控觸發"]) == 1


@pytest.mark.parametrize("bad_volume", [100, 99])
def test_ftd_requires_strictly_higher_volume(bad_volume):
    data = sequence()
    data.iloc[7, data.columns.get_loc("volume")] = bad_volume
    assert replay_ftd(data)["ftd_triggered"] is False


def test_exact_one_point_seven_percent_qualifies_on_day_four():
    data = sequence()
    data.iloc[7, data.columns.get_loc("close")] = 93 * 1.017
    data.iloc[7, data.columns.get_loc("low")] = 94
    assert replay_ftd(data)["ftd_triggered"] is True


def test_ftd_on_day_three_does_not_qualify():
    data = sequence()
    data.iloc[6, data.columns.get_loc("close")] = 94
    data.iloc[6, data.columns.get_loc("volume")] = 200
    data.iloc[7, data.columns.get_loc("volume")] = 100
    assert replay_ftd(data)["ftd_triggered"] is False


def test_new_low_restarts_attempt_and_does_not_count_old_day_four():
    data = sequence()
    data.iloc[6, data.columns.get_loc("low")] = 89
    result = replay_ftd(data)
    assert result["ftd_triggered"] is False
    assert any(e["kind"] == "反彈嘗試跌破 Day 1 低點" for e in result["events"])


def test_gap_after_known_event_is_pending_not_a_release():
    data = sequence()
    data.iloc[-2, data.columns.get_loc("gap_before")] = True
    assert replay_ftd(data)["stop_investing"] is None


def test_stale_latest_bar_never_authorizes_deployment():
    result = replay_ftd(sequence(), {"stale": True, "expected_date": "2026-11-27", "latest_date": "2026-11-25"})
    assert result["stop_investing"] is None
    assert result["ftd_valid"] is False


def test_insufficient_history_is_pending():
    assert replay_ftd(sequence().iloc[:5])["stop_investing"] is None


def raw(dates, timezone=None):
    dates = pd.to_datetime(dates)
    if timezone:
        dates = dates.tz_localize(timezone)
    return pd.DataFrame({"Close": 100., "Low": 99., "Volume": 100.}, index=dates)


def test_us_thanksgiving_is_not_an_extra_rally_session():
    frame = raw(["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"])
    data, quality = completed_sessions(frame, now="2026-11-27T18:16:00Z")
    assert len(data) == 4
    assert pd.Timestamp("2026-11-26") not in data.index
    assert quality["gap_count"] == 0
    assert quality["stale"] is False


def test_us_early_close_includes_bar_only_after_close_and_data_buffer():
    frame = raw(["2026-11-25", "2026-11-27"])
    before, _ = completed_sessions(frame, now="2026-11-27T17:59:00Z")
    after, _ = completed_sessions(frame, now="2026-11-27T18:16:00Z")
    assert before.index[-1] == pd.Timestamp("2026-11-25")
    assert after.index[-1] == pd.Timestamp("2026-11-27")


def test_native_dates_are_inner_joined_without_filling_missing_proxy_volume():
    price = raw(["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"], "America/New_York")
    volume = raw(["2026-11-23", "2026-11-25", "2026-11-27"])
    data, quality = completed_sessions(price, volume, now="2026-11-27T18:16:00Z")
    assert pd.Timestamp("2026-11-24") not in data.index
    assert quality["gap_count"] == 1
    assert data.loc["2026-11-25", "gap_before"]


def test_latest_missing_session_is_reported_as_stale():
    _, quality = completed_sessions(raw(["2026-11-25"]), now="2026-11-27T18:16:00Z")
    assert quality["stale"] is True
    assert quality["expected_date"] == "2026-11-27"


def test_taiwan_in_progress_bar_is_excluded():
    data, quality = completed_sessions(raw(["2026-11-26", "2026-11-27"], "Asia/Taipei"), calendar="XTAI", now="2026-11-27T04:00:00Z")
    assert data.index[-1] == pd.Timestamp("2026-11-26")
    assert not quality["stale"]


def test_zero_volume_and_missing_prices_are_not_manufactured_into_sessions():
    frame = raw(["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"])
    frame.loc["2026-11-24", "Volume"] = 0
    frame.loc["2026-11-25", "Close"] = float("nan")
    data, quality = completed_sessions(frame, now="2026-11-27T18:16:00Z")
    assert len(data) == 2
    assert quality["gap_count"] == 1


def test_higher_local_bottom_starts_a_new_rally_without_erasing_old_failure():
    data = sequence([92] + [110] * 30 + [106, 107, 108, 109, 111])
    data.iloc[-1, data.columns.get_loc("volume")] = 200
    result = replay_ftd(data)
    assert result["ftd_day"] == data.index[-1]
    assert result["event"]["rally_day"] == 4
    assert result["stop_investing"] is False
    assert any(e["kind"] == "2% 策略風控觸發" for e in result["events"])


def test_trend_can_record_a_new_ftd_above_a_much_older_valid_event():
    data = sequence([110] * 30 + [106, 107, 108, 109, 111])
    data.iloc[-1, data.columns.get_loc("volume")] = 200
    result = replay_ftd(data)
    assert result["ftd_day"] == data.index[-1]
    assert result["ftd_valid"] is True


@pytest.mark.parametrize("guard,expected", [(True, "GUARD_BLOCKED"), (None, "PENDING_VERIFICATION"), (False, "BUY_TO_TARGET")])
def test_rebalance_cannot_bypass_the_guard(guard, expected):
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "truthasset.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "evaluate_rebalance_action")
    namespace = {"STOCK_REBALANCE_BAND": 5, "GOLD_REBALANCE_BAND": 2, "BOND_REBALANCE_BAND": 5}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "truthasset.py", "exec"), namespace)
    assert namespace["evaluate_rebalance_action"]("VT", 20, 40, {"stop_investing": guard}) == expected
