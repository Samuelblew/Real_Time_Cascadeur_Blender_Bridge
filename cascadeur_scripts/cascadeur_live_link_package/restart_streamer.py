import os
import sys
import importlib

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

_PARENT_DIR = os.path.abspath(os.path.join(_CURRENT_DIR, ".."))
if os.path.exists(os.path.join(_PARENT_DIR, "cascadeur_streamer.py")) and _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

try:
    import cascadeur_streamer
except ImportError:
    from . import cascadeur_streamer


def name():
    return "Blender Live Link.Restart Streamer"


def description():
    return "Restart and reload Cascadeur Live Link Streamer"


def run(scene):
    cascadeur_streamer.stop_all()
    importlib.reload(cascadeur_streamer)
    msg = cascadeur_streamer.start()
    if scene and hasattr(scene, "info"):
        scene.info("Cascadeur Live Link Streamer restarted")
    else:
        print("[Live Link] Streamer restarted:\n" + str(msg))
