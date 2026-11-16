import pytest
from config.settings import clamp_volume


@pytest.mark.parametrize("raw,expected", [
    (80, 80), (0, 0), (100, 100), (-5, 0), (250, 100),
    ("35", 35), (42.7, 42), (None, 80), ("abc", 80), ([], 80),
])
def test_clamp_volume(raw, expected):
    assert clamp_volume(raw) == expected
