from django import forms
from .models import CustomUser

class AdminUserCreationForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['full_name', 'email', 'contact_number', 'home_address', 'role', 'store']

        widgets = {
            'role': forms.Select(attrs={'class': 'form-select'}),
            'store': forms.Select(attrs={'class': 'form-select'}),
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'contact_number': forms.TextInput(attrs={'class': 'form-control'}),
            'home_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

class CustomUserForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['full_name', 'email', 'contact_number', 'home_address', 'role', 'store']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'contact_number': forms.TextInput(attrs={'class': 'form-control'}),
            'home_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'store': forms.Select(attrs={'class': 'form-select'}),
        }

from django import forms
from datetime import date

FUEL_CHOICES = [
    ('UNLEADED 95', 'UNLEADED 95'),
    ('DIESEL 50PPM', 'DIESEL 50PPM'),
]

class FuelCashUpForm(forms.Form):
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}),
        initial=date.today,
        required=True
    )
    fuel_type = forms.ChoiceField(choices=FUEL_CHOICES, label="Fuel Type")
    opening_litres = forms.DecimalField(decimal_places=2, label="Opening (L)")
    closing_litres = forms.DecimalField(decimal_places=2, label="Closing (L)")
