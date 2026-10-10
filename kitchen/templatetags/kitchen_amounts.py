from django import template
from kitchen.measurements import format_quantity

register = template.Library()

@register.filter
def kitchen_amount(value):
    return format_quantity(value)
