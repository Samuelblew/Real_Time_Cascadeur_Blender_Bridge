"""
Cascadeur Live Link Package
===========================
Registered into Cascadeur's native Commands menu.
"""

import os
import sys

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

_PARENT_DIR = os.path.abspath(os.path.join(_CURRENT_DIR, ".."))
if os.path.exists(os.path.join(_PARENT_DIR, "cascadeur_streamer.py")) and _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)
