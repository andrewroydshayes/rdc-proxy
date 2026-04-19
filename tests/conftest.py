"""Shared pytest fixtures: path setup + config isolation."""

import os
import sys

# Make the rdc_proxy package importable without installing
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from rdc_proxy import config as cfg_mod  # noqa: E402
from rdc_proxy import state as state_mod  # noqa: E402


@pytest.fixture
def isolated_cfg(tmp_path, monkeypatch):
    """Give each test a fresh CFG pointing at a tmp config_dir."""
    cfg_mod.CFG.clear()
    cfg_mod.CFG.update(dict(cfg_mod.DEFAULT_CONFIG))
    cfg_mod.CFG["config_dir"] = str(tmp_path)
    yield cfg_mod.CFG


@pytest.fixture
def fresh_state():
    """Return a fresh GeneratorState independent of the module-level singleton."""
    return state_mod.GeneratorState()


@pytest.fixture
def reset_handshake():
    """Clear module-level HANDSHAKE dict between tests."""
    state_mod.HANDSHAKE["cloud_greeting"] = None
    state_mod.HANDSHAKE["rdc_response"] = None
    state_mod.HANDSHAKE["config_msg"] = None
    yield
    state_mod.HANDSHAKE["cloud_greeting"] = None
    state_mod.HANDSHAKE["rdc_response"] = None
    state_mod.HANDSHAKE["config_msg"] = None
