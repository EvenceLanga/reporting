from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    username = None  # Remove username field
    email = models.EmailField(unique=True)

    full_name = models.CharField(max_length=255)
    contact_number = models.CharField(max_length=20)
    home_address = models.TextField()

    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
    ]
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)

    STORE_CHOICES = [
        ('Mabokelele Feed', 'Mabokelele Feed'),
        ('Mamehlabe Shop', 'Mamehlabe Shop'),
        ('Mamehlabe Garage', 'Mamehlabe Garage'),
        ('Mabokelele Garage', 'Mabokelele Garage'),
    ]
    store = models.CharField(max_length=50, choices=STORE_CHOICES)

    must_change_password = models.BooleanField(default=False)

    # --- Add these fields ---
    failed_attempts = models.PositiveIntegerField(default=0)
    is_locked = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name', 'contact_number', 'home_address', 'role', 'store']

    objects = CustomUserManager()

    def __str__(self):
        return self.email

class Stdb(models.Model):
    code = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255)
    vatcode = models.CharField(max_length=10)  # e.g., "s" or "z"
    stdsell = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.code} — {self.description}"
    

# models.py

class FuelCashUp(models.Model):
    FUEL_CHOICES = [
        ('UNLEADED 95', 'UNLEADED 95'),
        ('DIESEL 50PPM', 'DIESEL 50PPM'),
    ]

    date = models.DateField(default=timezone.now)
    fuel_type = models.CharField(max_length=20, choices=FUEL_CHOICES)
    opening_litres = models.DecimalField(max_digits=10, decimal_places=2)
    closing_litres = models.DecimalField(max_digits=10, decimal_places=2)
    litres_pumped = models.DecimalField(max_digits=10, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.date} - {self.fuel_type}"


class ForecourtInvoice(models.Model):
    STATUS_CHOICES = [
        ("PAID", "Paid"),
        ("NOT PAID", "Not Paid")
    ]

    invoice_number = models.CharField(max_length=20, unique=True)
    invoice_date = models.DateField()
    total_excl_vat = models.DecimalField(max_digits=12, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.invoice_number



class DailySaleReport(models.Model):
    report_date = models.DateField(unique=True)

    unleaded_95_opening = models.FloatField(default=0)
    unleaded_95_closing = models.FloatField(default=0)
    diesel_50_opening = models.FloatField(default=0)
    diesel_50_closing = models.FloatField(default=0)

    dispensed_ulp_95 = models.FloatField(default=0)
    dispensed_d50 = models.FloatField(default=0)
    rate_r22_12 = models.FloatField(default=0)
    rate_r23_36 = models.FloatField(default=0)
    pumped_theoretical = models.FloatField(default=0)

    actual_pos = models.FloatField(default=0)
    variance_pos = models.FloatField(default=0)

    cash = models.FloatField(default=0)
    cards = models.FloatField(default=0)
    actual_sales = models.FloatField(default=0)
    variance_sales = models.FloatField(default=0)

    expenses = models.FloatField(default=0)
    grand_total = models.FloatField(default=0)
    over_short = models.FloatField(default=0)

    comments = models.TextField(blank=True)

    def __str__(self):
        return f"DailySaleReport {self.report_date}"

    class Meta:
        db_table = "daily_sales_reports"  # 👈 This tells Django to use your Supabase table

class FuelRate(models.Model):
    rate_r22_12 = models.DecimalField(max_digits=6, decimal_places=2)  # e.g., 9999.99
    rate_r23_36 = models.DecimalField(max_digits=6, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ULP: R{self.rate_r22_12}, D50: R{self.rate_r23_36} @ {self.created_at}"

class Slip(models.Model):
    slip_id = models.BigAutoField(primary_key=True)
    opref = models.TextField()
    termnum = models.IntegerField()
    trandate = models.DateField()
    trantime = models.TimeField()
    userid = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "slips"
        managed = False

    def __str__(self):
        return f"{self.opref} (POS{self.termnum})"


class SlipItem(models.Model):
    id = models.BigAutoField(primary_key=True)

    slip = models.ForeignKey(
        Slip,
        on_delete=models.CASCADE,
        db_column="slip_id",
        related_name="items",
    )

    opref = models.TextField()
    termnum = models.IntegerField()
    trandate = models.DateField()
    trantime = models.TimeField()
    userid = models.TextField()

    attendant = models.TextField(null=True, blank=True)
    seqno = models.IntegerField()
    raw_details = models.TextField()

    item_name = models.TextField()
    item_code = models.TextField(null=True, blank=True)
    qty = models.DecimalField(max_digits=10, decimal_places=3)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    vat = models.DecimalField(max_digits=10, decimal_places=2)

    created_at = models.DateTimeField()

    class Meta:
        db_table = "slip_items"
        managed = False

    def __str__(self):
        return f"{self.item_name} ({self.qty} × {self.unit_price})"
