from fractions import Fraction
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from .forms import NutritionFoodForm
from .measurements import resolve_unit
from .models import Ingredient, NutritionAlias, NutritionFood, Recipe, MealPlan
from .nutrition import estimate_text, recipe_nutrition
from .views import shopping_items, week_start


class NutritionTests(TestCase):
    def setUp(self):
        self.food = NutritionFood.objects.create(name="Test flour", calories=400, protein=10, carbs=70, fat=4, cup_grams=120)
        NutritionAlias.objects.create(name="Test flour", food=self.food)
        self.recipe = Recipe.objects.create(title="A test loaf", servings=2, instructions="Bake.")
        self.ingredient = Ingredient.objects.create(recipe=self.recipe, name="Test flour", quantity="100", unit="g")

    def form_data(self, **overrides):
        return {"name": "A new food", "description": "Dry, edible ingredient", "aliases": "new ingredient", "calories": "150", "protein": "3", "carbs": "10", "fat": "2", "cup_grams": "120", "item_grams": "", "slice_grams": "", "clove_grams": "", "can_grams": "", "portion_notes": "A level cup", "source_label": "Product label", "source_url": "https://example.com/food", **overrides}

    def test_exact_per_serving_and_batch_arithmetic(self):
        result = recipe_nutrition(self.recipe, 2)
        self.assertEqual(result["per_serving"], {"calories": "200", "protein": "5", "carbs": "35", "fat": "2"})
        self.assertEqual(result["batch"], {"calories": "400", "protein": "10", "carbs": "70", "fat": "4"})
        self.assertTrue(result["complete"])
        self.assertEqual(result["estimated_count"], 0)

    def test_more_servings_scale_batch_and_keep_each_portion_constant(self):
        before = recipe_nutrition(self.recipe, 2)
        after = recipe_nutrition(self.recipe, 50, "batch")
        self.assertEqual(before["per_serving"], after["per_serving"])
        self.assertEqual(Fraction(after["batch"]["calories"]), Fraction(before["batch"]["calories"]) * 25)
        self.assertEqual(after["rows"][0]["grams"], "2500")
        self.assertEqual(after["cards"][0]["display"], "10000")

    def test_changing_recipe_base_servings_changes_per_serving_nutrition(self):
        self.recipe.servings = 4
        self.recipe.save()
        self.assertEqual(recipe_nutrition(self.recipe, 4)["per_serving"]["calories"], "100")

    def test_fractional_ingredients_have_no_intermediate_rounding(self):
        self.ingredient.quantity, self.ingredient.unit = "1/3", "kg"
        self.ingredient.save()
        result = recipe_nutrition(self.recipe, 3)
        self.assertEqual(Fraction(result["batch"]["protein"]), 50)
        self.assertEqual(Fraction(result["per_serving"]["protein"]), Fraction(50, 3))

    def test_ounce_conversion_uses_exact_grams(self):
        self.ingredient.quantity, self.ingredient.unit = "1", "oz"
        self.ingredient.save()
        result = recipe_nutrition(self.recipe, 2)
        self.assertEqual(Fraction(result["batch"]["calories"]), Fraction("28.349523125") * 4)

    def test_cup_and_spoon_weights_are_marked_as_typical(self):
        self.ingredient.quantity, self.ingredient.unit = "8", "tbsp"
        self.ingredient.save()
        result = recipe_nutrition(self.recipe, 2)
        self.assertEqual(result["batch"]["calories"], "240")
        self.assertEqual(result["estimated_count"], 1)
        self.assertIn("1 US cup = 120 g", result["rows"][0]["assumption"])

    def test_metric_cups_and_explicit_us_units_use_source_standard(self):
        self.recipe.measurement_system = "metric"
        self.recipe.save()
        self.ingredient.quantity, self.ingredient.unit = "1", "cup"
        self.ingredient.save()
        result = recipe_nutrition(self.recipe, 2)
        expected = Fraction(120) * 250 / resolve_unit("cup", "us").factor
        self.assertEqual(Fraction(result["rows"][0]["grams"]), expected)
        self.ingredient.unit = "US cup"
        self.ingredient.save()
        self.assertEqual(recipe_nutrition(self.recipe, 2)["rows"][0]["grams"], "120")

    def test_item_slice_clove_and_can_weights_are_explicit(self):
        self.food.item_grams, self.food.slice_grams, self.food.clove_grams, self.food.can_grams = 50, 30, 3, 240
        self.food.save()
        for unit, weight in [("each", 50), ("slices", 30), ("cloves", 3), ("cans", 240)]:
            with self.subTest(unit=unit):
                self.ingredient.quantity, self.ingredient.unit = "1/2", unit
                self.ingredient.save()
                result = recipe_nutrition(self.recipe, 2)
                self.assertEqual(Fraction(result["rows"][0]["grams"]), Fraction(weight, 2))
                self.assertEqual(result["estimated_count"], 1)

    def test_missing_food_is_omitted_and_partial_is_visible(self):
        Ingredient.objects.create(recipe=self.recipe, name="Unlisted mystery food", quantity="1000", unit="g")
        result = recipe_nutrition(self.recipe, 2)
        self.assertFalse(result["complete"])
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["per_serving"]["calories"], "200")
        self.assertIsNone(result["rows"][1]["grams"])
        self.assertEqual(result["rows"][1]["values"][0]["display"], "—")
        response = self.client.get(reverse("recipe_detail", args=[self.recipe.pk]))
        self.assertContains(response, "Partial estimate")
        self.assertContains(response, "Missing ingredients are not counted as zero")

    def test_unknown_portions_are_not_assumed_as_each(self):
        for unit in ["each", "can", "bag", "bunch"]:
            self.ingredient.unit = unit
            self.ingredient.save()
            result = recipe_nutrition(self.recipe, 2)
            self.assertEqual(result["missing"][0]["status"], "missing_weight")
            self.assertFalse(result["available"])
            self.assertIsNone(result["per_serving"]["calories"])
            self.assertEqual(result["cards"][0]["display"], "—")

    def test_known_zero_calorie_food_is_not_unknown(self):
        self.ingredient.name = "salt"
        self.ingredient.save()
        result = recipe_nutrition(self.recipe, 2)
        self.assertTrue(result["available"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["cards"][0]["display"], "0")

    def test_empty_recipe_has_no_estimate(self):
        self.ingredient.delete()
        result = recipe_nutrition(self.recipe, 2)
        self.assertFalse(result["complete"])
        self.assertFalse(result["available"])

    def test_source_calories_are_not_inferred_with_4_4_9(self):
        food = NutritionFood.objects.get(name="Vanilla extract")
        self.assertNotEqual(food.calories, (food.protein + food.carbs) * 4 + food.fat * 9)

    def test_small_values_do_not_display_as_zero(self):
        self.assertEqual(estimate_text(Fraction(1, 1000)), "<0.1")
        self.assertEqual(estimate_text(Fraction(1, 1000), 0), "<1")
        self.assertEqual(estimate_text(Fraction(5, 4)), "1.3")

    def test_dry_and_cooked_food_matches_are_distinct(self):
        dry = NutritionAlias.objects.get(name="rice").food
        cooked = NutritionAlias.objects.get(name="cooked rice").food
        self.assertNotEqual(dry.pk, cooked.pk)
        self.assertGreater(dry.calories, cooked.calories)
        self.assertFalse(NutritionAlias.objects.filter(name="chickpea").exists())

    def test_starter_data_is_sourced_and_covers_demo_recipes(self):
        from .demo_recipes import RECIPES
        self.assertEqual(NutritionFood.objects.filter(source_label__startswith="USDA SR Legacy").count(), 57)
        for data in RECIPES:
            recipe = Recipe.objects.create(title=data["title"], servings=data["servings"])
            for quantity, unit, name in data["ingredients"]:
                Ingredient.objects.create(recipe=recipe, name=name, quantity=str(quantity), unit=unit)
            with self.subTest(recipe=recipe.title):
                result = recipe_nutrition(recipe, recipe.servings)
                self.assertTrue(result["complete"], result["missing"])

    def test_table_update_is_used_on_next_calculation(self):
        self.food.calories = 500
        self.food.save()
        self.assertEqual(recipe_nutrition(self.recipe, 2)["per_serving"]["calories"], "250")
        self.ingredient.quantity = "200"
        self.ingredient.save()
        self.assertEqual(recipe_nutrition(self.recipe, 2)["per_serving"]["calories"], "500")

    def test_density_is_for_nutrition_only_shopping_stays_separate(self):
        Ingredient.objects.create(recipe=self.recipe, name="Test flour", quantity="1", unit="cup")
        MealPlan.objects.create(recipe=self.recipe, date=week_start(), servings=2)
        self.assertTrue(recipe_nutrition(self.recipe, 2)["complete"])
        self.assertEqual({row["dimension"] for row in shopping_items(week_start())[0]}, {"mass", "volume"})

    def test_food_table_renders_searches_and_paginates(self):
        self.assertEqual(self.client.get("/nutrition/").status_code, 200)
        result = self.client.get("/nutrition/", {"q": "plain flour"})
        self.assertEqual([food.name for food in result.context["foods"]], ["Plain wheat flour"])
        self.assertEqual(self.client.get("/nutrition/?p=bad").status_code, 200)
        self.assertContains(self.client.get("/nutrition/?q=xxxxxxunknown"), "No matching food yet")

    def test_create_food_and_aliases_from_ui_form(self):
        response = self.client.post(reverse("nutrition_food_create"), self.form_data(aliases=" New Ingredient , new ingredients, A new food"))
        self.assertEqual(response.status_code, 302)
        food = NutritionFood.objects.get(name="A new food")
        self.assertEqual(food.calories, 150)
        self.assertEqual(set(food.aliases.values_list("name", flat=True)), {"new ingredient", "new ingredients", "a new food"})

    def test_new_food_prefill_and_edit_preserve_name(self):
        self.assertContains(self.client.get("/nutrition/foods/new/?name=Missing%20food"), 'value="Missing food"')
        self.assertContains(self.client.get(reverse("nutrition_food_edit", args=[self.food.pk])), 'value="Test flour"')

    def test_alias_collisions_and_invalid_nutrients_do_not_save(self):
        for overrides in [{"aliases": "  TEST FLOUR  "}, {"name": "test flour"}, {"protein": "-1"}, {"fat": "101"}, {"calories": "-2"}, {"calories": ""}, {"cup_grams": "0"}, {"item_grams": "-1"}, {"source_url": "javascript:alert(1)"}]:
            with self.subTest(overrides=overrides):
                form = NutritionFoodForm(self.form_data(**overrides))
                self.assertFalse(form.is_valid())
        self.assertFalse(NutritionFood.objects.filter(name="A new food").exists())

    def test_edit_aliases_replaces_only_this_foods_matches(self):
        response = self.client.post(reverse("nutrition_food_edit", args=[self.food.pk]), self.form_data(name="Renamed test flour", aliases="test flour, test baking flour", calories="500"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(recipe_nutrition(self.recipe, 2)["per_serving"]["calories"], "250")
        self.assertTrue(NutritionAlias.objects.filter(name="test baking flour", food=self.food).exists())
        self.assertTrue(NutritionAlias.objects.filter(name="whole milk").exists())

    def test_recipe_scaling_endpoint_and_ssr_agree(self):
        response = self.client.get(reverse("recipe_scale", args=[self.recipe.pk]), {"servings": 4, "nutrition_basis": "batch"})
        data = response.json()
        self.assertEqual(data["nutrition"]["batch"]["calories"], "800")
        self.assertEqual(data["nutrition"]["per_serving"]["calories"], "200")
        self.assertIn('data-nutrient="calories">800', data["nutrition_html"])
        response = self.client.get(reverse("recipe_detail", args=[self.recipe.pk]), {"servings": 4, "nutrition_basis": "batch"})
        self.assertContains(response, 'data-nutrient="calories">800')
        self.assertEqual(response.context["nutrition"]["batch"], data["nutrition"]["batch"])

    def test_food_delete_creates_gap_and_requires_post_and_csrf(self):
        url = reverse("nutrition_food_delete", args=[self.food.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(client.post(reverse("nutrition_food_create"), self.form_data()).status_code, 403)
        self.client.post(url)
        self.assertFalse(recipe_nutrition(self.recipe, 2)["available"])

    def test_invalid_servings_rejected_without_changing_ingredients(self):
        for servings in [0, -1, Fraction(3, 2), 2147483648]:
            with self.assertRaises(ValueError):
                recipe_nutrition(self.recipe, servings)
        self.ingredient.refresh_from_db()
        self.assertEqual(self.ingredient.quantity, "100")
