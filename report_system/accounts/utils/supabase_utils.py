# accounts/utils/supabase_utils.py

import os
from dotenv import load_dotenv
from supabase import create_client, Client

from pathlib import Path

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_posaud_sales(from_date=None, filters=None, select="*"):
    all_data = []
    page = 0
    page_size = 1000

    query = supabase.table("posaud").select(select)

    # -------------------------------------------------------
    # Optional date filters
    # -------------------------------------------------------
    if filters:
        start_date = filters.get("start_date")
        end_date = filters.get("end_date")

        if start_date and end_date:
            # Filter POSAUS in the date range (inclusive)
            query = query.gte("trandate", start_date).lte("trandate", end_date)
        elif start_date:
            query = query.gte("trandate", start_date)
        elif end_date:
            query = query.lte("trandate", end_date)
        elif from_date:
            query = query.gte("trandate", from_date)

    elif from_date:
        query = query.gte("trandate", from_date)

    # -------------------------------------------------------
    # Optional other filters
    # -------------------------------------------------------
    if filters:
        search_term = filters.get("search_term", "").strip().lower()
        if search_term:
            query = query.or_(
                f"userid.ilike.%{search_term}%,logfile.ilike.%{search_term}%"
            )

        if filters.get("item"):
            item_filter = filters["item"]
            query = query.or_(
                f"details.ilike.%{item_filter}%,code.ilike.%{item_filter}%"
            )

        if filters.get("trantime"):
            query = query.ilike("trantime", f"{filters['trantime']}%")

    # -------------------------------------------------------
    # Sorting
    # -------------------------------------------------------
    query = query.order("trandate", desc=True).order("trantime", desc=True)

    # -------------------------------------------------------
    # Pagination loop
    # -------------------------------------------------------
    while True:
        response = query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = response.data or []
        all_data.extend(rows)

        if len(rows) < page_size:
            break
        page += 1

    return all_data


def fetch_eod_data(from_date=None, to_date=None):
    all_data = []
    page = 0
    page_size = 1000

    # IMPORTANT: include gradeid in the SELECT
    query = supabase.table("eod_data").select(
        "s_time, s_date, volume, price, total, tagid, gradeid"
    )

    if from_date:
        query = query.gte("s_date", from_date)
    if to_date:
        query = query.lte("s_date", to_date)

    # Sort by date and time
    query = query.order("s_date", desc=False).order("s_time", desc=False)

    while True:
        response = query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = response.data or []
        all_data.extend(rows)

        if len(rows) < page_size:
            break
        page += 1

    return all_data


def fetch_pos2_stock_data(from_date=None, to_date=None, filters=None):
    all_data = []
    page = 0
    page_size = 1000

    query = supabase.table("pos2_stock_data").select("*")

    if from_date:
        query = query.gte("trandate", from_date)
    if to_date:
        query = query.lte("trandate", to_date)

    if filters:
        if filters.get("user"):
            query = query.ilike("userid", f"%{filters['user']}%")

    query = query.order("trandate", desc=True).order("trantime", desc=True)

    while True:
        response = query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = response.data or []
        all_data.extend(rows)
        if len(rows) < page_size:
            break
        page += 1

    return all_data


def fetch_pos3_stock_data(from_date=None, to_date=None, filters=None):
    all_data = []
    page = 0
    page_size = 1000

    query = supabase.table("pos3_stock_data").select("*")

    if from_date:
        query = query.gte("trandate", from_date)
    if to_date:
        query = query.lte("trandate", to_date)

    if filters:
        if filters.get("user"):
            query = query.ilike("userid", f"%{filters['user']}%")

    query = query.order("trandate", desc=True).order("trantime", desc=True)

    while True:
        response = query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = response.data or []
        all_data.extend(rows)
        if len(rows) < page_size:
            break
        page += 1

    return all_data

def fetch_daily_sales_reports(from_date=None, to_date=None):
    # Step 1: Fetch reports
    query = supabase.table("daily_sales_reports").select("*")

    if from_date:
        query = query.gte("report_date", from_date)
    if to_date:
        query = query.lte("report_date", to_date)

    query = query.order("report_date", desc=False)
    reports_response = query.execute()
    reports = reports_response.data or []

    # Step 2: Fetch ALL rates up to to_date (or today)
    rate_query = supabase.table("fuel_rates").select("*")
    if to_date:
        rate_query = rate_query.lte("start_date", to_date)
    rates_response = rate_query.execute()
    rates = rates_response.data or []

    # Step 3: Organize rates into lookup by rate name
    from collections import defaultdict
    from datetime import datetime

    rate_lookup = defaultdict(list)
    for rate in rates:
        rate_lookup[rate["rate_name"]].append(rate)

    # Helper to get latest applicable rate for a given date
    def get_rate_for_date(rate_name, report_date_str):
        candidates = [
            r for r in rate_lookup.get(rate_name, [])
            if r["start_date"] <= report_date_str
        ]
        if not candidates:
            return 0.0
        return sorted(candidates, key=lambda r: r["start_date"], reverse=True)[0]["value"]

    # Step 4: Add rates to each report
    for rec in reports:
        report_date = rec["report_date"]  # should be a string like '2025-10-01'
        rec["rate_r22_12"] = get_rate_for_date("rate_r22_12", report_date)
        rec["rate_r23_36"] = get_rate_for_date("rate_r23_36", report_date)

    return reports


