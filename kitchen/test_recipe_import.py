from fractions import Fraction
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from .models import Ingredient, KitchenSettings, MealPlan, PantryItem, Recipe
from .nutrition import recipe_nutrition
from .recipe_import import parse_ingredient, parse_recipe
from .views import shopping_items, week_start


RECIPE = """# Lemon pancakes
A little sunshine for breakfast.
Serves: 4
Prep time: 10 minutes
Cook time: 15 mins

## Ingredients
• 1½ cups plain flour
- 2 large eggs
- 1 cup milk
- ½ tsp salt

## Method
1. Whisk the ingredients together.
   Keep mixing until smooth.
2. Cook spoonfuls in a warm pan until golden.

Notes:
Serve with lemon.
"""


class RecipeParsingTests(SimpleTestCase):
    def test_full_recipe_and_wrapped_steps(self):
        draft = parse_recipe(RECIPE)
        self.assertEqual(draft["initial"]["title"], "Lemon pancakes")
        self.assertEqual(draft["initial"]["description"], "A little sunshine for breakfast.")
        self.assertEqual(draft["initial"]["servings"], 4)
        self.assertEqual(draft["initial"]["prep_time"], 10)
        self.assertEqual(draft["initial"]["cook_time"], 15)
        self.assertEqual(draft["initial"]["instructions"].splitlines(), ["Whisk the ingredients together. Keep mixing until smooth.", "Cook spoonfuls in a warm pan until golden."])
        self.assertIn("Serve with lemon.", draft["initial"]["notes"])
        self.assertIn("2 large eggs", draft["initial"]["notes"])
        self.assertEqual(len(draft["ingredients"]), 4)
        self.assertEqual(draft["ingredients"][0]["quantity"], "3/2")
        self.assertEqual(draft["ingredients"][1]["name"], "eggs")
        self.assertFalse(draft["ingredients"][1]["warning"])

    def test_fractions_decimals_and_attached_units(self):
        for text, exact, unit, name in [
            ("1 1/2 c. flour", "3/2", "cup", "flour"),
            ("1-1/2 cups flour", "3/2", "cup", "flour"),
            ("⅓ cup flour", "1/3", "cup", "flour"),
            ("1⁄3 cup flour", "1/3", "cup", "flour"),
            ("250g rice", "250", "g", "rice"),
            (".5 kilograms rice", "1/2", "kg", "rice"),
            ("2 fl. oz. milk", "2", "fl oz", "milk"),
            ("1 T olive oil", "1", "tbsp", "olive oil"),
            ("1 t olive oil", "1", "tsp", "olive oil"),
            ("2 garlic cloves, minced", "2", "clove", "garlic"),
            ("2 cloves of garlic, minced", "2", "clove", "garlic"),
        ]:
            with self.subTest(text=text):
                row = parse_ingredient(text)
                self.assertEqual((row["quantity"], row["unit"], row["name"]), (exact, unit, name))

    def test_explicit_measurement_standards(self):
        for unit in ["US cups", "metric cups", "UK pints"]:
            row = parse_ingredient(f"1 {unit} milk")
            self.assertEqual(row["unit"], unit)
            self.assertEqual(row["name"], "milk")
        self.assertEqual(parse_recipe(RECIPE, "metric")["initial"]["measurement_system"], "metric")

    def test_uncertain_amounts_are_blank_never_one(self):
        for line in ["Salt to taste", "1–2 cups milk", "1 to 2 cups milk", "1 or 2 eggs", "about 2 cups milk", "1 heaped tbsp flour", "0 g flour", "1/0 cup flour"]:
            with self.subTest(line=line):
                row = parse_ingredient(line)
                self.assertEqual(row["quantity"], "")
                self.assertTrue(row["warning"])
                self.assertEqual(row["name"], line)

    def test_package_size_is_preserved_and_flagged(self):
        draft = parse_recipe("Tomato soup\nIngredients:\n2 (400 g) cans tomatoes, drained\nMethod:\nHeat gently.")
        row = draft["ingredients"][0]
        self.assertEqual((row["quantity"], row["unit"], row["name"]), ("2", "can", "tomatoes"))
        self.assertIn("Package size", row["warning"])
        self.assertIn("2 (400 g) cans tomatoes, drained", draft["initial"]["notes"])

    def test_unknown_unit_is_visible_for_review(self):
        row = parse_ingredient("2 bags spinach")
        self.assertEqual(row["name"], "bags spinach")
        self.assertTrue(row["warning"])

    def test_dual_measures_require_review(self):
        for line in ["1 cup (120 g) flour", "1 cup or 120 g flour", "1 cup / 120 g flour"]:
            with self.subTest(line=line):
                self.assertTrue(parse_ingredient(line)["warning"])

    def test_no_headings_infers_ingredients_and_method(self):
        draft = parse_recipe("Roast chicken\nServes 2\n200g chicken\n1 tbsp olive oil\nHeat the oven.\nRoast until done.\nServe warm.")
        self.assertEqual(draft["initial"]["title"], "Roast chicken")
        self.assertEqual(len(draft["ingredients"]), 2)
        self.assertEqual(draft["initial"]["instructions"].splitlines(), ["Heat the oven.", "Roast until done.", "Serve warm."])

    def test_inline_headings_and_subsections(self):
        draft = parse_recipe("Title: Garden bowl\nIngredients: 200 g rice\nFor the sauce:\n1 tbsp olive oil\nDirections: Boil rice.\nMix and serve.")
        self.assertEqual(draft["initial"]["title"], "Garden bowl")
        self.assertEqual(len(draft["ingredients"]), 2)
        self.assertIn("Ingredient section: For the sauce:", draft["initial"]["notes"])
        self.assertEqual(draft["initial"]["instructions"], "Boil rice.\nMix and serve.")

    def test_numbered_ingredients_and_unordered_steps(self):
        draft = parse_recipe("Pasta\nIngredients\n1. 200 g pasta\n2. 1 tsp salt\nDirections\n- Boil the pasta.\n- Drain and serve.")
        self.assertEqual(draft["ingredients"][0]["quantity"], "200")
        self.assertEqual(draft["initial"]["instructions"], "Boil the pasta.\nDrain and serve.")

    def test_missing_sections_and_defaults_are_explicit(self):
        draft = parse_recipe("A handwritten memory")
        self.assertEqual(draft["ingredients"], [])
        self.assertEqual(draft["initial"]["instructions"], "")
        self.assertTrue(any("No ingredient" in warning for warning in draft["warnings"]))
        self.assertTrue(any("Servings were not found" in warning for warning in draft["warnings"]))
        self.assertEqual(draft["initial"]["prep_time"], 0)

    def test_hours_and_unrecognized_metadata_are_not_lost(self):
        draft = parse_recipe("Soup\nPrep time: 1 hour 5 minutes\nCook time: overnight\nIngredients\n1 l water\nMethod\nSimmer.")
        self.assertEqual(draft["initial"]["prep_time"], 65)
        self.assertEqual(draft["initial"]["cook_time"], 0)
        self.assertIn("Cook time: overnight", draft["initial"]["notes"])

    def test_limits_reject_instead_of_truncating(self):
        for text in ["   ", "x" * 30001, "x\n" * 501, "Recipe\nIngredients\n" + "1 g rice\n" * 101]:
            with self.subTest(length=len(text)):
                with self.assertRaises(ValueError):
                    parse_recipe(text)
        draft = parse_recipe("Recipe\nIngredients\n" + "1 g rice\n" * 100)
        self.assertEqual(len(draft["ingredients"]), 100)

    def test_large_numeric_metadata_does_not_crash(self):
        draft = parse_recipe("Soup\nServes " + "9" * 5000 + "\nCook time: " + "9" * 5000 + " minutes")
        self.assertEqual(draft["initial"]["servings"], 2)
        self.assertEqual(draft["initial"]["cook_time"], 0)


class RecipeImportFlowTests(TestCase):
    def save_data(self, response):
        form = response.context["form"]
        data = {key: form[key].value() for key in form.fields if key not in ("image",)}
        formset = response.context["formset"]
        data.update({"ingredients-TOTAL_FORMS": len(formset.forms), "ingredients-INITIAL_FORMS": 0,
                     "ingredients-MIN_NUM_FORMS": 1, "ingredients-MAX_NUM_FORMS": 100})
        for index, row in enumerate(formset):
            for field in ("quantity", "unit", "name"):
                data[f"ingredients-{index}-{field}"] = row[field].value() or ""
        data["import_source"] = response.context["import_draft"]["source"]
        return {key: value if value is not None else "" for key, value in data.items()}

    def test_entry_points_and_no_write_on_preview(self):
        self.assertContains(self.client.get("/"), reverse("recipe_import"))
        self.assertContains(self.client.get(reverse("recipe_create")), "Paste a recipe")
        self.assertContains(self.client.get(reverse("recipe_import")), "Create editable draft")
        response = self.client.post(reverse("recipe_import"), {"text": RECIPE})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Recipe.objects.count(), 0)
        self.assertEqual(Ingredient.objects.count(), 0)
        self.assertEqual(MealPlan.objects.count(), 0)
        self.assertEqual(PantryItem.objects.count(), 0)
        self.assertContains(response, "Nothing has been saved yet")
        self.assertEqual(len(response.context["formset"].forms), 4)
        self.assertEqual(response.context["formset"].forms[0]["quantity"].value(), "1½")
        self.assertContains(response, f'action="{reverse("recipe_create")}"')

    def test_save_edited_draft_exactly_and_integrate_scaling_shopping_nutrition(self):
        response = self.client.post(reverse("recipe_import"), {"text": RECIPE})
        data = self.save_data(response)
        data["title"] = "Our lemon pancakes"
        data["category"] = "Breakfast"
        response = self.client.post(reverse("recipe_create"), data)
        self.assertEqual(response.status_code, 302)
        recipe = Recipe.objects.get()
        self.assertEqual(recipe.title, "Our lemon pancakes")
        self.assertEqual(recipe.category, "Breakfast")
        self.assertEqual(recipe.ingredients.count(), 4)
        self.assertEqual(recipe.ingredients.first().exact_quantity, Fraction(3, 2))
        self.assertEqual(recipe.steps[0], "Whisk the ingredients together. Keep mixing until smooth.")
        self.assertIn("2 large eggs", recipe.notes)
        scaled = self.client.get(reverse("recipe_scale", args=[recipe.pk]), {"servings": 8}).json()
        self.assertEqual(scaled["ingredients"][0]["exact"], "3")
        self.assertTrue(recipe_nutrition(recipe, 8)["complete"])
        MealPlan.objects.create(recipe=recipe, servings=8, date=week_start())
        items, count = shopping_items(week_start())
        flour = next(item for item in items if item["name"] == "plain flour")
        self.assertEqual(flour["quantity"], Fraction(3))
        self.assertEqual(count, 1)

    def test_missing_amount_blocks_save_and_keeps_source_on_invalid_post(self):
        source = "Soup\nIngredients\nSalt to taste\nDirections\nSimmer gently."
        preview = self.client.post(reverse("recipe_import"), {"text": source})
        data = self.save_data(preview)
        response = self.client.post(reverse("recipe_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Recipe.objects.count(), 0)
        self.assertIn("quantity", response.context["formset"].forms[0].errors)
        self.assertContains(response, "See the original pasted recipe")
        data.update({"ingredients-0-name": "salt", "ingredients-0-quantity": "1/2", "ingredients-0-unit": "tsp"})
        self.assertEqual(self.client.post(reverse("recipe_create"), data).status_code, 302)

    def test_empty_recipe_draft_has_one_editable_row(self):
        response = self.client.post(reverse("recipe_import"), {"text": "Mystery soup"})
        self.assertEqual(len(response.context["formset"].forms), 1)
        data = self.save_data(response)
        response = self.client.post(reverse("recipe_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Recipe.objects.count(), 0)

    def test_one_hundred_rows_are_previewed_and_saveable(self):
        response = self.client.post(reverse("recipe_import"), {"text": "Big soup\nIngredients\n" + "1 g rice\n" * 100 + "Method\nCook rice."})
        self.assertEqual(len(response.context["formset"].forms), 100)
        self.assertEqual(self.client.post(reverse("recipe_create"), self.save_data(response)).status_code, 302)
        self.assertEqual(Recipe.objects.get().ingredients.count(), 100)

    def test_invalid_paste_stays_on_paste_form_with_no_recipe(self):
        for text in ["", "   ", "x" * 30001, "Recipe\nIngredients\n" + "1 g rice\n" * 101]:
            response = self.client.post(reverse("recipe_import"), {"text": text})
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "kitchen/recipe_import.html")
            self.assertTrue(response.context["form"].errors)
        self.assertEqual(Recipe.objects.count(), 0)

    def test_kitchen_standard_carried_into_draft(self):
        KitchenSettings.objects.create(pk=1, measurement_system="metric")
        response = self.client.post(reverse("recipe_import"), {"text": RECIPE})
        self.assertEqual(response.context["form"]["measurement_system"].value(), "metric")

    def test_html_is_displayed_as_text_in_review_and_saved_recipe(self):
        source = '<script>alert("bad")</script>\nIngredients\n1 g <img src=x onerror=alert(1)>\nMethod\n<script>alert("method")</script>'
        response = self.client.post(reverse("recipe_import"), {"text": source})
        self.assertNotContains(response, '<script>alert("bad")</script>')
        self.assertContains(response, "&lt;script&gt;")
        saved = self.client.post(reverse("recipe_create"), self.save_data(response))
        self.assertEqual(saved.status_code, 302)
        response = self.client.get(saved.url)
        self.assertNotContains(response, '<script>alert("method")</script>')
        self.assertNotContains(response, "<img src=x onerror=alert(1)>")

    def test_import_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("recipe_import"))
        self.assertEqual(client.post(reverse("recipe_import"), {"text": RECIPE}).status_code, 403)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(reverse("recipe_import"), {"text": RECIPE}, HTTP_X_CSRFTOKEN=token).status_code, 200)
