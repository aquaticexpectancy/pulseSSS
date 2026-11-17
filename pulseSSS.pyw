# Double-click to start pulseSSS in the system tray (no console window).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tray import main  # noqa: E402

main()
