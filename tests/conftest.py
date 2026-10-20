from dataclasses import replace
from pathlib import Path
import pytest
from route_estimator.config import load_config


@pytest.fixture
def settings(tmp_path):
    source = Path(__file__).parents[1] / "config.example.json"
    path = tmp_path / "config.json"
    path.write_text(source.read_text())
    loaded = load_config(path)
    # 既存テストは厳密モード（番地まで必須）で検証する。フォールバックは fallback_settings で検証。
    return replace(loaded, geocoding=replace(loaded.geocoding, allow_approximate=False))


@pytest.fixture
def fallback_settings(tmp_path):
    source = Path(__file__).parents[1] / "config.example.json"
    path = tmp_path / "config.json"
    path.write_text(source.read_text())
    return load_config(path)
