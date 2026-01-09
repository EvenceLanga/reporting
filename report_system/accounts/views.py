# Standard library imports
import io
import json
import os
import random
import re
import string
from collections import defaultdict, Counter
from datetime import datetime, date, timedelta
from urllib.parse import urlencode

# Third-party imports
import pandas as pd
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    authenticate, get_user_model, login, logout, 
    update_session_auth_hash
)
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from dotenv import load_dotenv
from supabase import create_client

# Local imports
from accounts.decorators import role_required
from accounts.utils.supabase_utils import (
    fetch_eod_data, fetch_posaud_sales, fetch_pos2_stock_data, 
    fetch_pos3_stock_data, supabase
)
from .forms import AdminUserCreationForm, CustomUserForm
from .models import CustomUser

# Constants
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_TABLE = "daily_sales_reports"
TABLE_NAME = 'stdb'

# Patterns
ITEM_LINE_PATTERN = re.compile(r"^\s*(\d+)\s*×\s*(.+?)\s*—\s*R([\d,.]+)", re.IGNORECASE)
STANDARD_RATE_PATTERN = re.compile(r"STANDARD\s+RATE/S\s+(\d+(\.\d+)?)%\s+([\d.]+)\s+([\d.]+)", re.IGNORECASE)
QTY_PRICE_PATTERN = re.compile(r"(\d+(\.\d+)?)\s*@\s*([\d.]+)\s*([\d.]*)")
DESCRIPTION_PRICE_PATTERN = re.compile(r"(.+?)\s+(\d+\.\d{2})$")

# Fuel constants
FUEL_NAMES = {"UNLEADED 95", "DIESEL 50PPM"}
FUEL_PRICE_RANGES = {
    "UNLEADED 95": (21.50, 22.50),
    "DIESEL 50PPM": (22.50, 23.50),
}
TAGID_ATTENDANT_MAP = {
    "94CBA1": "Molapo",
    "56BDEF": "Jose",
    "C7DFA1": "Sibu",
    "6DC29F": "Mathebe"
}

# Invoice Management Constants
INVOICE_TABLE = "forecourt_invoices"
INVOICE_SUPABASE = create_client(SUPABASE_URL, SUPABASE_KEY)


# Utility Functions
def generate_password(length=8):
    """Generate a random password."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def get_fuel_type_from_price(price):
    """Determine fuel type based on price range."""
    for fuel, (low, high) in FUEL_PRICE_RANGES.items():
        if low <= price <= high:
            return fuel
    return "UNKNOWN"


def is_admin_or_manager(user):
    """Check if user is admin or manager."""
    return user.is_authenticated and user.role in ['admin', 'manager']


def normalize_item_rows(group_rows):
    """Normalize item rows from transaction data."""
    normalized = []
    index = 0
    
    while index < len(group_rows):
        row = group_rows[index]
        details = (row.get("details") or "").strip()
        code = row.get("code") or ""
        
        if not details:
            index += 1
            continue

        # 1. Check qty/price lines
        match = QTY_PRICE_PATTERN.match(details)
        if match:
            qty = float(match.group(1))
            unit_price = float(match.group(3))
            total_price = float(match.group(4)) if match.group(4) else qty * unit_price
            
            # Try description in previous row
            name, barcode = details, code
            if index > 0:
                prev = group_rows[index - 1]
                pdet, pcode = (prev.get("details") or "").strip(), prev.get("code") or ""
                if pdet and not QTY_PRICE_PATTERN.match(pdet):
                    name, barcode = pdet, pcode or code
                    
            normalized.append((name, qty, unit_price, total_price, barcode))
            index += 1
            continue

        # 2. Look ahead if next line is qty/price
        if index + 1 < len(group_rows):
            next_row = group_rows[index + 1]
            next_match = QTY_PRICE_PATTERN.match((next_row.get("details") or "").strip())
            if next_match:
                qty = float(next_match.group(1))
                unit_price = float(next_match.group(3))
                total_price = float(next_match.group(4)) if next_match.group(4) else qty * unit_price
                normalized.append((details, qty, unit_price, total_price, code))
                index += 2
                continue

        # 3. Check "ITEMS 1 TOTAL X.XX" within current group
        items1 = next((r for r in group_rows if (r.get("details") or "").upper().startswith("ITEMS 1 TOTAL")), None)
        if items1:
            try:
                total_val = float(items1.get("details").split()[-1])
                normalized.append((details, 1, total_val, total_val, code))
                index += 1
                continue
            except (ValueError, IndexError, AttributeError):
                pass

        # 4. Check if description ends with price
        price_match = DESCRIPTION_PRICE_PATTERN.match(details)
        if price_match:
            name, price = price_match.group(1), float(price_match.group(2))
            normalized.append((name, 1, price, price, code))
            index += 1
            continue

        # Fallback
        normalized.append((details, 1, 0.0, 0.0, code))
        index += 1

    return normalized


def calc_dispensed(opening, closing, max_meter=100000):
    """
    Safely calculate litres dispensed from meter readings.
    Handles normal cases and meter rollover (reset to 0).
    Never returns a negative value.
    """
    if opening is None or closing is None:
        return 0

    try:
        opening = float(opening)
        closing = float(closing)
    except (TypeError, ValueError):
        return 0

    if closing >= opening:
        return closing - opening

    # Handle rollover (closing < opening)
    if (opening - closing) > (0.5 * max_meter):  # Use threshold to detect rollover
        return (max_meter - opening) + closing

    # If not rollover and still closing < opening, treat as bad data
    return 0


def fetch_saved_sales(from_date, to_date):
    """Fetch saved records from Supabase between given dates."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    query = f"?select=*&report_date=gte.{from_date}&report_date=lte.{to_date}"
    response = requests.get(f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}{query}", headers=headers)
    return response.json() if response.status_code == 200 else []


# Authentication Views
def logout_view(request):
    """Handle user logout."""
    logout(request)
    return redirect('login')


from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import CustomUser

MAX_FAILED_ATTEMPTS = 4

def custom_login_view(request):
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")

        try:
            user_obj = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            messages.error(request, "Invalid email or password")
            return render(request, "auth/login.html")

        if user_obj.is_locked:
            messages.error(request, "Your account is locked. Please reset your password.")
            return render(request, "auth/login.html")

        user = authenticate(request, email=email, password=password)
        if user is not None:
            # Reset failed attempts
            user_obj.failed_attempts = 0
            user_obj.save()

            login(request, user)
            request.session.set_expiry(600)  # 10 minutes inactivity auto-logout

            role = user.role.lower()
            if role in ["admin", "manager"]:
                return redirect("admin_dashboard")
            elif role == "staff":
                return redirect("staff_dashboard")
            else:
                messages.error(request, "User role not recognized")
                logout(request)
                return redirect("login")
        else:
            # Increment failed attempts
            user_obj.failed_attempts += 1
            if user_obj.failed_attempts > MAX_FAILED_ATTEMPTS:
                user_obj.is_locked = True
                messages.error(
                    request,
                    "Your account has been locked due to multiple failed login attempts. Please reset your password."
                )
            else:
                messages.error(
                    request,
                    f"Invalid email or password. Attempt {user_obj.failed_attempts}/{MAX_FAILED_ATTEMPTS + 1}"
                )
            user_obj.save()
            return render(request, "auth/login.html")

    return render(request, "auth/login.html")


@login_required
def home_view(request):
    """Home view with password change check."""
    if request.session.pop('force_password_change', False):
        return redirect('force_password_change')
    return render(request, 'auth/login.html')


@login_required
def force_password_change(request):
    """Force password change for users."""
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)
            request.user.must_change_password = False
            request.user.save()
            messages.success(request, "Password changed successfully.")
            return redirect('dashboard')  # Or any post-login page
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'auth/force_password_change.html', {'form': form})


# User Management Views
@login_required
@role_required(['admin', 'manager'])
def create_user_view(request):
    """Create new user with auto-generated password."""
    User = get_user_model()
    
    if request.method == 'POST':
        form = AdminUserCreationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            if CustomUser.objects.filter(email=email).exists():
                messages.error(request, 'A user with this email already exists.')
            else:
                password = generate_password()
                user = form.save(commit=False)
                user.set_password(password)
                user.save()

                # Send email
                send_mail(
                    subject='Your New Account Credentials',
                    message=f"Hi {user.full_name},\n\nYour account has been created.\nEmail: {user.email}\nPassword: {password}\n\nPlease change your password upon login.",
                    from_email='info@maatlasolutions.co.za',
                    recipient_list=[user.email],
                    fail_silently=False,
                )

                messages.success(request, 'User created successfully. Login details sent to email.')
                return redirect('create_user')  # Refresh the page
    else:
        form = AdminUserCreationForm()
        
    return render(request, 'admin/create_user.html', {'form': form})


@login_required
@role_required(['admin', 'manager'])
def user_list(request):
    """Display list of all users."""
    users = CustomUser.objects.all()
    return render(request, 'admin/user_list.html', {'users': users})


@login_required
@role_required(['admin', 'manager'])
@user_passes_test(is_admin_or_manager)
def edit_user(request, user_id):
    """Edit user details."""
    user_obj = get_object_or_404(CustomUser, id=user_id)
    if request.method == 'POST':
        form = CustomUserForm(request.POST, instance=user_obj)
        if form.is_valid():
            form.save()
            return redirect('user_list')
    else:
        form = CustomUserForm(instance=user_obj)
        
    return render(request, 'admin/edit_user.html', {'form': form, 'user_obj': user_obj})


@login_required
@role_required(['admin', 'manager'])
@user_passes_test(is_admin_or_manager)
def delete_user(request, user_id):
    """Delete user with confirmation."""
    user_obj = get_object_or_404(CustomUser, id=user_id)
    if request.method == 'POST':
        user_obj.delete()
        return redirect('user_list')
    return render(request, 'admin/confirm_delete.html', {'user_obj': user_obj})


@login_required
@role_required(['admin', 'manager'])
def mamehlabe_store(request):
    """Display POS1 sales data with filtering."""

    today_str = date.today().strftime("%Y-%m-%d")
    
    # Get filters from GET parameters
    search_term = request.GET.get("search_term", "").strip().lower()
    item_filter = request.GET.get("item", "").strip().lower()
    start_date_filter = request.GET.get("start_date", "").strip() or today_str
    end_date_filter = request.GET.get("end_date", "").strip() or today_str
    trantime_filter = request.GET.get("trantime", "").strip()

    # Build filters dictionary
    filters = {
        "search_term": search_term,
        "item": item_filter,
        "start_date": start_date_filter,
        "end_date": end_date_filter,
        "trantime": trantime_filter,
    }

    # Fetch filtered data
    data = fetch_posaud_sales(filters=filters)

    # Sort by datetime descending
    def get_datetime(row):
        try:
            return datetime.strptime(f"{row['trandate']} {row['trantime']}", "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return datetime.min

    data.sort(key=get_datetime, reverse=True)

    # Paginate
    paginator = Paginator(data, 1500)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Prepare query string for pagination links
    query_params = {
        "search_term": search_term,
        "item": item_filter,
        "start_date": start_date_filter,
        "end_date": end_date_filter,
        "trantime": trantime_filter,
    }
    query_string = urlencode({k: v for k, v in query_params.items() if v})

    return render(request, 'admin/mamehlabe_store.html', {
        "sales": page_obj,
        "search_term": search_term,
        "item_filter": item_filter,
        "start_date_filter": start_date_filter,
        "end_date_filter": end_date_filter,
        "trantime_filter": trantime_filter,
        "query_string": query_string,
        "chart_data": json.dumps({"labels": [], "total_sales": [], "actual_sales": []}),
    })


def till_slip_analysis_pos1(request):
    """
    POS1 – Fuel-focused analysis (litres, fuel types, attendant).
    """

    template_name = "admin/till_slip_analysis_pos1.html"

    today = timezone.localdate()

    raw_start_date = request.GET.get("start_date")
    raw_end_date = request.GET.get("end_date")
    attendant_filter = request.GET.get("attendant", "").strip()
    fuel_type_filter = request.GET.get("fuel_type", "").strip()

    # Parse dates or default to today
    start_date = (
        datetime.strptime(raw_start_date, "%Y-%m-%d").date()
        if raw_start_date else today
    )
    end_date = (
        datetime.strptime(raw_end_date, "%Y-%m-%d").date()
        if raw_end_date else today
    )

    # Prevent future dates
    if start_date > today:
        start_date = today
    if end_date > today:
        end_date = today

    # Ensure start_date <= end_date
    if start_date > end_date:
        start_date = end_date

    # Build query
    query = (
        supabase.table("slip_items")
        .select("*, slips(trandate, trantime)")
        .eq("termnum", 1)
        .gte("trandate", start_date)
        .lte("trandate", end_date)
        .order("trandate", desc=True)
        .order("trantime", desc=True)
    )

    if attendant_filter:
        query = query.ilike("attendant", f"%{attendant_filter}%")
    if fuel_type_filter:
        query = query.ilike("item_name", f"%{fuel_type_filter}%")

    # Pagination
    all_data, page, page_size = [], 0, 1000
    while True:
        resp = query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = resp.data or []
        all_data.extend(rows)
        if len(rows) < page_size:
            break
        page += 1

    parsed_slips = []
    litres_by_fuel = defaultdict(float)

    total_litres = total_amount_paid = total_vat = 0.0

    for item in all_data:
        litres = float(item.get("qty") or 0)
        amount = float(item.get("total_price") or 0)
        vat = float(item.get("vat") or 0)
        fuel = item.get("item_name") or ""

        total_litres += litres
        total_amount_paid += amount
        total_vat += vat
        litres_by_fuel[fuel] += litres

        parsed_slips.append({
            "attendant": item.get("attendant") or "",
            "trandate": item.get("trandate"),
            "time": item.get("trantime"),
            "fuel_type": fuel,
            "litres": litres,
            "amount_paid": amount,
        })

    paginator = Paginator(parsed_slips, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, template_name, {
        "parsed_slips": page_obj,
        "page_obj": page_obj,
        "start_date": start_date,
        "end_date": end_date,
        "attendant_filter": attendant_filter,
        "fuel_type_filter": fuel_type_filter,
        "total_litres": total_litres,
        "total_amount_paid": total_amount_paid,
        "total_vat": total_vat,
        "litres_by_fuel": dict(litres_by_fuel),
        "today": today,  # for max date in input
    })

# POS 2 &3
from urllib.parse import urlencode


from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone
from datetime import datetime
from urllib.parse import urlencode

@login_required
def till_slip_analysis_dynamic(request, termnum):
    """
    POS2 & POS3 – Receipt-style slip analysis (items, VAT, totals) with financials.
    Only includes slip_items and slip_financials for the requested termnum.
    """
    template_name = f"admin/till_slip_analysis_pos{termnum}.html"

    today = timezone.localdate()

    raw_start_date = request.GET.get("start_date")
    raw_end_date = request.GET.get("end_date")
    user_filter = request.GET.get("user", "").strip()

    # Parse dates or default to today
    start_date = (
        datetime.strptime(raw_start_date, "%Y-%m-%d").date()
        if raw_start_date else today
    )
    end_date = (
        datetime.strptime(raw_end_date, "%Y-%m-%d").date()
        if raw_end_date else today
    )

    # Prevent future dates
    if start_date > today:
        start_date = today
    if end_date > today:
        end_date = today

    # Ensure start_date <= end_date
    if start_date > end_date:
        start_date = end_date

    # ==========================
    # FETCH SALES (slip_items)
    # ==========================
    sales_query = (
        supabase.table("slip_items")
        .select("*, slips(trandate, trantime)")
        .eq("termnum", termnum)
        .gte("trandate", start_date)
        .lte("trandate", end_date)
        .order("trandate", desc=True)
        .order("trantime", desc=True)
    )
    if user_filter:
        sales_query = sales_query.ilike("attendant", f"%{user_filter}%")

    all_sales, page, page_size = [], 0, 1000
    while True:
        resp = sales_query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = resp.data or []
        all_sales.extend(rows)
        if len(rows) < page_size:
            break
        page += 1

    # ==========================
    # FETCH FINANCIALS (slip_financials)
    # ==========================
    fin_query = (
        supabase.table("slip_financials")
        .select("*")
        .eq("termnum", termnum)
        .gte("trandate", start_date)
        .lte("trandate", end_date)
        .order("trandate", desc=True)
        .order("trantime", desc=True)
    )
    if user_filter:
        fin_query = fin_query.ilike("userid", f"%{user_filter}%")

    all_fin, page, page_size = [], 0, 1000
    while True:
        resp = fin_query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = resp.data or []
        all_fin.extend(rows)
        if len(rows) < page_size:
            break
        page += 1

    # ==========================
    # PROCESS SLIPS
    # ==========================
    slips = {}
    total_amount_paid = total_vat = total_returns = 0.0
    total_payouts = total_receipts = total_payments = 0.0

    for item in all_sales:
        slip_id = item.get("slip_id") or f"slip_{item.get('id')}"
        if slip_id not in slips:
            slips[slip_id] = {
                "userid": item.get("attendant") or "",
                "trandate": item.get("trandate"),
                "time": item.get("trantime"),
                "logfile": slip_id,
                "items": [],
                "amount_paid": 0.0,
                "vat": 0.0,
                "returns": 0.0,
                "payouts": 0.0,
                "receipts": 0.0,
                "payments": 0.0,
            }

        qty = float(item.get("qty") or 0)
        amount = float(item.get("total_price") or 0)
        vat = float(item.get("vat") or 0)

        slips[slip_id]["items"].append({
            "name": item.get("item_name") or "Unknown",
            "quantity": qty,
            "amount": amount,
            "vat": vat,
        })

        slips[slip_id]["amount_paid"] += amount
        slips[slip_id]["vat"] += vat

        total_amount_paid += amount
        total_vat += vat

    for fin in all_fin:
        slip_id = fin.get("slip_id") or f"fin_{fin.get('id')}"
        if slip_id not in slips:
            slips[slip_id] = {
                "userid": fin.get("userid") or "",
                "trandate": fin.get("trandate"),
                "time": fin.get("trantime"),
                "logfile": slip_id,
                "items": [],
                "amount_paid": 0.0,
                "vat": 0.0,
                "returns": 0.0,
                "payouts": 0.0,
                "receipts": 0.0,
                "payments": 0.0,
            }

        amount = float(fin.get("amount") or 0)
        fin_type = (fin.get("fin_type") or "").upper()

        if fin_type == "RETURN":
            slips[slip_id]["returns"] += amount
            total_returns += amount
        elif fin_type == "PAYOUT":
            slips[slip_id]["payouts"] += amount
            total_payouts += amount
        elif fin_type == "RECEIPT":
            slips[slip_id]["receipts"] += amount
            total_receipts += amount
        elif fin_type == "PAYMENT":
            slips[slip_id]["payments"] += amount
            total_payments += amount

    slips_list = list(slips.values())

    paginator = Paginator(slips_list, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    query_params = request.GET.copy()
    query_params.pop("page", None)
    querystring = urlencode(query_params)

    nett_takings = (
        total_amount_paid
        - total_returns
        - total_payouts
        - total_receipts
        - total_payments
    )

    return render(request, template_name, {
        "page_obj": page_obj,
        "querystring": querystring,
        "start_date": start_date,
        "end_date": end_date,
        "user_filter": user_filter,
        "total_transactions": len(slips_list),
        "total_amount_paid": total_amount_paid,
        "total_vat_amount": total_vat,
        "total_returns": total_returns,
        "total_payouts": total_payouts,
        "total_receipts": total_receipts,
        "total_payments": total_payments,
        "nett_takings": nett_takings,
        "today": today,  # for max date in inputs
    })


@login_required
def most_sold_items(request):
    """
    Display the most sold items combining POS2 and POS3.
    Aggregates quantity and total amount per item across both terminals.
    """
    template_name = "admin/most_sold_items.html"
    today = timezone.localdate()

    # Date filter
    raw_start_date = request.GET.get("start_date")
    raw_end_date = request.GET.get("end_date")
    item_filter = request.GET.get("item", "").strip()  # filter by item name

    start_date = datetime.strptime(raw_start_date, "%Y-%m-%d").date() if raw_start_date else today
    end_date = datetime.strptime(raw_end_date, "%Y-%m-%d").date() if raw_end_date else today

    # Prevent future dates
    start_date = min(start_date, today)
    end_date = min(end_date, today)
    if start_date > end_date:
        start_date = end_date

    # Combine sales from POS2 and POS3
    combined_sales = []

    for termnum in [2, 3]:
        query = (
            supabase.table("slip_items")
            .select("*")
            .eq("termnum", termnum)
            .gte("trandate", start_date)
            .lte("trandate", end_date)
            .order("trandate", desc=True)
            .order("trantime", desc=True)
        )
        if item_filter:
            query = query.ilike("item_name", f"%{item_filter}%")

        page, page_size = 0, 1000
        while True:
            resp = query.range(page * page_size, (page + 1) * page_size - 1).execute()
            rows = resp.data or []
            combined_sales.extend(rows)
            if len(rows) < page_size:
                break
            page += 1

    # Aggregate quantity and total amount per item
    item_totals = defaultdict(lambda: {"quantity": 0.0, "amount": 0.0})
    for item in combined_sales:
        name = item.get("item_name") or "Unknown"
        qty = float(item.get("qty") or 0)
        amount = float(item.get("total_price") or 0)
        item_totals[name]["quantity"] += qty
        item_totals[name]["amount"] += amount

    # Sort by quantity sold descending
    sorted_items = sorted(
        item_totals.items(), key=lambda x: x[1]["quantity"], reverse=True
    )

    # Pagination
    paginator = Paginator(sorted_items, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    query_params = request.GET.copy()
    query_params.pop("page", None)
    querystring = urlencode(query_params)

    return render(request, template_name, {
        "page_obj": page_obj,
        "querystring": querystring,
        "start_date": start_date,
        "end_date": end_date,
        "item_filter": item_filter,
        "today": today,
        "termnum": "POS2+POS3",  # for display
    })

# Report Views
def report_sections_view(request):
    """Display report sections with search functionality."""
    search_query = request.GET.get("search", "").strip()
    response = supabase.table("report_sections").select("*").execute()
    data = response.data if hasattr(response, 'data') else []

    # Sort by report_date descending
    data.sort(key=lambda x: x.get("report_date", ""), reverse=True)

    # Only latest 5 unless searching
    if not search_query:
        filtered = data[:5]
    else:
        filtered = [row for row in data if search_query.lower() in str(row).lower()]

    for i, row in enumerate(filtered):
        row["index"] = i + 1
        row["raw_text"] = row.get("data", {}).get("raw", "")

    section_names = list({row["section_name"] for row in data if "section_name" in row})

    return render(request, "admin/report_sections.html", {
        "sections": filtered,
        "search_query": search_query,
        "distinct_sections": sorted(section_names)
    })


def stdb_view(request):
    """Display stock on hand data."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

    # Get the search query
    search_query = request.GET.get('search', '').lower()

    # Fetch all data from Supabase
    url = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}?select=code,description,stdsell,openstock,qty,cat"
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        data = response.json()
    else:
        data = []

    # Filtering
    if search_query:
        data = [
            item for item in data
            if search_query in item.get('code', '').lower() or
               search_query in item.get('description', '').lower() or
               search_query in item.get('cat', '').lower()
        ]

    # Pagination
    paginator = Paginator(data, 100)  # 100 items per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'admin/stock_on_hand.html', {
        'products': page_obj,
        'search_query': search_query,
        'page_obj': page_obj,
    })

from django.shortcuts import render
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from collections import defaultdict
from datetime import datetime, timedelta
import json
import requests

# Supabase Edge Function details
SUPABASE_FUNCTION_URL = "https://afwwpqkcdivgezopclbz.functions.supabase.co/calc_totals"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFmd3dwcWtjZGl2Z2V6b3BjbGJ6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTM0NDQ2NjIsImV4cCI6MjA2OTAyMDY2Mn0.h31x2lYnlTGfvGicjjhnQpGTWeg5yfLvvnGDLZFWXro"  # Replace with your service role JWT


def call_calc_totals(transactions):
    """
    Sends transactions to the Supabase Edge Function.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",  # <-- use anon key
    }

    payload = {"transactions": transactions}  # <-- VERY IMPORTANT

    response = requests.post(
        SUPABASE_FUNCTION_URL,
        headers=headers,
        json=payload     # <-- NOT data= !!!
    )

    print("\n---- DEBUG ----")
    print("Sending payload:", json.dumps(payload)[:500])
    print("Supabase response:", response.text)
    print("Status code:", response.status_code)
    print("---- END DEBUG ----\n")

    if response.status_code == 200:
        return response.json()

    raise Exception(f"Supabase error: {response.status_code} {response.text}")


import calendar

@login_required
def admin_dashboard(request):
    # -----------------------------
    # Date filters
    # -----------------------------
    today = date.today()
    from_filter = request.GET.get("from")
    to_filter = request.GET.get("to")

    if from_filter:
        from_date = date.fromisoformat(from_filter)
    else:
        from_date = today.replace(day=1)

    if to_filter:
        to_date = date.fromisoformat(to_filter)
    else:
        to_date = today

    # -----------------------------
    # Supabase query (POS 1,2,3)
    # -----------------------------
    query = (
        supabase
        .table("slip_items")
        .select("*")
        .in_("termnum", [1, 2, 3])
        .gte("trandate", from_date.isoformat())
        .lte("trandate", to_date.isoformat())
    )

    # -----------------------------
    # Fetch all rows
    # -----------------------------
    all_data = []
    page = 0
    page_size = 1000

    while True:
        response = query.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = response.data or []
        all_data.extend(rows)
        if len(rows) < page_size:
            break
        page += 1

    # -----------------------------
    # Aggregations
    # -----------------------------
    total_revenue = 0.0
    total_vat = 0.0
    total_returns = 0.0
    total_transactions = 0

    fuel_total = 0.0
    nonfuel_total = 0.0
    diesel_total = 0.0
    unleaded_total = 0.0

    daily_sales = defaultdict(float)
    hourly_sales = defaultdict(float)
    staff_sales = defaultdict(lambda: {"sales": 0.0, "transactions": 0})

    seen_slips = set()

    for item in all_data:
        amount = float(item.get("total_price") or 0)
        vat = float(item.get("vat") or 0)
        qty = float(item.get("qty") or 0)
        name = (item.get("item_name") or "").upper()
        attendant = item.get("attendant") or "Unknown"
        trandate = item.get("trandate")
        trantime = item.get("trantime")
        slip_id = item.get("slip_id") or item.get("id")

        total_revenue += amount
        total_vat += vat

        if slip_id not in seen_slips:
            total_transactions += 1
            seen_slips.add(slip_id)

        # Fuel vs non-fuel
        if "DIESEL" in name or "UNLEADED" in name:
            fuel_total += amount
        else:
            nonfuel_total += amount

        if "DIESEL" in name:
            diesel_total += amount
        if "UNLEADED" in name:
            unleaded_total += amount

        # Daily trend
        if trandate:
            daily_sales[trandate] += amount

        # Hourly trend
        if trantime:
            hour = trantime[:2]
            hourly_sales[hour] += amount

        # Staff performance
        staff_sales[attendant]["sales"] += amount
        staff_sales[attendant]["transactions"] += 1

    # -----------------------------
    # Time-based metrics
    # -----------------------------
    today_sale = daily_sales.get(today.isoformat(), 0)

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    week_sale = sum(
        amt for d, amt in daily_sales.items()
        if week_start <= date.fromisoformat(d) <= week_end
    )

    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    month_sale = sum(
        amt for d, amt in daily_sales.items()
        if month_start <= date.fromisoformat(d) <= month_end
    )

    # -----------------------------
    # Charts
    # -----------------------------
    revenue_trend_labels = sorted(daily_sales.keys())
    revenue_trend_data = [daily_sales[d] for d in revenue_trend_labels]

    hourly_labels = sorted(hourly_sales.keys())
    hourly_data = [hourly_sales[h] for h in hourly_labels]

    # -----------------------------
    # Percentages & averages
    # -----------------------------
    fuel_percentage = (fuel_total / total_revenue * 100) if total_revenue else 0
    nonfuel_percentage = 100 - fuel_percentage

    avg_transaction_value = (
        total_revenue / total_transactions if total_transactions else 0
    )

    nett_revenue = total_revenue - total_returns

    # -----------------------------
    # Render
    # -----------------------------
    return render(request, "admin/admin_dashboard.html", {
        "from_filter": from_date,
        "to_filter": to_date,

        "today": today,
        "today_sale": today_sale,

        "week_sale": week_sale,
        "week_start": week_start,
        "week_end": week_end,

        "month_sale": month_sale,
        "month_start": month_start,
        "month_end": month_end,

        "total_revenue": total_revenue,
        "nett_revenue": nett_revenue,
        "total_vat": total_vat,
        "total_returns": total_returns,
        "total_transactions": total_transactions,
        "avg_transaction_value": avg_transaction_value,

        "fuel_total": fuel_total,
        "nonfuel_total": nonfuel_total,
        "fuel_percentage": fuel_percentage,
        "nonfuel_percentage": nonfuel_percentage,

        "diesel_total": diesel_total,
        "unleaded_total": unleaded_total,

        "staff_performance": dict(
            sorted(staff_sales.items(), key=lambda x: x[1]["sales"], reverse=True)
        ),

        "revenue_trend_labels": json.dumps(revenue_trend_labels),
        "revenue_trend_data": json.dumps(revenue_trend_data),

        "hourly_labels": json.dumps(hourly_labels),
        "hourly_sales": json.dumps(hourly_data),

        # Placeholder (can wire to stock table later)
        "inventory_alerts": [],
    })


@login_required
def return_transactions_view(request):
    """Display return transactions."""
    # Get filters
    from_date = request.GET.get("from")
    to_date = request.GET.get("to")

    # Fetch all data from posauds (optionally with from_date)
    data = fetch_posaud_sales(from_date=from_date)

    # Parse to_date (for filtering after fetch)
    if to_date:
        try:
            to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date()
            data = [row for row in data if row.get("trandate") and datetime.strptime(row["trandate"], "%Y-%m-%d").date() <= to_date_obj]
        except ValueError:
            pass

    # Group by slip (opref)
    grouped = defaultdict(list)
    for row in data:
        grouped[row.get("opref")].append(row)

    # Identify slips with return/refund, and track total return amount
    return_oprefs = set()
    total_returns = 0.0
    return_slips = []

    for opref, slip_rows in grouped.items():
        has_return = any("return" in (r.get("details") or "").lower() or "refund" in (r.get("details") or "").lower() for r in slip_rows)
        if has_return:
            return_oprefs.add(opref)
            return_slips.extend(slip_rows)
            for r in slip_rows:
                # Try to extract amount if present in detail line
                details = (r.get("details") or "").lower().replace(" ", "")
                if "return" in details or "refund" in details:
                    match = re.search(r"amount\s*:?\s*([\d]+(?:\.\d{1,2})?)", details)
                    if match:
                        try:
                            total_returns += float(match.group(1))
                        except ValueError:
                            pass

    # Sort the slips (latest first)
    def get_datetime(row):
        try:
            return datetime.strptime(f"{row['trandate']} {row['trantime']}", "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return datetime.min

    return_slips.sort(key=get_datetime, reverse=True)

    # Paginate
    paginator = Paginator(return_slips, 100)
    page_obj = paginator.get_page(request.GET.get("page"))

    now = datetime.now()
    context = {
        "returns": page_obj,
        "total_return_amount": round(total_returns, 2),
        "current_month": now.strftime("%B"),
        "current_year": now.year,
        "from_filter": from_date or "",
        "to_filter": to_date or "",
    }

    return render(request, "admin/return_transactions.html", context)


# Daily Sales Report Views
def parse_readings(details_lines):
    """Parse fuel meter readings from details lines."""
    ulp_reading_03 = None
    ulp_reading_01 = None
    d50_reading = None

    for line in details_lines:
        if not line:
            continue
        s = line.lower()

        if "03 unleaded 95" in s:
            match = re.search(r"03\s+unleaded 95\s+(\d+)", s)
            if match:
                ulp_reading_03 = int(match.group(1))

        elif "01 unleaded 95" in s:
            match = re.search(r"01\s+unleaded 95\s+(\d+)", s)
            if match:
                ulp_reading_01 = int(match.group(1))

        elif "diesel 50ppm" in s:
            match = re.search(r"50ppm\s+(\d+)", s)
            if match:
                d50_reading = int(match.group(1))

    ulp_reading = ulp_reading_03 if ulp_reading_03 else (ulp_reading_01 or 0)
    d50_reading = d50_reading if d50_reading is not None else 0

    return ulp_reading, d50_reading


from .models import DailySaleReport  # you might not use ORM for fetch/save
from accounts.utils.supabase_utils import supabase, fetch_daily_sales_reports

@csrf_exempt
@login_required
def save_fuel_rates(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            rate_r22_12 = data.get("rate_r22_12")
            rate_r23_36 = data.get("rate_r23_36")

            # Insert both rates as separate rows
            entries = []
            if rate_r22_12 is not None:
                entries.append({
                    "rate_name": "rate_r22_12",
                    "value": rate_r22_12
                })
            if rate_r23_36 is not None:
                entries.append({
                    "rate_name": "rate_r23_36", 
                    "value": rate_r23_36
                })

            if entries:
                response = supabase.table("fuel_rates").insert(entries).execute()

            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
    return JsonResponse({"success": False, "error": "Invalid request"})

def fetch_current_rates(as_of_date=None):
    if not as_of_date:
        as_of_date = datetime.now().date().isoformat()

    try:
        # Fetch both rates separately since they're stored as separate rows
        response_ulp = supabase.table("fuel_rates") \
            .select("*") \
            .eq("rate_name", "rate_r22_12") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        response_d50 = supabase.table("fuel_rates") \
            .select("*") \
            .eq("rate_name", "rate_r23_36") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        # Extract values
        ulp_rate = 0
        d50_rate = 0

        if response_ulp.data:
            ulp_rate = float(response_ulp.data[0].get("value", 0))
        
        if response_d50.data:
            d50_rate = float(response_d50.data[0].get("value", 0))

        return {
            "rate_r22_12": ulp_rate,
            "rate_r23_36": d50_rate,
        }

    except Exception as e:
        print(f"Error fetching fuel rates: {e}")
        # Return default rates in case of error
        return {
            "rate_r22_12": 0,
            "rate_r23_36": 0,
        }


@login_required
def daily_sales_report(request):
    """Render the daily sales report template, fetching from Supabase."""
    from_date = request.GET.get("from")
    to_date = request.GET.get("to")
    export = request.GET.get("export")

    today = datetime.now().date()

    try:
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d").date() if from_date else today
    except ValueError:
        from_date_obj = today
    try:
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date() if to_date else from_date_obj
    except ValueError:
        to_date_obj = from_date_obj

    from_date = from_date or today.strftime("%Y-%m-%d")
    to_date = to_date or from_date

    # Fetch current fuel rates
    current_rates = fetch_current_rates()

    # Fetch saved records (list of dicts) from Supabase
    saved = fetch_daily_sales_reports(from_date, to_date)
    # Transform saved list into a lookup by date
    saved_lookup = { rec["report_date"]: rec for rec in saved }

    # Build combined list: for every date in the range, either saved or default
    date_range = pd.date_range(start=from_date_obj, end=to_date_obj)
    daily_sales = []
    for dt in date_range:
        date_str = dt.strftime("%Y-%m-%d")
        # In the part where you transform saved records
        if date_str in saved_lookup:
            rec = saved_lookup[date_str]
            row = {
                "date": rec.get("report_date"),
                "ulp_open": rec.get("unleaded_95_opening", 0),
                "ulp_close": rec.get("unleaded_95_closing", 0),
                "d50_open": rec.get("diesel_50_opening", 0),
                "d50_close": rec.get("diesel_50_closing", 0),
                "actual_pos": rec.get("actual_pos", 0),
                "cash": rec.get("cash", 0),
                "cards": rec.get("cards", 0),
                "expenses": rec.get("expenses", 0),
                "comments": rec.get("comments", ""),
                # Use the stored rates for this specific report
                "rate_ulp_used": rec.get("rate_ulp_95_used", current_rates["rate_r22_12"]),
                "rate_d50_used": rec.get("rate_d50_used", current_rates["rate_r23_36"]),
                # Include calculated values for display
                "litres_ulp": rec.get("dispensed_ulp_95", 0),
                "litres_d50": rec.get("dispensed_d50", 0),
                "r_ulp": rec.get("r_ulp", 0),
                "r_d50": rec.get("r_d50", 0),
                "pumped_theoretical": rec.get("pumped_theoretical", 0),
                "variance_pos": rec.get("variance_pos", 0),
                "actual_sales": rec.get("actual_sales", 0),
                "variance_sales": rec.get("variance_sales", 0),
                "grand_total": rec.get("grand_total", 0),
                "over_short": rec.get("over_short", 0),
            }
        else:
            # For unsaved rows, use current rates
            row = {
                "date": date_str,
                "ulp_open": 0,
                "ulp_close": 0,
                "d50_open": 0,
                "d50_close": 0,
                "actual_pos": 0,
                "cash": 0,
                "cards": 0,
                "expenses": 0,
                "comments": "",
                "rate_ulp_used": current_rates["rate_r22_12"],
                "rate_d50_used": current_rates["rate_r23_36"],
                # Default calculated values
                "litres_ulp": 0.00,
                "litres_d50": 0.00,
                "r_ulp": 0.00,
                "r_d50": 0.00,
                "pumped_theoretical": 0.00,
                "variance_pos": 0.00,
                "actual_sales": 0.00,
                "variance_sales": 0.00,
                "grand_total": 0.00,
                "over_short": 0.00,
            }
        daily_sales.append(row)

    if export == "excel":
        df = pd.DataFrame(daily_sales)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Sales")
        buf.seek(0)
        fname = f"report_{from_date}_to_{to_date}.xlsx"
        response = HttpResponse(
            buf,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename={fname}'
        return response

    paginator = Paginator(daily_sales, 100)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "admin/daily_sales_report.html", {
        "daily_sales": page_obj,
        "from_filter": from_date,
        "to_filter": to_date,
        "current_month": from_date_obj.strftime("%B"),
        "current_year": from_date_obj.year,
        "current_rates": current_rates,  # pass rates here
    })


@csrf_exempt
@login_required
def save_daily_sale(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            date_str = data.get("date")
            
            # Get the rates that were used for this calculation (from the request)
            rate_ulp_used = data.get("rate_ulp_used", 0)
            rate_d50_used = data.get("rate_d50_used", 0)
            
            # Build payload with only the columns that exist in your table
            payload = {
                "report_date": date_str,
                "unleaded_95_opening": data.get("ulp_open", 0),
                "unleaded_95_closing": data.get("ulp_close", 0),
                "diesel_50_opening": data.get("d50_open", 0),
                "diesel_50_closing": data.get("d50_close", 0),
                "actual_pos": data.get("actual_pos", 0),
                "cash": data.get("cash", 0),
                "cards": data.get("cards", 0),
                "expenses": data.get("expenses", 0),
                "comments": data.get("comments", ""),
                # Store the rates that were used for this specific report
                "rate_ulp_95_used": rate_ulp_used,
                "rate_d50_used": rate_d50_used,
                # Store calculated values - use the column names that exist in your table
                "dispensed_ulp_95": data.get("litres_ulp", 0),
                "dispensed_d50": data.get("litres_d50", 0),
                # If these columns don't exist, we'll calculate them on the fly instead
                "pumped_theoretical": data.get("pumped_theoretical", 0),
                "variance_pos": data.get("variance_pos", 0),
                "actual_sales": data.get("actual_sales", 0),
                "variance_sales": data.get("variance_sales", 0),
                "grand_total": data.get("grand_total", 0),
                "over_short": data.get("over_short", 0)
            }
            
            # Remove any None values to avoid errors
            payload = {k: v for k, v in payload.items() if v is not None}
            
            # Upsert to Supabase
            response = supabase.table("daily_sales_reports").upsert(
                payload, 
                on_conflict="report_date"
            ).execute()
            
            return JsonResponse({"success": True})
            
        except Exception as e:
            print(f"Error saving daily sale: {e}")
            return JsonResponse({"success": False, "error": str(e)})
    
    return JsonResponse({"success": False, "error": "Invalid request"})
# Invoice Management Views
@login_required
def invoice_entry_page(request):
    """Display invoice entry page."""
    return render(request, "admin/invoice_entry.html")


def get_forecourt_invoices(request):
    """Get all forecourt invoices."""
    response = INVOICE_SUPABASE.table(INVOICE_TABLE).select("*").execute()

    data = None
    error = None
    try:
        data = response["data"]
        error = response.get("error")
    except (KeyError, TypeError):
        data = getattr(response, "data", None)
        error = getattr(response, "error", None)

    if error:
        print(f"Supabase error getting invoices: {error}")
        return JsonResponse({"success": False, "error": str(error)}, status=500)

    invoices = data or []
    for inv in invoices:
        if inv.get("invoice_date"):
            inv["invoice_date"] = inv["invoice_date"][:10]

    return JsonResponse({"success": True, "invoices": invoices})


@csrf_exempt
def save_forecourt_invoices(request):
    """Save forecourt invoices to database."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid method"}, status=405)

    try:
        invoices = json.loads(request.body).get("invoices", [])
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    response = INVOICE_SUPABASE.table(INVOICE_TABLE).insert(invoices).execute()

    # Check for error attribute or dict key
    error = None
    if hasattr(response, "error") and response.error:
        error = response.error
    elif isinstance(response, dict) and "error" in response and response["error"]:
        error = response["error"]

    if error:
        print(f"Error inserting invoices: {error}")
        return JsonResponse({"success": False, "error": str(error)}, status=500)

    return JsonResponse({"success": True, "message": "Invoices saved successfully"})


@csrf_exempt
def delete_forecourt_invoice(request, invoice_number):
    """Delete forecourt invoice."""
    if request.method != "DELETE":
        return JsonResponse({"success": False, "error": "Invalid method"}, status=405)

    response = INVOICE_SUPABASE.table(INVOICE_TABLE).delete().eq("invoice_number", invoice_number).execute()

    # You might want to adjust this depending on your supabase response structure
    if hasattr(response, "status_code") and response.status_code != 200:
        return JsonResponse({"success": False, "error": "Failed to delete invoice"}, status=500)

    if not getattr(response, "data", None):
        return JsonResponse({"success": False, "error": "Invoice not found"}, status=404)

    return JsonResponse({"success": True, "message": "Invoice deleted"})


@csrf_exempt
def upload_invoice_pdf(request):
    """Upload invoice PDF to storage."""
    if request.method == "POST":
        pdf_file = request.FILES.get("pdf")
        invoice_number = request.POST.get("invoice_number", "unknown")

        if not pdf_file:
            return JsonResponse({"success": False, "error": "No file uploaded."})

        try:
            filename = f"{invoice_number}_{pdf_file.name}"

            # Add content-type explicitly
            response = INVOICE_SUPABASE.storage.from_("invoices").upload(
                path=filename,
                file=pdf_file.read(),
                file_options={"content-type": "application/pdf"}
            )

            if not hasattr(response, "path") and not (isinstance(response, dict) and "path" in response):
                return JsonResponse({"success": False, "error": "Upload failed"})

            public_url = INVOICE_SUPABASE.storage.from_("invoices").get_public_url(filename)

            update_response = INVOICE_SUPABASE.table("forecourt_invoices") \
                .update({"invoice_pdf_url": public_url}) \
                .eq("invoice_number", invoice_number) \
                .execute()

            error = None
            if hasattr(update_response, "error") and update_response.error:
                error = update_response.error
            elif isinstance(update_response, dict) and "error" in update_response and update_response["error"]:
                error = update_response["error"]

            if error:
                return JsonResponse({"success": False, "error": f"Failed to update invoice with PDF URL: {error}"})

            return JsonResponse({"success": True, "url": public_url})

        except Exception as e:
            print(f"Exception uploading PDF: {e}")
            return JsonResponse({"success": False, "error": str(e)})

    return JsonResponse({"success": False, "error": "Invalid request method."})