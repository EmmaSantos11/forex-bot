import csv
import os
from datetime import datetime

def log_trade(data):
    """Logs trade details to a CSV file for performance analysis."""
    path = "data/trade_history.csv"
    os.makedirs("data", exist_ok=True)
    file_exists = os.path.isfile(path)
    
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(data)