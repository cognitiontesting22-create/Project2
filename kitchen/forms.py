from django import forms
from django.forms import inlineformset_factory
from .models import Ingredient, KitchenSettings, MealPlan, Recipe, PantryItem, NutritionFood, NutritionAlias
from django.utils import timezone
from .measurements import MAX_SERVINGS, parse_quantity, format_quantity, normalize_name
from .recipe_import import MAX_TEXT_LENGTH


class RecipePasteForm(forms.Form):
    text = forms.CharField(label="Recipe text", max_length=MAX_TEXT_LENGTH, strip=False,
        widget=forms.Textarea(attrs={"rows": 18, "placeholder": "Lemon pancakes\nServes 4\nPrep time: 10 minutes\nCook time: 15 minutes\n\nIngredients\n1½ cups plain flour\n2 eggs\n1 cup milk\n\nMethod\n1. Whisk the ingredients together.\n2. Cook spoonfuls in a warm pan until golden.", "spellcheck": "false", "aria-describedby": "paste-help"}))

    def clean_text(self):
        text = self.cleaned_data["text"]
        if not text.strip():
            raise forms.ValidationError("Paste a recipe first.")
        return text

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


class NutritionFoodForm(forms.ModelForm):
    aliases = forms.CharField(required=False, max_length=4000, label="Matching ingredient names", widget=forms.Textarea(attrs={"rows": 2, "placeholder": "e.g. plain flour, all-purpose flour"}))

    class Meta:
        model = NutritionFood
        fields = ["name", "description", "calories", "protein", "carbs", "fat", "cup_grams", "item_grams", "slice_grams", "clove_grams", "can_grams", "portion_notes", "source_label", "source_url"]
        labels = {"name": "Food name", "description": "Food and preparation", "calories": "Calories (kcal)", "protein": "Protein (g)", "carbs": "Carbohydrates (g)", "fat": "Fat (g)", "cup_grams": "Grams per US cup", "item_grams": "Grams per whole item", "slice_grams": "Grams per slice", "clove_grams": "Grams per clove", "can_grams": "Grams per can", "portion_notes": "Portion assumptions", "source_label": "Source or product label", "source_url": "Source link"}
        widgets = {"portion_notes": forms.Textarea(attrs={"rows": 3}), "description": forms.TextInput(attrs={"placeholder": "e.g. dry rice, cooked rice, or a specific brand"}), "source_url": forms.URLInput(attrs={"placeholder": "https://…"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["aliases"] = ", ".join(self.instance.aliases.exclude(name=normalize_name(self.instance.name)).values_list("name", flat=True))

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())

    def clean(self):
        cleaned = super().clean()
        text = cleaned.get("aliases", "")
        names = {normalize_name(value) for value in text.replace("\n", ",").split(",") if value.strip()}
        if cleaned.get("name"):
            names.add(normalize_name(cleaned["name"]))
        if any(len(name) > 120 for name in names) or len(names) > 50:
            self.add_error("aliases", "Use at most 50 names, each no longer than 120 characters.")
        conflicts = NutritionAlias.objects.filter(name__in=names).exclude(food_id=self.instance.pk).values_list("name", flat=True)
        if conflicts:
            self.add_error("aliases", "Already matched to another food: " + ", ".join(conflicts))
        self.alias_names = names
        return cleaned

    def save_aliases(self):
        self.instance.aliases.exclude(name__in=self.alias_names).delete()
        for name in self.alias_names:
            NutritionAlias.objects.get_or_create(name=name, defaults={"food": self.instance})

    def save(self, commit=True):
        food = super().save(commit=commit)
        if commit:
            self.save_aliases()
        return food
