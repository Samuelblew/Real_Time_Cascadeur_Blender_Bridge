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
    return "Blender Live Link.Check Status"


def description():
    return "Check Cascadeur Live Link Streamer status"


def run(scene):
    st = cascadeur_streamer.status()
    # Format single-line summary for Cascadeur status bar
    summary = st.replace("\n", "  |  ")
    if scene and hasattr(scene, "info"):
        scene.info(summary)
    print("\n[Live Link Status]\n" + st + "\n")
