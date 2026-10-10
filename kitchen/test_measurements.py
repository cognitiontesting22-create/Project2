from datetime import timedelta
from fractions import Fraction
from django.test import SimpleTestCase, TestCase
from django.apps import apps
from django.db import connection
from importlib import import_module
import hashlib
from types import SimpleNamespace
from django.urls import reverse
from .forms import IngredientForm
from .measurements import US_FL_OZ, format_quantity, normalize_name, parse_quantity, resolve_unit
from .models import Ingredient, KitchenSettings, MealPlan, Recipe, ShoppingCheck
from .views import shopping_items, week_start

class QuantityTests(SimpleTestCase):
    def test_parses_user_fractions_exactly(self):
        for text, expected in [("1½", Fraction(3, 2)), ("1 1/2", Fraction(3, 2)), ("1-1/2", Fraction(3, 2)), ("½", Fraction(1, 2)), ("⅓", Fraction(1, 3)), ("1/3", Fraction(1, 3)), (".125", Fraction(1, 8)), ("2⅔", Fraction(8, 3)), ("1⁄4", Fraction(1, 4)), ("  2  1 / 3  ", Fraction(7, 3)), ("0.00001", Fraction(1, 100000))]:
            with self.subTest(text=text):
                self.assertEqual(parse_quantity(text), expected)

    def test_rejects_invalid_and_ambiguous_amounts(self):
        for value in ["0", "-1", "-½", "1/0", "1 3/2", "1/2/3", "1,500", "1 2", "NaN", "Infinity", "1e6", "", "½½", "1" * 81]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_quantity(value)

    def test_fraction_display_and_honest_decimal_rounding(self):
        for value, display in [(Fraction(3, 2), "1½"), (Fraction(1, 3), "⅓"), (Fraction(5, 8), "⅝"), (Fraction(31, 16), "1 15/16"), (Fraction(1, 7), "≈0.143"), (Fraction(1001, 1000), "1.001"), (Fraction(1, 100000), "0.00001")]:
            with self.subTest(value=value):
                self.assertEqual(format_quantity(value), display)
        self.assertEqual(format_quantity(Fraction(3, 2), kitchen=False), "1.5")

    def test_name_merging_is_conservative(self):
        self.assertEqual(normalize_name(" Cherry  TOMATOES "), normalize_name("cherry tomato"))
        self.assertNotEqual(normalize_name("red onion"), normalize_name("onion"))
        self.assertNotEqual(normalize_name("fresh basil"), normalize_name("dried basil"))

    def test_aliases_and_unit_factors_are_exact(self):
        self.assertEqual(resolve_unit("Kilograms").factor, 1000)
        self.assertEqual(resolve_unit("pounds").factor, Fraction("453.59237"))
        self.assertEqual(resolve_unit("cups").factor, 16 * resolve_unit("tablespoons").factor)
        self.assertEqual(resolve_unit("tbsp").factor, 3 * resolve_unit("tsp").factor)
        self.assertEqual(resolve_unit("fl. oz.").factor, US_FL_OZ)
        self.assertEqual(resolve_unit("T").factor, resolve_unit("tbsp").factor)
        self.assertEqual(resolve_unit("t").factor, resolve_unit("tsp").factor)
        self.assertEqual(resolve_unit("metric cups").factor, 250)
        self.assertEqual(resolve_unit("metric tbsp").factor, 15)
        self.assertEqual(resolve_unit("US cup", "metric").factor, Fraction("236.5882365"))
        self.assertEqual(resolve_unit("UK pint").factor, Fraction("568.26125"))
        self.assertNotEqual(resolve_unit("oz").dimension, resolve_unit("fl oz").dimension)

class ShoppingMeasurementsTests(TestCase):
    def setUp(self):
        self.start = week_start()
        self.profile = KitchenSettings.objects.create(pk=1, measurement_system="us")

    def plan(self, ingredients, servings=1, base_servings=1, system="us", offset=0):
        index = Recipe.objects.count()
        recipe = Recipe.objects.create(title=f"Recipe {index}", description="Test recipe", instructions="Cook.", servings=base_servings, measurement_system=system)
        for name, amount, unit in ingredients:
            Ingredient.objects.create(recipe=recipe, name=name, quantity=str(parse_quantity(amount)), unit=unit)
        MealPlan.objects.create(recipe=recipe, date=self.start + timedelta(days=offset), meal=["Breakfast", "Lunch", "Dinner"][index % 3], servings=servings)
        return recipe

    def test_grams_and_kilograms_merge_after_serving_scaling(self):
        self.plan([(" Flour ", "250", "grams")], servings=4, base_servings=2)
        self.plan([("flour", "½", "kg")])
        items, count = shopping_items(self.start)
        self.assertEqual(count, 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["total"], 1000)
        self.assertEqual((items[0]["amount_display"], items[0]["unit"]), ("1", "kg"))
        self.assertEqual(len(items[0]["recipes"]), 2)

    def test_cups_tablespoons_and_teaspoons_merge_as_kitchen_fraction(self):
        self.plan([("olive oil", "1", "cup")])
        self.plan([("Olive  oil", "8", "tablespoons")])
        items, _ = shopping_items(self.start)
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["amount_display"], items[0]["unit"]), ("1½", "cups"))
        self.assertEqual(items[0]["quantity"], Fraction(3, 2))

    def test_metric_and_us_volume_units_share_an_exact_total(self):
        self.plan([("milk", "1", "cups")])
        self.plan([("milk", "250", "ml")])
        items, _ = shopping_items(self.start)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["total"], Fraction("486.5882365"))
        self.assertEqual(items[0]["unit"], "ml")
        self.assertEqual(items[0]["amount_display"], "≈486.588")

    def test_metric_recipe_standard_is_independent_of_kitchen_preference(self):
        recipe = self.plan([("milk", "1", "cup")], system="metric")
        self.plan([("milk", "250", "ml")])
        before = shopping_items(self.start)[0][0]
        self.assertEqual(before["total"], 500)
        self.profile.measurement_system = "metric"
        self.profile.save()
        after = shopping_items(self.start)[0][0]
        self.assertEqual(after["total"], before["total"])
        self.assertEqual(after["key"], before["key"])
        recipe.refresh_from_db()
        self.assertEqual(recipe.measurement_system, "metric")

    def test_mass_volume_packages_and_unknown_units_are_separate(self):
        self.plan([("flour", "100", "g"), ("flour", "1", "cup"), ("flour", "2", "bags"), ("flour", "1", "handful")])
        items, _ = shopping_items(self.start)
        self.assertEqual(len(items), 4)
        self.assertEqual({item["dimension"] for item in items}, {"mass", "volume", "custom:bags", "custom:handful"})

    def test_singular_plural_and_count_aliases_merge(self):
        self.plan([("Egg", "1", "each")])
        self.plan([("eggs", "2", "")])
        self.plan([("EGGS", "3", "pieces")])
        items, _ = shopping_items(self.start)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], 6)
        self.assertEqual(items[0]["unit"], "")

    def test_fraction_sum_is_not_rounded_before_aggregation(self):
        self.plan([("sugar", "1/3", "cup")], servings=1, base_servings=3)
        self.plan([("sugar", "2/3", "cups")], servings=1, base_servings=3)
        items, _ = shopping_items(self.start)
        self.assertEqual(items[0]["total"], resolve_unit("cup").factor / 3)
        self.assertEqual(items[0]["exact_base"], str(resolve_unit("cup").factor / 3))
        self.assertEqual((items[0]["amount_display"], items[0]["unit"]), ("⅓", "cup"))

    def test_imperial_mass_merges_with_metric_without_confusing_fluid_ounces(self):
        self.plan([("butter", "1", "lb")])
        self.plan([("butter", "16", "oz"), ("butter", "100", "g"), ("butter", "1", "fl oz")])
        items, _ = shopping_items(self.start)
        self.assertEqual(len(items), 2)
        mass = next(item for item in items if item["dimension"] == "mass")
        self.assertEqual(mass["total"], Fraction("1007.18474"))
        volume = next(item for item in items if item["dimension"] == "volume")
        self.assertEqual(volume["total"], US_FL_OZ)

    def test_check_keys_survive_display_unit_changes(self):
        self.plan([("flour", "500", "g")])
        first = shopping_items(self.start)[0][0]
        self.client.post(reverse("shopping_check"), {"key": first["key"], "week": self.start.isoformat(), "checked": "true"})
        self.plan([("flour", "1", "kg")])
        second = shopping_items(self.start)[0][0]
        self.assertEqual(second["unit"], "kg")
        self.assertEqual(first["key"], second["key"])
        self.assertTrue(second["checked"])

    def test_scale_accepts_more_than_24_servings_and_preserves_thirds(self):
        recipe = self.plan([("milk", "1/3", "cup")], base_servings=2)
        response = self.client.get(reverse("recipe_scale", args=[recipe.pk]), {"servings": 50})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ingredients"][0]["exact"], "25/3")
        self.assertEqual(response.json()["ingredients"][0]["amount_display"], "8⅓")
        detail = self.client.get(reverse("recipe_detail", args=[recipe.pk]), {"servings": 50})
        self.assertContains(detail, "8⅓")
        self.assertEqual(detail.context["servings"], 50)
        self.client.post(reverse("plan_add"), {"recipe": recipe.pk, "date": self.start.isoformat(), "meal": "Dinner", "servings": 100})
        self.assertEqual(MealPlan.objects.get(meal="Dinner").servings, 100)

    def test_scale_rejects_invalid_servings(self):
        recipe = self.plan([("flour", "1", "g")])
        for value in [0, -1, "1.5", "abc", "", "2147483648"]:
            with self.subTest(value=value):
                response = self.client.get(reverse("recipe_scale", args=[recipe.pk]), {"servings": value})
                self.assertEqual(response.status_code, 400)

    def test_fraction_forms_and_editing_preserve_exact_value(self):
        recipe = self.plan([("milk", "1/3", "cup")])
        ingredient = recipe.ingredients.get()
        form = IngredientForm(instance=ingredient)
        self.assertEqual(form.initial["quantity"], "⅓")
        bound = IngredientForm({"quantity": "1 1/3", "unit": "cups", "name": "Milk"}, instance=ingredient)
        self.assertTrue(bound.is_valid(), bound.errors)
        bound.save()
        ingredient.refresh_from_db()
        self.assertEqual(ingredient.exact_quantity, Fraction(4, 3))

    def test_download_matches_fraction_ui(self):
        self.plan([("olive oil", "1", "cup")])
        self.plan([("olive oil", "8", "tbsp")])
        self.assertContains(self.client.get(reverse("shopping")), "1½ cups")
        self.assertContains(self.client.get(reverse("shopping_export")), "1½ cups olive oil")

    def test_migration_preserves_legacy_checks_conservatively(self):
        self.plan([("Flour", "500", "g")])
        self.plan([("flour", "½", "kg")])
        legacy_keys = [hashlib.sha256("\0".join(("flour", unit)).encode()).hexdigest() for unit in ["g", "kg"]]
        ShoppingCheck.objects.create(week=self.start, key=legacy_keys[0], checked=True)
        migrate = import_module("kitchen.migrations.0002_kitchen_measurements").preserve_shopping_checks
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertFalse(shopping_items(self.start)[0][0]["checked"])
        ShoppingCheck.objects.create(week=self.start, key=legacy_keys[1], checked=True)
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertTrue(shopping_items(self.start)[0][0]["checked"])
