from django.test import TestCase
from django.urls import reverse
from .models import KitchenSettings, NutritionProfile, PersonalPortion, Recipe, Ingredient, MealPlan, NutritionFood, NutritionAlias
from .forms import PersonalPortionForm
from datetime import date


class PersonalNutritionTests(TestCase):
    def setUp(self):
        self.settings = KitchenSettings.objects.create(pk=1, nutrition_mode="household")
        self.alex = NutritionProfile.objects.create(name="Alex", calories=2000, protein=100)
        self.jamie = NutritionProfile.objects.create(name="Jamie", protein=150)
        self.recipe = Recipe.objects.create(title="Test dinner", servings=4, instructions="Cook")
        Ingredient.objects.create(recipe=self.recipe, name="test food", quantity="400", unit="g")
        food = NutritionFood.objects.create(name="test food", calories=100, protein=20, carbs=0, fat=0)
        NutritionAlias.objects.create(name="test food", food=food)
        self.plan = MealPlan.objects.create(recipe=self.recipe, date=date.today(), meal="Dinner", servings=4)

    def daily(self, person):
        response = self.client.get(reverse("planner"), {"person": person.pk})
        self.assertEqual(response.status_code, 200)
        return next(day for day in response.context["days"] if day["date"] == date.today())

    def test_personal_portions_do_not_count_entire_batch(self):
        PersonalPortion.objects.create(profile=self.alex, plan=self.plan, servings="1.50")
        PersonalPortion.objects.create(profile=self.jamie, plan=self.plan, servings="0.50")
        self.assertEqual(self.daily(self.alex)["nutrition_cards"][1]["total"], "30.0")
        self.assertEqual(self.daily(self.jamie)["nutrition_cards"][1]["total"], "10.0")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.servings, 4)

    def test_missing_ingredients_suppress_target_comparison(self):
        PersonalPortion.objects.create(profile=self.alex, plan=self.plan, servings=1)
        Ingredient.objects.create(recipe=self.recipe, name="unknown ingredient", quantity="10", unit="g")
        day = self.daily(self.alex)
        self.assertTrue(day["nutrition_incomplete"])
        self.assertEqual(day["nutrition_cards"][1]["total"], "20.0")
        self.assertIsNone(day["nutrition_cards"][1]["difference"])

    def test_unassigned_and_zero_are_distinct(self):
        self.assertEqual(self.daily(self.alex)["unassigned"], 1)
        self.client.post(reverse("personal_portion_save", args=[self.plan.pk, self.alex.pk]), {"servings": "0"})
        self.assertEqual(self.daily(self.alex)["unassigned"], 0)
        self.assertFalse(self.daily(self.alex)["nutrition_incomplete"])

    def test_personal_mode_hides_other_people_without_deleting(self):
        self.settings.nutrition_mode = "personal"
        self.settings.save()
        response = self.client.get(reverse("planner"), {"person": self.jamie.pk})
        self.assertEqual(response.context["selected_person"], self.alex)
        self.assertEqual(NutritionProfile.objects.count(), 2)

    def test_settings_save_modes_and_goals(self):
        response = self.client.post(reverse("nutrition_profiles_save"), {
            "nutrition_mode": "personal", "people-TOTAL_FORMS": "2", "people-INITIAL_FORMS": "2",
            "people-0-id": self.alex.pk, "people-0-name": "Alex", "people-0-protein": "120",
            "people-1-id": self.jamie.pk, "people-1-name": "Jamie", "people-1-protein": "150",
        })
        self.assertEqual(response.status_code, 302)
        self.alex.refresh_from_db()
        self.settings.refresh_from_db()
        self.assertEqual(self.alex.protein, 120)
        self.assertEqual(self.settings.nutrition_mode, "personal")
        self.assertEqual(self.client.get(reverse("settings")).status_code, 200)

    def test_invalid_portions(self):
        for value in ["-1", "1001", "1.234", "NaN"]:
            self.assertFalse(PersonalPortionForm({"servings": value}).is_valid())

    def test_portions_cannot_exceed_cooking_quantity(self):
        PersonalPortion.objects.create(profile=self.jamie, plan=self.plan, servings=3)
        self.client.post(reverse("personal_portion_save", args=[self.plan.pk, self.alex.pk]), {"servings": "1.50"})
        self.assertFalse(PersonalPortion.objects.filter(profile=self.alex).exists())

    def test_changing_cooking_quantity_clears_portions(self):
        PersonalPortion.objects.create(profile=self.alex, plan=self.plan, servings=1)
        self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": date.today().isoformat(), "meal": "Dinner", "servings": 2})
        self.assertFalse(PersonalPortion.objects.filter(plan=self.plan).exists())

    def test_zero_portions_display_zero(self):
        PersonalPortion.objects.create(profile=self.alex, plan=self.plan, servings=0)
        self.assertEqual(self.daily(self.alex)["nutrition_cards"][0]["total"], "0")
