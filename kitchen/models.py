from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.templatetags.static import static
from .measurements import MEASUREMENT_SYSTEMS, validate_quantity, validate_stock_quantity
from django.utils import timezone
import uuid
from decimal import Decimal

class Recipe(models.Model):
    CATEGORY_CHOICES = [(x, x) for x in ["Breakfast", "Lunch", "Dinner", "Dessert", "Snack"]]
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=1000)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="Dinner")
    prep_time = models.PositiveIntegerField(default=10, validators=[MinValueValidator(0), MaxValueValidator(600)])
    cook_time = models.PositiveIntegerField(default=20, validators=[MinValueValidator(0), MaxValueValidator(600)])
    servings = models.PositiveIntegerField(default=2, validators=[MinValueValidator(1)])
    measurement_system = models.CharField(max_length=10, choices=MEASUREMENT_SYSTEMS, default="us")
    image = models.ImageField(upload_to="recipes/", blank=True)
    image_url = models.URLField(blank=True)
    photo = models.CharField(max_length=100, blank=True)
    instructions = models.TextField(help_text="Write each step on a new line.")
    notes = models.TextField(blank=True)
    vegetarian = models.BooleanField(default=False)
    high_protein = models.BooleanField(default=False)
    seasonal = models.BooleanField(default=False)
    favorite = models.BooleanField(default=False)
    tag = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "pk"]

    def __str__(self):
        return self.title

    @property
    def total_time(self):
        return self.prep_time + self.cook_time

    @property
    def image_src(self):
        if self.image:
            return self.image.url
        return self.image_url or static("images/" + (self.photo or "recipe-placeholder.svg"))

    @property
    def steps(self):
        return [s.strip() for s in self.instructions.splitlines() if s.strip()]

class Ingredient(models.Model):
    recipe = models.ForeignKey(Recipe, related_name="ingredients", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    quantity = models.CharField(max_length=80, validators=[validate_quantity], help_text="A decimal or fraction, such as 1½ or 1/3.")
    unit = models.CharField(max_length=30, blank=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self):
        from .measurements import quantity_display
        display = quantity_display(self.exact_quantity, self.unit, self.recipe.measurement_system)
        return f'{display["amount_display"]} {display["unit"]} {self.name}'.strip()

    @property
    def exact_quantity(self):
        from .measurements import parse_quantity
        return parse_quantity(self.quantity)

class MealPlan(models.Model):
    MEAL_CHOICES = [(x, x) for x in ["Breakfast", "Lunch", "Dinner"]]
    recipe = models.ForeignKey(Recipe, related_name="planned_meals", on_delete=models.CASCADE)
    date = models.DateField()
    meal = models.CharField(max_length=15, choices=MEAL_CHOICES, default="Dinner")
    servings = models.PositiveIntegerField(default=2, validators=[MinValueValidator(1)])

    class Meta:
        ordering = ["date", "meal"]
        constraints = [models.UniqueConstraint(fields=["date", "meal"], name="one_recipe_per_meal_slot")]

class ShoppingCheck(models.Model):
    week = models.DateField()
    key = models.CharField(max_length=64)
    checked = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["week", "key"], name="one_check_per_week_item")]

class KitchenSettings(models.Model):
    name = models.CharField(max_length=50, default="Alex")
    kitchen_name = models.CharField(max_length=60, default="My little kitchen")
    measurement_system = models.CharField(max_length=10, choices=MEASUREMENT_SYSTEMS, default="us")

class PantryItem(models.Model):
    LOCATION_CHOICES = [("pantry", "Pantry"), ("fridge", "Fridge"), ("freezer", "Freezer")]
    name = models.CharField(max_length=120)
    quantity = models.CharField(max_length=1000, validators=[validate_stock_quantity])
    unit = models.CharField(max_length=30, blank=True)
    measurement_system = models.CharField(max_length=10, choices=MEASUREMENT_SYSTEMS, default="us")
    location = models.CharField(max_length=10, choices=LOCATION_CHOICES, default="pantry")
    expires_on = models.DateField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "expires_on", "pk"]

    @property
    def exact_quantity(self):
        from .measurements import parse_quantity
        return parse_quantity(self.quantity, allow_zero=True, max_length=1000)

    @property
    def expired(self):
        return self.expires_on is not None and self.expires_on < timezone.localdate()

    def __str__(self):
        return self.name

class CookingRecord(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.SET_NULL, null=True, blank=True, related_name="cooking_records")
    plan = models.ForeignKey(MealPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name="cooking_records")
    recipe_title = models.CharField(max_length=120)
    image_snapshot = models.CharField(max_length=2048, blank=True)
    servings = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    cooked_on = models.DateField(default=timezone.localdate)
    cooked_at = models.DateTimeField(auto_now_add=True)
    action_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    deductions = models.JSONField(default=list)
    shortages = models.JSONField(default=list)
    notes = models.TextField(blank=True, max_length=500)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-cooked_on", "-cooked_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["plan"], condition=models.Q(undone_at__isnull=True, plan__isnull=False), name="one_active_cooking_record_per_plan")]

    def __str__(self):
        return f"{self.recipe_title} · {self.cooked_on}"


class NutritionFood(models.Model):
    """Local composition values per 100 g of edible food, with optional weights."""
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    calories = models.DecimalField(max_digits=9, decimal_places=3, validators=[MinValueValidator(0), MaxValueValidator(1000)])
    protein = models.DecimalField(max_digits=8, decimal_places=3, validators=[MinValueValidator(0), MaxValueValidator(100)])
    carbs = models.DecimalField(max_digits=8, decimal_places=3, validators=[MinValueValidator(0), MaxValueValidator(100)])
    fat = models.DecimalField(max_digits=8, decimal_places=3, validators=[MinValueValidator(0), MaxValueValidator(100)])
    cup_grams = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0001"))])
    item_grams = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0001"))])
    slice_grams = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0001"))])
    clove_grams = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0001"))])
    can_grams = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal("0.0001"))])
    portion_notes = models.TextField(blank=True, max_length=1000)
    source_label = models.CharField(max_length=150, blank=True)
    source_url = models.URLField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class NutritionAlias(models.Model):
    name = models.CharField(max_length=120, unique=True)
    food = models.ForeignKey(NutritionFood, related_name="aliases", on_delete=models.CASCADE)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        from .measurements import normalize_name
        self.name = normalize_name(self.name)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name
