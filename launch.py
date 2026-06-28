"""
launch.py — Apex Forex AI Bot master orchestrator.

Starts and monitors all system components:
  • MetaTrader 5 terminal (auto-launches if not running)
  • Trading bot          (run_bot.py → crash-recovery wrapper → main.py)
  • Streamlit dashboard  (accessible at http://localhost:8501)
  • Sunday auto-retrain  (detected and run before market open)

Called by start_bot.bat — do not run directly unless inside the venv.
"""

import os
import sys
import time
import signal
import subprocess
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

ROOT        = Path(__file__).parent.resolve()
# Venv lives OUTSIDE OneDrive so Windows Application Control does not block
# compiled extension DLLs (.pyd files) that OneDrive marks as untrusted.
VENV_ROOT   = Path(r"C:\forex_venv")
VENV_PY     = VENV_ROOT / "Scripts" / "python.exe"
VENV_STR    = VENV_ROOT / "Scripts" / "streamlit.exe"
BOT_SCRIPT  = ROOT / "run_bot.py"
DASH_SCRIPT = ROOT / "streamlit_app.py"
RETRAIN_SCR = ROOT / "retrain_model.py"
IMPORT_SCR  = ROOT / "import_manual_trades.py"
LOG_DIR     = ROOT / "logs"
DASH_PORT   = 8501

MT5_SEARCH_PATHS = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal",
]

LOG_DIR.mkdir(exist_ok=True)

# ── Colour output (Windows ANSI) ──────────────────────────────────────────────
os.system("")   # enable ANSI on Windows 10+

CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
DIM     = "\033[2m"
BOLD    = "\033[1m"
RESET   = "\033[0m"

def _p(msg):   print(msg, flush=True)
def _ok(msg):  _p(f"  {GREEN}✅  {msg}{RESET}")
def _warn(msg):_p(f"  {YELLOW}⚠️   {msg}{RESET}")
def _err(msg): _p(f"  {RED}❌  {msg}{RESET}")
def _info(msg):_p(f"  {CYAN}ℹ️   {msg}{RESET}")
def _dim(msg): _p(f"  {DIM}{msg}{RESET}")


# ── Banner ────────────────────────────────────────────────────────────────────

def _banner():
    _p(f"""
{CYAN}{BOLD}
  ╔══════════════════════════════════════════════════════╗
  ║            🐻  M R  B E R A ' S _ B O T             ║
  ║                  Master Launcher v2.0                ║
  ╚══════════════════════════════════════════════════════╝
{RESET}""")


# ── MT5 detection ─────────────────────────────────────────────────────────────

def _mt5_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
            capture_output=True, text=True, timeout=5,
        )
        return "terminal64.exe" in result.stdout
    except Exception:
        return False


def _find_mt5_exe() -> Path | None:
    for p in MT5_SEARCH_PATHS:
        path = Path(p)
        if path.is_file():
            return path
        # Search inside MetaQuotes AppData folder
        if path.is_dir():
            for child in path.iterdir():
                exe = child / "terminal64.exe"
                if exe.exists():
                    return exe
    return None


def _ensure_mt5():
    _p(f"\n{BOLD}  [1/4] MetaTrader 5{RESET}")
    if _mt5_running():
        _ok("MT5 is already running.")
        return True

    _warn("MT5 is not running — searching for terminal...")
    exe = _find_mt5_exe()
    if exe:
        _info(f"Found MT5 at: {exe}")
        subprocess.Popen([str(exe)], close_fds=True)
        _info("Waiting 20 seconds for MT5 to initialise...")
        for i in range(20, 0, -1):
            time.sleep(1)
            if _mt5_running():
                _ok(f"MT5 started successfully.")
                time.sleep(5)   # extra settle time
                return True
        _warn("MT5 may still be loading — continuing anyway.")
        return True
    else:
        _err("MT5 not found automatically.")
        _p(f"  {YELLOW}Please open MetaTrader 5 manually, then press Enter to continue...{RESET}")
        input()
        if _mt5_running():
            _ok("MT5 detected — continuing.")
            return True
        _err("MT5 still not detected. The bot may fail to connect.")
        return False


# ── Sunday retrain check ──────────────────────────────────────────────────────

def _should_retrain() -> bool:
    """True if today is Sunday and models are older than 6 days."""
    now = datetime.now(timezone.utc)
    if now.weekday() != 6:   # 6 = Sunday
        return False
    model_path = ROOT / "models" / "xgb_EURUSD.pkl"
    if not model_path.exists():
        return True
    age_days = (now.timestamp() - model_path.stat().st_mtime) / 86400
    return age_days > 6


def _run_retrain():
    _p(f"\n{BOLD}  [RETRAIN] Sunday detected — running full AI retrain before market open{RESET}")

    _info("Step 1 — Importing live trade history...")
    r1 = subprocess.run([str(VENV_PY), str(IMPORT_SCR)], cwd=ROOT)
    if r1.returncode == 0:
        _ok("Live trades imported.")
    else:
        _warn("Import had issues — continuing with retrain anyway.")

    _info("Step 2 — Retraining all pair models (this takes 2–5 minutes)...")
    r2 = subprocess.run([str(VENV_PY), str(RETRAIN_SCR)], cwd=ROOT)
    if r2.returncode == 0:
        _ok("Retrain complete. Models updated.")
    else:
        _warn("Retrain finished with warnings — check console output above.")


# ── Process launchers ─────────────────────────────────────────────────────────

_processes: list[subprocess.Popen] = []
_shutdown_event = threading.Event()


def _start_bot() -> subprocess.Popen:
    _p(f"\n{BOLD}  [2/4] Trading Bot{RESET}")
    proc = subprocess.Popen(
        [str(VENV_PY), "-u", str(BOT_SCRIPT)],
        cwd=ROOT,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    _ok(f"Bot started   (PID {proc.pid}) — window title: 'run_bot'")
    return proc


def _start_dashboard() -> subprocess.Popen:
    _p(f"\n{BOLD}  [3/4] Streamlit Dashboard{RESET}")
    proc = subprocess.Popen(
        [
            str(VENV_STR), "run", str(DASH_SCRIPT),
            "--server.port", str(DASH_PORT),
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=ROOT,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        env={**os.environ},
    )
    _ok(f"Dashboard started (PID {proc.pid})")
    _info(f"Open in browser → http://localhost:{DASH_PORT}")
    return proc


def _open_browser():
    """Opens the dashboard in default browser after a short delay."""
    time.sleep(4)
    try:
        import webbrowser
        webbrowser.open(f"http://localhost:{DASH_PORT}")
    except Exception:
        pass


def _start_ngrok() -> str | None:
    _p(f"\n{BOLD}  [3.5/4] ngrok — Remote Phone Access{RESET}")
    token = os.environ.get("NGROK_AUTH_TOKEN", "").strip()
    if not token:
        _warn("NGROK_AUTH_TOKEN not set in .env — skipping remote access.")
        _info("Get a free token at https://dashboard.ngrok.com/get-started/your-authtoken")
        _info("Paste it into .env as NGROK_AUTH_TOKEN=your_token  then restart.")
        return None
    try:
        from pyngrok import ngrok, conf
        _ngrok_path = os.environ.get("NGROK_PATH", "").strip()
        if _ngrok_path:
            conf.get_default().ngrok_path = _ngrok_path
        conf.get_default().auth_token = token
        _domain = os.environ.get("NGROK_DOMAIN", "").strip()
        tunnel = ngrok.connect(DASH_PORT, "http", domain=_domain if _domain else None)
        url = tunnel.public_url
        _p(f"\n  {'='*54}")
        _p(f"  {GREEN}{BOLD}📱  PHONE URL  →  {url}{RESET}")
        _p(f"  {'='*54}\n")
        return url
    except Exception as e:
        _warn(f"ngrok failed to start: {e}")
        _info("Make sure pyngrok is installed:  pip install pyngrok")
        return None


# ── Status monitor loop ───────────────────────────────────────────────────────

def _status_loop(bot_proc: subprocess.Popen, dash_proc: subprocess.Popen, phone_url: str | None = None):
    _p(f"\n{BOLD}  [4/4] Status Monitor{RESET}")
    _p(f"  {DIM}{'─' * 54}{RESET}")
    _p(f"  {CYAN}All systems running. Press Ctrl+C to stop everything.{RESET}")
    if phone_url:
        _p(f"  {GREEN}📱 Phone URL → {phone_url}{RESET}")
    _p(f"  {DIM}{'─' * 54}{RESET}\n")

    cycle = 0
    while not _shutdown_event.is_set():
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")

        bot_alive  = bot_proc.poll()  is None
        dash_alive = dash_proc.poll() is None
        mt5_alive  = _mt5_running()

        bot_str  = f"{GREEN}RUNNING{RESET}" if bot_alive  else f"{RED}STOPPED{RESET}"
        dash_str = f"{GREEN}RUNNING{RESET}" if dash_alive else f"{RED}STOPPED{RESET}"
        mt5_str  = f"{GREEN}RUNNING{RESET}" if mt5_alive  else f"{YELLOW}NOT DETECTED{RESET}"

        if cycle % 4 == 1:   # print status every ~2 minutes (4 × 30s)
            phone_part = f"  │  📱 {phone_url}" if phone_url else ""
            _p(
                f"  {DIM}[{now}]{RESET}  "
                f"Bot: {bot_str}  │  "
                f"Dashboard: {dash_str}  │  "
                f"MT5: {mt5_str}  │  "
                f"http://localhost:{DASH_PORT}"
                f"{phone_part}"
            )

        if not bot_alive and not _shutdown_event.is_set():
            _warn(f"[{now}] Bot process exited — run_bot.py will handle restart automatically.")

        _shutdown_event.wait(timeout=30)


# ── Shutdown ──────────────────────────────────────────────────────────────────

def _shutdown(procs: list[subprocess.Popen]):
    _shutdown_event.set()
    _p(f"\n\n  {YELLOW}⏹  Shutting down all components...{RESET}")
    for proc in procs:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _ok("All processes stopped.")
    _p(f"  {DIM}Restoring Windows sleep settings...{RESET}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    _banner()

    # Sunday retrain check
    if _should_retrain():
        _p(f"  {YELLOW}📅 Sunday detected — AI retrain will run before bot starts.{RESET}")
        ans = input(f"  {BOLD}Run retrain now? (Y/n): {RESET}").strip().lower()
        if ans != "n":
            _run_retrain()
        else:
            _info("Retrain skipped — bot will use existing models.")
    else:
        # Offer manual retrain option
        _p(f"  {DIM}Tip: Run 'python retrain_model.py' anytime to update AI models.{RESET}")

    _ensure_mt5()

    bot_proc  = _start_bot()
    _processes.append(bot_proc)

    dash_proc = _start_dashboard()
    _processes.append(dash_proc)

    # Give Streamlit a moment to bind its port before ngrok tunnels into it
    time.sleep(3)
    phone_url = _start_ngrok()

    # Open browser in background
    threading.Thread(target=_open_browser, daemon=True).start()

    def _sig_handler(sig, frame):
        _shutdown(_processes)
        sys.exit(0)

    signal.signal(signal.SIGINT,  _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    try:
        _status_loop(bot_proc, dash_proc, phone_url)
    except KeyboardInterrupt:
        _shutdown(_processes)


if __name__ == "__main__":
    main()
