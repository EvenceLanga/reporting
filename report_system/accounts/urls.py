from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from accounts.views import custom_login_view

urlpatterns = [
    path('', views.home_view, name='home'),
    path('login/', custom_login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('admin_dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('mamehlabe_store/', views.mamehlabe_store, name='mamehlabe_store'),
    path('create_user/', views.create_user_view, name='create_user'),
     path('most_sold/', views.most_sold_items, name='most_sold_items'),
    path('force-password-change/', views.force_password_change, name='force_password_change'),
    # urls.py
    path("pos_1/", views.till_slip_analysis_pos1, name="pos_1"),
    path("pos_2/", lambda r: views.till_slip_analysis_dynamic(r, 2), name="pos_2"),
    path("pos_3/", lambda r: views.till_slip_analysis_dynamic(r, 3), name="pos_3"),
    
    path('report_sections/', views.report_sections_view, name='report_sections'),
    path('stock_on_hand/', views.stdb_view, name='stock_on_hand'),
    path('inventory/', views.stdb_view, name='stock_on_hand'),
    path('daily_sales_report/', views.daily_sales_report, name='daily_sales_report'),
    path("save-daily-sale/", views.save_daily_sale, name="save_daily_sale"),
    path('return_transactions/', views.return_transactions_view, name='return_transactions'),
    path("save_fuel_rates/", views.save_fuel_rates, name="save_fuel_rates"),

    path("invoices/", views.invoice_entry_page, name="invoice_entry_page"),
    path("invoices/get/", views.get_forecourt_invoices, name="get_forecourt_invoices"),
    path("invoices/save/", views.save_forecourt_invoices, name="save_forecourt_invoices"),
    path("invoices/delete/<str:invoice_number>/", views.delete_forecourt_invoice, name="delete_forecourt_invoice"),
    path("upload-invoice-pdf/", views.upload_invoice_pdf, name="upload_invoice_pdf"),
    path('password_change/', auth_views.PasswordChangeView.as_view( template_name='auth/password_change_form.html'), name='password_change'),
    
    path('password_change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='auth/password_change_done.html'), name='password_change_done'),

    path('user_list/', views.user_list, name='user_list'),
    path('users/', views.user_list, name='user_list'),
    path('users/edit/<int:user_id>/', views.edit_user, name='edit_user'),
    path('users/delete/<int:user_id>/', views.delete_user, name='delete_user'),
    
]
