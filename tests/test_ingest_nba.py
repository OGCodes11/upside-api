import pandas as pd
import pytest

from ingest import nba

FAKE_ROWS = pd.DataFrame([{"PLAYER_ID": 1, "PLAYER_NAME": "Example Player", "PTS": 18.4}])


def test_pull_season_writes_csv(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nba, "fetch_season", lambda season: calls.append(season) or FAKE_ROWS)

    path = nba.pull_season("2025-26", raw_dir=tmp_path)

    assert calls == ["2025-26"]
    assert pd.read_csv(path).to_dict("records") == FAKE_ROWS.to_dict("records")


def test_pull_season_uses_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nba, "fetch_season", lambda season: calls.append(season) or FAKE_ROWS)

    nba.pull_season("2025-26", raw_dir=tmp_path)
    nba.pull_season("2025-26", raw_dir=tmp_path)

    assert calls == ["2025-26"]  # second run read the file, no new request


def test_pull_season_refresh_fetches_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(nba, "fetch_season", lambda season: calls.append(season) or FAKE_ROWS)

    nba.pull_season("2025-26", raw_dir=tmp_path)
    nba.pull_season("2025-26", raw_dir=tmp_path, refresh=True)

    assert calls == ["2025-26", "2025-26"]


def test_season_range():
    assert nba.season_range("2010-11", "2012-13") == ["2010-11", "2011-12", "2012-13"]
    assert nba.season_range("1999-00", "2000-01") == ["1999-00", "2000-01"]
    assert nba.season_range("2025-26", "2025-26") == ["2025-26"]
    with pytest.raises(ValueError):
        nba.season_range("2025-26", "2024-25")


def test_pull_season_rejects_empty_response(tmp_path, monkeypatch):
    monkeypatch.setattr(nba, "fetch_season", lambda season: pd.DataFrame())

    with pytest.raises(RuntimeError):
        nba.pull_season("2025-26", raw_dir=tmp_path)

    assert not nba.csv_path("2025-26", tmp_path).exists()
