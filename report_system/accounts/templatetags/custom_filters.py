# custom_filters.py
from django import template

register = template.Library()

@register.filter
def lookup(dictionary, key):
    """Get value from a dict or return 0 if missing."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, 0)
    return 0
