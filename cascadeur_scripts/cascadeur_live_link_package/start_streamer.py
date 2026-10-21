import os
import sys

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
    return "Blender Live Link.Start Streamer"


def description():
    return "Start streaming character animation to Blender Live Link"


def run(scene):
    msg = cascadeur_streamer.start()
    if scene and hasattr(scene, "info"):
        scene.info("Cascadeur Live Link Streamer started")
    else:
        print("[Live Link] Streamer started:\n" + str(msg))
