from django import forms
from django.forms import inlineformset_factory
from .models import Ingredient, KitchenSettings, MealPlan, Recipe, PantryItem
from django.utils import timezone
from .measurements import MAX_SERVINGS, parse_quantity, format_quantity

class RecipeForm(forms.ModelForm):
    class Meta:
        model = Recipe
        fields = ["title", "description", "category", "prep_time", "cook_time", "servings", "measurement_system", "image", "image_url", "instructions", "notes", "vegetarian", "high_protein", "seasonal", "tag"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2, "placeholder": "What makes this recipe special?"}),
            "instructions": forms.Textarea(attrs={"rows": 8, "placeholder": "Preheat the oven to 200°C.\nPrepare your ingredients.\nWrite each step on a new line…"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Substitutions, serving ideas, or a little family secret…"}),
            "title": forms.TextInput(attrs={"placeholder": "Give your recipe a delicious name"}),
            "image": forms.FileInput(attrs={"accept": "image/*"}),
            "image_url": forms.URLInput(attrs={"placeholder": "https://…"}),
            "tag": forms.TextInput(attrs={"placeholder": "e.g. Weeknight favorite"}),
        }

    def clean_image(self):
        value = self.cleaned_data.get("image")
        if value and value.size > 8 * 1024 * 1024:
            raise forms.ValidationError("Please choose an image smaller than 8 MB.")
        return value

class QuantityField(forms.CharField):
    def to_python(self, value):
        text = super().to_python(value)
        if not text:
            return text
        try:
            return str(parse_quantity(text))
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

class IngredientForm(forms.ModelForm):
    quantity = QuantityField(max_length=80, widget=forms.TextInput(attrs={"placeholder": "1½ or 0.5", "aria-label": "Quantity", "autocomplete": "off"}))

    class Meta:
        model = Ingredient
        fields = ("quantity", "unit", "name")
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Ingredient", "aria-label": "Ingredient name"}), "unit": forms.TextInput(attrs={"placeholder": "g, tbsp, cups…", "aria-label": "Unit", "list": "ingredient-units"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["quantity"] = format_quantity(self.instance.exact_quantity)
            # Editing must preserve the exact original, even for uncommon fractions.
            if self.initial["quantity"].startswith("≈"):
                self.initial["quantity"] = str(self.instance.exact_quantity)

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

IngredientFormSet = inlineformset_factory(Recipe, Ingredient, form=IngredientForm, extra=1, min_num=1, validate_min=True, can_delete=True, max_num=100, validate_max=True)

class MealPlanForm(forms.Form):
    recipe = forms.ModelChoiceField(queryset=Recipe.objects.all())
    date = forms.DateField(widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    meal = forms.ChoiceField(choices=MealPlan.MEAL_CHOICES)
    servings = forms.IntegerField(min_value=1, max_value=MAX_SERVINGS)

    def clean_date(self):
        day = self.cleaned_data["date"]
        if not 1900 <= day.year <= 2100:
            raise forms.ValidationError("Please choose a date between 1900 and 2100.")
        return day

class SettingsForm(forms.ModelForm):
    class Meta:
        model = KitchenSettings
        fields = ["name", "kitchen_name", "measurement_system"]

class StockQuantityField(QuantityField):
    def to_python(self, value):
        text = forms.CharField.to_python(self, value)
        if not text:
            return text
        try:
            return str(parse_quantity(text, allow_zero=True, max_length=1000))
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

class PantryForm(forms.ModelForm):
    quantity = StockQuantityField(max_length=1000, widget=forms.TextInput(attrs={"placeholder": "e.g. 1½ or 500", "autocomplete": "off"}))

    class Meta:
        model = PantryItem
        fields = ["name", "quantity", "unit", "measurement_system", "location", "expires_on"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. plain flour", "list": "pantry-ingredient-names"}), "unit": forms.TextInput(attrs={"placeholder": "g, cups, each…", "list": "ingredient-units"}), "expires_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["quantity"] = str(self.instance.exact_quantity)

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

class CookForm(forms.Form):
    servings = forms.IntegerField(min_value=1, max_value=MAX_SERVINGS, widget=forms.HiddenInput())
    action_token = forms.UUIDField(widget=forms.HiddenInput())
    cooked_on = forms.DateField(widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    notes = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "How did it turn out? Any little tweaks for next time?"}))

    def clean_cooked_on(self):
        day = self.cleaned_data["cooked_on"]
        if day.year < 1900 or day > timezone.localdate():
            raise forms.ValidationError("Choose today or a past date after 1900.")
        return day
