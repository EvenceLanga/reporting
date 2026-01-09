from django.core.cache import cache
from datetime import datetime, timedelta
import re

from accounts.utils.supabase_utils import (
    fetch_eod_data,
    fetch_pos2_stock_data,
    fetch_pos3_stock_data,
    fetch_posaud_sales,
)
from accounts.utils.fuel_utils import TAGID_ATTENDANT_MAP


def refresh_dashboard_cache():
    """Refreshes dashboard cache with accurate totals and trends."""
    today = datetime.today().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    next_month = month_start.replace(day=28) + timedelta(days=4)
    month_end = next_month - timedelta(days=next_month.day)

    # Fetch all data
    eod_data = fetch_eod_data(month_start.isoformat(), today.isoformat())
    pos2_data = fetch_pos2_stock_data(month_start.isoformat(), today.isoformat())
    pos3_data = fetch_pos3_stock_data(month_start.isoformat(), today.isoformat())
    posaud_data = fetch_posaud_sales(month_start.isoformat())

    # Initialize totals
    totals = {
        "daily": 0,
        "weekly": 0,
        "monthly": 0,
        "fuel": 0,
        "diesel": 0,
        "unleaded": 0,
        "nonfuel": 0,
        "revenue": 0,
        "returns": 0,
        "transactions": 0,
    }

    staff_perf = {}
    daily_trends = {}

    # --- Process Fuel Sales (POS1 / EOD) ---
    for row in eod_data:
        try:
            s_date = datetime.strptime(row["s_date"], "%Y-%m-%d").date()
            amount = float(row.get("total", 0))
            gradeid = (row.get("gradeid") or "").strip()

            totals["fuel"] += amount
            totals["revenue"] += amount
            totals["transactions"] += 1

            if gradeid == "01":
                totals["unleaded"] += amount
            elif gradeid == "02":
                totals["diesel"] += amount

            if s_date == today:
                totals["daily"] += amount
            if week_start <= s_date <= week_end:
                totals["weekly"] += amount
            if month_start <= s_date <= month_end:
                totals["monthly"] += amount

            tagid = (row.get("tagid") or "").upper()
            attendant = TAGID_ATTENDANT_MAP.get(tagid, "Unknown")
            staff_perf.setdefault(attendant, {"sales": 0, "transactions": 0})
            staff_perf[attendant]["sales"] += amount
            staff_perf[attendant]["transactions"] += 1

            daily_trends[row["s_date"]] = daily_trends.get(row["s_date"], 0) + amount
        except Exception:
            continue

    # --- Process POS2 and POS3 (Non-Fuel) ---
    all_pos = pos2_data + pos3_data
    for row in all_pos:
        try:
            date_str = row.get("trandate")
            if not date_str:
                continue
            s_date = datetime.strptime(date_str, "%Y-%m-%d").date()

            details = (row.get("details") or "").strip()
            if "VOID" in details.upper():
                continue

            nums = re.findall(r"\d+\.?\d*", details)
            amount = float(nums[-1]) if nums else 0

            totals["nonfuel"] += amount
            totals["revenue"] += amount
            totals["transactions"] += 1

            if s_date == today:
                totals["daily"] += amount
            if week_start <= s_date <= week_end:
                totals["weekly"] += amount
            if month_start <= s_date <= month_end:
                totals["monthly"] += amount

            daily_trends[date_str] = daily_trends.get(date_str, 0) + amount
        except Exception:
            continue

    # --- Process Returns ---
    for row in posaud_data:
        try:
            details = (row.get("details") or "").lower()
            if "return" in details or "refund" in details:
                m = re.search(r"\d+\.?\d*", details)
                if m:
                    totals["returns"] += float(m.group())
        except Exception:
            continue

    # --- Calculate POS totals ---
    pos1_total = sum(float(row.get("total", 0)) for row in eod_data)
    pos2_total = sum(
        float(re.findall(r"\d+\.?\d*", (row.get("details") or ""))[-1])
        if re.findall(r"\d+\.?\d*", (row.get("details") or "")) else 0
        for row in pos2_data
        if "VOID" not in (row.get("details") or "").upper()
    )
    pos3_total = sum(
        float(re.findall(r"\d+\.?\d*", (row.get("details") or ""))[-1])
        if re.findall(r"\d+\.?\d*", (row.get("details") or "")) else 0
        for row in pos3_data
        if "VOID" not in (row.get("details") or "").upper()
    )

    sorted_dates = sorted(daily_trends.keys())
    trend_values = [daily_trends[d] for d in sorted_dates]

    dashboard = {
        "today_sale": round(totals["daily"], 2),
        "week_sale": round(totals["weekly"], 2),
        "month_sale": round(totals["monthly"], 2),
        "fuel_total": round(totals["fuel"], 2),
        "diesel_total": round(totals["diesel"], 2),
        "unleaded_total": round(totals["unleaded"], 2),
        "nonfuel_total": round(totals["nonfuel"], 2),
        "total_revenue": round(totals["revenue"], 2),
        "nett_revenue": round(totals["revenue"] - totals["returns"], 2),
        "total_returns": round(totals["returns"], 2),
        "total_transactions": totals["transactions"],
        "staff_performance": staff_perf,
        "revenue_trend_labels": sorted_dates,
        "revenue_trend_data": trend_values,
        "pos1_total_paid": round(pos1_total, 2),
        "pos2_total_paid": round(pos2_total, 2),
        "pos3_total_paid": round(pos3_total, 2),
        "week_start": week_start,
        "week_end": week_end,
        "month_start": month_start,
        "month_end": month_end,
        "today": today,
    }

    cache.set("dashboard_cache", dashboard, 300)
    print("✔ Dashboard cache refreshed.")
    return True
