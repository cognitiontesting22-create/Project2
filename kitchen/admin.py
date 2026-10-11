from django.contrib import admin
from .models import Ingredient, KitchenSettings, MealPlan, Recipe, PantryItem, CookingRecord, NutritionFood
from .forms import IngredientForm, NutritionFoodForm

class IngredientInline(admin.TabularInline):
    model = Ingredient
    form = IngredientForm
    extra = 1

@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "servings", "favorite")
    search_fields = ("title", "ingredients__name")
    list_filter = ("category", "favorite", "vegetarian")
    inlines = [IngredientInline]

admin.site.register(MealPlan)
admin.site.register(KitchenSettings)
admin.site.register(PantryItem)
@admin.register(CookingRecord)
class CookingRecordAdmin(admin.ModelAdmin):
    list_display = ("recipe_title", "cooked_on", "servings", "undone_at")
    readonly_fields = [field.name for field in CookingRecord._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(NutritionFood)
class NutritionFoodAdmin(admin.ModelAdmin):
    form = NutritionFoodForm
    list_display = ("name", "calories", "protein", "carbs", "fat", "source_label")
    search_fields = ("name", "description", "aliases__name")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.save_aliases()
