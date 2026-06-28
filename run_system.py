"""
run_system.py — DEPRECATED legacy launcher.

This file previously launched run_live.py (an old single-pair bot with no
confidence gate, no ICT gate, and no pending confirmation).  It should NOT
be used — it would trade on raw signals without any safety checks.

Use start_bot.bat instead.  That calls launch.py → run_bot.py → main.py,
which has all guards: ICT gate, BOS gate, confidence gate, dead-zone,
session filter, news filter, daily cap, and pending confirmation.
"""

import sys
print("=" * 60)
print("  ⚠️  run_system.py is DEPRECATED and disabled.")
print()
print("  Use start_bot.bat to launch the bot correctly.")
print("  (start_bot.bat → launch.py → run_bot.py → main.py)")
print("=" * 60)
sys.exit(1)
