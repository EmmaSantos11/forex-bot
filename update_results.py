import pandas as pd
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv
from mt5_connector import connect_mt5, shutdown_mt5

# Load credentials
load_dotenv()
LOGIN = int(os.getenv("MT5_LOGIN", "0"))
PASSWORD = os.getenv("MT5_PASSWORD", "")
SERVER = os.getenv("MT5_SERVER", "")

def update_trade_results():
    """
    Reads the trade history CSV, checks MT5 for closed positions,
    and updates the profit/exit price columns.
    """
    csv_path = "data/trade_history.csv"
    if not os.path.exists(csv_path):
        print(f"❌ {csv_path} not found.")
        return

    # Initialize connection using existing utility
    if not connect_mt5(LOGIN, PASSWORD, SERVER):
        return

    try:
        df = pd.read_csv(csv_path)
        
        # Ensure result columns exist in the DataFrame
        for col in ['exit_price', 'profit', 'status']:
            if col not in df.columns:
                df[col] = None

        updated_count = 0
        
        for index, row in df.iterrows():
            # Only process trades not already marked as CLOSED
            if row['status'] != 'CLOSED' and pd.notna(row['ticket']):
                ticket = int(row['ticket'])
                
                # Query history deals for this position ID
                # In MT5, the entry order ticket typically matches the position ID
                deals = mt5.history_deals_get(position=ticket)
                
                if deals is not None and len(deals) > 1:
                    # Find the OUT deal (the deal that closed the position)
                    # Entry types: 0=IN, 1=OUT, 2=IN/OUT
                    exit_deal = next((d for d in deals if d.entry in [1, 2]), None)
                    
                    if exit_deal:
                        df.at[index, 'exit_price'] = exit_deal.price
                        df.at[index, 'profit'] = round(exit_deal.profit + exit_deal.commission + exit_deal.swap, 2)
                        df.at[index, 'status'] = 'CLOSED'
                        updated_count += 1
                        print(f"✅ Position {ticket} updated. Profit: {df.at[index, 'profit']}")

        if updated_count > 0:
            df.to_csv(csv_path, index=False)
            print(f"📝 Successfully updated {updated_count} trades in CSV.")
        else:
            print("🔍 No new closed trades found in MT5 history.")

    finally:
        shutdown_mt5()

if __name__ == "__main__":
    update_trade_results()