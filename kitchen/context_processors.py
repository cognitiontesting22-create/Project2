from django.db.models import F, Q
from .models import KitchenSettings, Recipe, PantryItem
from django.utils import timezone

def kitchen_context(request):
    profile, _ = KitchenSettings.objects.get_or_create(pk=1)
    return {
        "profile": profile,
        "recipe_count": Recipe.objects.count(),
        "favorite_count": Recipe.objects.filter(favorite=True).count(),
        "pantry_count": PantryItem.objects.exclude(quantity="0").filter(Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate())).count(),
        "quick_count": Recipe.objects.annotate(total=F("prep_time") + F("cook_time")).filter(total__lte=30).count(),
        "vegetarian_count": Recipe.objects.filter(vegetarian=True).count(),
        "baking_count": Recipe.objects.filter(Q(category="Dessert") | Q(tag__icontains="bak")).count(),
    }
