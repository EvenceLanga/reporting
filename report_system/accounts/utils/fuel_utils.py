import re

FUEL_NAMES = ["DIESEL", "UNLEADED", "ULP", "LPG"]

FUEL_BARCODE_SET = {
    "F001",  # Example barcodes
    "F002",
    "F003"
}

fuel_pattern = re.compile(
    r"(?P<desc>DIESEL|UNLEADED|ULP)\s+(?P<litres>\d+\.\d{2})\s+(?P<price>\d+\.\d{2})\s+(?P<total>\d+\.\d{2})",
    re.IGNORECASE
)
FUEL_NAMES = {"UNLEADED 95", "DIESEL 50PPM"}

TAGID_ATTENDANT_MAP = {
    "94CBA1": "Molapo",
    "56BDEF": "Jose",
    "C7DFA1": "Sibu",
    "6DC29F": "Mathebe"
}

def get_fuel_type_from_price(price):
    if abs(price - 22.08) < 0.05:
        return "UNLEADED 95"
    elif abs(price - 22.8) < 0.05:
        return "DIESEL 50PPM"
    return "UNKNOWN"

