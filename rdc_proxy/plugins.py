"""Plugin loader: discover entry-point group "rdc_proxy.plugins" at startup.

Each plugin must expose a class with a no-arg constructor and `start(state)`
method that spawns its own thread and pushes data via
`state.update_side_channel(name, dict)`.

A plugin's package is OPTIONAL. If no plugin is installed, this is a no-op.
"""

import importlib.metadata
import traceback

ENTRY_POINT_GROUP = "rdc_proxy.plugins"

_loaded = []


def load_plugins(state):
    """Discover and start all installed plugins. Returns list of plugin names."""
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:
        # Python < 3.10 fallback
        eps = importlib.metadata.entry_points().get(ENTRY_POINT_GROUP, [])

    started = []
    for ep in eps:
        try:
            cls = ep.load()
            instance = cls()
            instance.start(state)
            _loaded.append((ep.name, instance))
            started.append(ep.name)
            print(f"[plugins] loaded: {ep.name}", flush=True)
        except Exception as e:
            print(f"[plugins] {ep.name} failed: {e}", flush=True)
            traceback.print_exc()
    if not started:
        print("[plugins] no plugins installed", flush=True)
    return started


def loaded_plugins():
    return list(_loaded)
