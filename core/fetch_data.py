import pandas as pd
import os

def load_csv(filepath):

    # Check file exists
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    # Check if empty
    if os.path.getsize(filepath) == 0:
        raise ValueError(f"File is empty: {filepath}")

    df = pd.read_csv(filepath)

    # Check if dataframe empty
    if df.empty:
        raise ValueError(f"No data inside file: {filepath}")

    # Standardize columns
    df.columns = df.columns.str.capitalize()

    # Ensure Timestamp exists
    if "Timestamp" not in df.columns:
        raise ValueError("CSV must contain a 'Timestamp' column")

    # 🔥 REMOVE BAD ROWS (THIS FIXES YOUR ERROR)
    df = df[df["Timestamp"].astype(str).str.contains(r"\d{4}", na=False)]

    # Convert safely
    df["Timestamp"] = pd.to_datetime(
        df["Timestamp"],
        errors="coerce"
    )

    # Drop invalid timestamps
    df = df.dropna(subset=["Timestamp"])

    if df.empty:
        raise ValueError("All rows removed — bad timestamp data")

    # Set index
    df.set_index("Timestamp", inplace=True)

    return df