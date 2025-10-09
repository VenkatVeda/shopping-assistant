# utils/data_loader.py
import pandas as pd
from typing import Dict
from config.settings import DATA_CONFIG

class DataLoader:
    def __init__(self):
        self.url_to_image = {}
        self.load_excel_data()
    
    def load_excel_data(self):
        try:
            df = pd.read_excel(DATA_CONFIG["excel_file"])
            self.url_to_image = dict(zip(df["Product Page URL"], df["Image URL"]))
            print("Excel file loaded successfully")
        except Exception as e:
            print(f"Could not load Excel file: {e}")