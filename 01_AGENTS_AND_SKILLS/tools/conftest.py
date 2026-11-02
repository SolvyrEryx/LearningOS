"""Put the tools directory on ``sys.path`` so the suite imports the real modules.

``learningos.py`` is run as a script, which means its own directory is ``sys.path[0]`` at runtime.
Under pytest the rootdir is this directory but it is not automatically importable, so the flat
modules (``common``, ``schema``, ``validation``) and the packages (``memory``, ``ingestion``,
``graphs``) are made importable here — exactly as they are when the CLI runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
