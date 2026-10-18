from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from .models import Ingredient, KitchenSettings, MealPlan, Recipe, ShoppingCheck
from .views import shopping_items, week_start

class KitchenTests(TestCase):
    def setUp(self):
        self.recipe = Recipe.objects.create(title="Garden pasta", description="Fresh and lovely", vegetarian=True, prep_time=5, cook_time=15, servings=2, instructions="Boil the pasta.\nMix and serve.")
        Ingredient.objects.create(recipe=self.recipe, name="Pasta", quantity=200, unit="g")
        Ingredient.objects.create(recipe=self.recipe, name="Garlic", quantity=2, unit="cloves")
        self.other = Recipe.objects.create(title="Sunday chicken", description="Golden", prep_time=20, cook_time=50, servings=4, instructions="Roast the chicken.")
        Ingredient.objects.create(recipe=self.other, name="pasta", quantity=300, unit="G")
        Ingredient.objects.create(recipe=self.other, name="Pasta", quantity=2, unit="cups")
        self.start = week_start()

    def recipe_data(self):
        return {"title": "New family favorite", "description": "A lovely recipe", "category": "Dinner", "prep_time": "10", "cook_time": "20", "servings": "2", "measurement_system": "us", "instructions": "Prepare.\nCook.", "ingredients-TOTAL_FORMS": "1", "ingredients-INITIAL_FORMS": "0", "ingredients-MIN_NUM_FORMS": "1", "ingredients-MAX_NUM_FORMS": "100", "ingredients-0-quantity": "150", "ingredients-0-unit": "g", "ingredients-0-name": "Rice"}

    def test_all_pages_render(self):
        for path in ["/", "/favorites/", "/planner/", "/shopping/", "/settings/", "/recipes/new/", f"/recipes/{self.recipe.pk}/", f"/recipes/{self.recipe.pk}/edit/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_search_ingredients_and_filter(self):
        response = self.client.get("/", {"q": "Garlic", "filter": "vegetarian"})
        self.assertEqual(list(response.context["recipes"]), [self.recipe])
        self.assertEqual(list(self.client.get("/", {"filter": "quick"}).context["recipes"]), [self.recipe])

    def test_recipe_creation_saves_ingredients(self):
        response = self.client.post(reverse("recipe_create"), self.recipe_data())
        self.assertEqual(response.status_code, 302)
        recipe = Recipe.objects.get(title="New family favorite")
        self.assertEqual(recipe.ingredients.get().exact_quantity, Fraction(150))
        self.assertEqual(recipe.steps, ["Prepare.", "Cook."])

    def test_invalid_ingredient_does_not_partially_save_recipe(self):
        data = self.recipe_data()
        data["ingredients-0-quantity"] = "-2"
        response = self.client.post(reverse("recipe_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Recipe.objects.filter(title=data["title"]).exists())

    def test_recipe_requires_at_least_one_ingredient(self):
        data = self.recipe_data()
        data.update({"ingredients-0-quantity": "", "ingredients-0-name": "", "ingredients-0-unit": ""})
        self.assertEqual(self.client.post(reverse("recipe_create"), data).status_code, 200)
        self.assertFalse(Recipe.objects.filter(title=data["title"]).exists())

    def test_recipe_edit_updates_and_deletes_ingredients(self):
        ingredients = list(self.recipe.ingredients.all())
        data = self.recipe_data()
        data.update({"ingredients-TOTAL_FORMS": "3", "ingredients-INITIAL_FORMS": "2", "ingredients-0-id": str(ingredients[0].pk), "ingredients-1-id": str(ingredients[1].pk), "ingredients-1-quantity": "2", "ingredients-1-unit": "cloves", "ingredients-1-name": "Garlic", "ingredients-1-DELETE": "on", "ingredients-2-quantity": "1", "ingredients-2-unit": "", "ingredients-2-name": "Lemon"})
        response = self.client.post(reverse("recipe_edit", args=[self.recipe.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.title, data["title"])
        self.assertEqual(list(self.recipe.ingredients.values_list("name", flat=True)), ["Rice", "Lemon"])

    def test_invalid_image_is_rejected(self):
        data = self.recipe_data()
        data["image"] = SimpleUploadedFile("bad.jpg", b"not an image", content_type="image/jpeg")
        response = self.client.post(reverse("recipe_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("image", response.context["form"].errors)

    def test_favorites_persist_and_require_post(self):
        url = reverse("favorite_toggle", args=[self.recipe.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(response.json()["favorite"])
        self.recipe.refresh_from_db()
        self.assertTrue(self.recipe.favorite)
        self.assertEqual(list(self.client.get("/favorites/").context["recipes"]), [self.recipe])

    def test_csrf_cookie_and_post_protection(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/")
        token = client.cookies["csrftoken"].value
        url = reverse("favorite_toggle", args=[self.recipe.pk])
        self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(client.post(url, HTTP_X_CSRFTOKEN=token).status_code, 302)

    def test_add_and_replace_meal_slot(self):
        data = {"recipe": self.recipe.pk, "date": self.start.isoformat(), "meal": "Dinner", "servings": 3}
        self.assertEqual(self.client.post(reverse("plan_add"), data).status_code, 302)
        data.update(recipe=self.other.pk, servings=4)
        self.client.post(reverse("plan_add"), data)
        slot = MealPlan.objects.filter(date=self.start, meal="Dinner")
        self.assertEqual(slot.count(), 1)
        self.assertEqual(slot.get().recipe, self.other)
        self.assertEqual(slot.get().servings, 4)

    def test_invalid_meals_do_not_save(self):
        for field, value in [("servings", 0), ("servings", -2), ("servings", "1.5"), ("servings", 2147483648), ("meal", "Midnight"), ("date", "invalid"), ("date", "9999-12-31"), ("recipe", 123456)]:
            data = {"recipe": self.recipe.pk, "date": self.start.isoformat(), "meal": "Dinner", "servings": 2}
            data[field] = value
            self.client.post(reverse("plan_add"), data)
        self.assertEqual(MealPlan.objects.count(), 0)

    def test_shopping_aggregates_scales_and_keeps_units_separate(self):
        MealPlan.objects.create(recipe=self.recipe, date=self.start, meal="Dinner", servings=4)
        MealPlan.objects.create(recipe=self.other, date=self.start, meal="Lunch", servings=2)
        MealPlan.objects.create(recipe=self.recipe, date=self.start + timedelta(days=7), servings=20)
        items, count = shopping_items(self.start)
        self.assertEqual(count, 2)
        by_unit = {(i["name"].lower(), i["unit"].lower()): i for i in items}
        self.assertEqual(by_unit[("pasta", "g")]["quantity"], Decimal(550))
        self.assertEqual(by_unit[("pasta", "cup")]["quantity"], Decimal(1))
        self.assertEqual(by_unit[("garlic", "cloves")]["quantity"], Decimal(4))

    def test_shopping_checks_persist_for_correct_week(self):
        MealPlan.objects.create(recipe=self.recipe, date=self.start, servings=2)
        key = shopping_items(self.start)[0][0]["key"]
        response = self.client.post(reverse("shopping_check"), {"week": self.start.isoformat(), "key": key, "checked": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(shopping_items(self.start)[0][0]["checked"])
        self.assertFalse(ShoppingCheck.objects.filter(week=self.start + timedelta(days=7)).exists())
        self.assertEqual(self.client.post(reverse("shopping_check"), {"key": "unknown"}).status_code, 400)

    def test_export_shopping_list(self):
        MealPlan.objects.create(recipe=self.recipe, date=self.start, servings=2)
        response = self.client.get(reverse("shopping_export"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "200 g Pasta")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_remove_meal_updates_shopping_list(self):
        plan = MealPlan.objects.create(recipe=self.recipe, date=self.start, servings=2)
        self.client.post(reverse("plan_remove", args=[plan.pk]))
        self.assertEqual(shopping_items(self.start), ([], 0))

    def test_delete_recipe_cascades_meal_plan(self):
        MealPlan.objects.create(recipe=self.recipe, date=self.start, servings=2)
        self.assertEqual(self.client.get(reverse("recipe_delete", args=[self.recipe.pk])).status_code, 405)
        self.client.post(reverse("recipe_delete", args=[self.recipe.pk]))
        self.assertFalse(MealPlan.objects.exists())
        self.assertFalse(Ingredient.objects.filter(recipe_id=self.recipe.pk).exists())

    def test_settings_persist(self):
        self.client.post(reverse("settings"), {"name": "Jamie", "kitchen_name": "Sunday table", "measurement_system": "us"})
        self.assertEqual(KitchenSettings.objects.get(pk=1).name, "Jamie")
        self.assertContains(self.client.get("/"), "Jamie’s kitchen")

    def test_invalid_week_falls_back_and_week_starts_monday(self):
        for value in ["nonsense", "9999-12-31", "0001-01-01"]:
            self.assertEqual(week_start(value), self.start)
            self.assertEqual(self.client.get("/planner/", {"week": value}).status_code, 200)
        self.assertEqual(week_start("2026-10-11"), date(2026, 10, 5))
