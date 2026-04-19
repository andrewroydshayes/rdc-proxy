"""Plugin loader: entry-point discovery + start(state) contract."""

from unittest.mock import MagicMock, patch

from rdc_proxy import plugins
from rdc_proxy.state import GeneratorState


class FakeGoodPlugin:
    def __init__(self):
        self.started_with = None

    def start(self, state):
        self.started_with = state
        state.update_side_channel("fake", {"hello": "world"})


class FakeBadPlugin:
    def start(self, state):
        raise RuntimeError("boom")


def _fake_entry_point(name, cls):
    ep = MagicMock()
    ep.name = name
    ep.load = MagicMock(return_value=cls)
    return ep


def test_no_plugins_installed_is_fine():
    state = GeneratorState()
    with patch("importlib.metadata.entry_points", return_value=[]):
        started = plugins.load_plugins(state)
    assert started == []


def test_good_plugin_is_loaded_and_started():
    state = GeneratorState()
    eps = [_fake_entry_point("fake", FakeGoodPlugin)]
    with patch("importlib.metadata.entry_points", return_value=eps):
        started = plugins.load_plugins(state)
    assert "fake" in started
    # Side effect occurred
    assert state.get_side_channels()["fake"] == {"hello": "world"}


def test_bad_plugin_does_not_crash_loader():
    state = GeneratorState()
    eps = [
        _fake_entry_point("bad", FakeBadPlugin),
        _fake_entry_point("good", FakeGoodPlugin),
    ]
    with patch("importlib.metadata.entry_points", return_value=eps):
        started = plugins.load_plugins(state)
    # Bad one excluded, good one still started
    assert "bad" not in started
    assert "good" in started
    assert state.get_side_channels().get("fake") == {"hello": "world"}
