from pathlib import Path
import pytest
from route_estimator.config import load_config


@pytest.fixture
def settings(tmp_path):
    source = Path(__file__).parents[1] / "config.example.json"
    path = tmp_path / "config.json"
    path.write_text(source.read_text())
    return load_config(path)
