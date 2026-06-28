import os
from datetime import datetime

def log_execution_note(ticket, symbol, signal, lots, reasoning, status="OPEN"):
    """Logs a human-readable note about why a trade was taken."""
    path = "data/execution_notes.txt"
    os.makedirs("data", exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    side = "BUY" if signal == 1 else "SELL"
    
    note = (
        f"[{timestamp}] Ticket: {ticket} | {symbol} {side} | Lots: {lots}\n"
        f"REASONING: {reasoning}\n"
        f"STATUS: {status}\n"
        f"{'-'*50}\n"
    )
    
    with open(path, "a") as f:
        f.write(note)