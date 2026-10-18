from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .models import CookingRecord, KitchenSettings, MealPlan, NutritionProfile, PersonalPortion, Recipe


MONDAY = date(2026, 11, 2)


class LeftoversTests(TestCase):
    def setUp(self):
        self.clock = patch("kitchen.views.timezone.localdate", return_value=MONDAY)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.settings = KitchenSettings.objects.create(pk=1)
        self.recipe = Recipe.objects.create(title="Monday pasta", servings=4, instructions="Cook.")
        self.other = Recipe.objects.create(title="Fresh soup", servings=2, instructions="Simmer.")

    def plan(self, **overrides):
        data = {"recipe": self.recipe.pk, "date": MONDAY.isoformat(), "meal": "Dinner", "servings": 4, "portions_eaten": ""}
        return self.client.post(reverse("plan_add"), {**data, **overrides}, follow=True)

    def messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def source(self):
        return MealPlan.objects.get(date=MONDAY, meal="Dinner")

    def test_settings_default_and_valid_boundaries_save_in_the_original_form(self):
        self.assertEqual(self.settings.household_portions, 2)
        page = self.client.get(reverse("settings"))
        self.assertContains(page, 'name="household_portions"')
        self.assertContains(page, "Household portions")
        for value in (1, 7, 20):
            with self.subTest(value=value):
                response = self.client.post(reverse("settings"), {"name": "Sam", "kitchen_name": "Our kitchen", "measurement_system": "us", "household_portions": value})
                self.assertRedirects(response, reverse("settings"))
                self.settings.refresh_from_db()
                self.assertEqual((self.settings.name, self.settings.kitchen_name, self.settings.household_portions), ("Sam", "Our kitchen", value))

    def test_invalid_household_portions_preserve_the_saved_value(self):
        for value in (0, -1, 21, "2.5", "a", "", "NaN"):
            with self.subTest(value=value):
                response = self.client.post(reverse("settings"), {"name": "Sam", "kitchen_name": "Kitchen", "measurement_system": "us", "household_portions": value})
                self.assertContains(response, "Household portions must be a whole number from 1 to 20.")
                self.settings.refresh_from_db()
                self.assertEqual(self.settings.household_portions, 2)

    def test_monday_four_servings_creates_tuesday_card_and_exact_messages(self):
        response = self.plan()
        source = self.source()
        leftover = MealPlan.objects.get(leftover_of=source)
        self.assertEqual((source.servings, source.portions_eaten), (4, 2))
        self.assertEqual((leftover.date, leftover.meal, leftover.recipe_id, leftover.servings, leftover.portions_eaten),
                         (date(2026, 11, 3), "Dinner", self.recipe.pk, 2, 2))
        self.assertEqual(self.messages(response), ["Monday pasta added to Monday dinner.", "Leftovers planned for Tuesday dinner."])
        page = self.client.get(reverse("planner"))
        self.assertContains(page, "Leftovers from Monday's dinner · 2 portions")
        self.assertContains(page, "Expected leftovers")
        self.assertContains(page, "Eating 2 · 2 for leftovers")
        self.assertContains(page, f'id="plan-{source.pk}"')
        self.assertContains(page, f'id="plan-{leftover.pk}"')
        self.assertContains(page, f'href="#plan-{source.pk}"')
        self.assertContains(page, f'href="{reverse("cook_recipe", args=[self.recipe.pk])}?plan={source.pk}"', count=1)
        self.assertNotContains(page, f'?plan={leftover.pk}"')

    def test_six_servings_only_fills_tuesday_and_does_not_chain(self):
        self.plan(servings=6)
        source = self.source()
        self.assertEqual(source.spare_portions, 4)
        self.assertEqual(source.leftover_meals.get().servings, 2)
        self.assertEqual(MealPlan.objects.count(), 2)
        self.assertFalse(MealPlan.objects.filter(date=MONDAY + timedelta(days=2)).exists())

    def test_two_servings_has_no_leftover_and_just_eating_line(self):
        self.plan(servings=2)
        self.assertEqual(MealPlan.objects.count(), 1)
        page = self.client.get(reverse("planner"))
        self.assertContains(page, "Eating 2")
        self.assertNotContains(page, "for leftovers")
        self.assertNotContains(page, "Expected leftovers")

    def test_default_eating_is_capped_at_the_batch_size(self):
        self.plan(servings=1)
        self.assertEqual((self.source().servings, self.source().portions_eaten), (1, 1))
        self.assertEqual(MealPlan.objects.count(), 1)

    def test_explicit_eating_and_household_size_control_the_threshold(self):
        self.settings.household_portions = 3
        self.settings.save()
        self.plan(servings=5, portions_eaten=3)
        self.assertEqual(MealPlan.objects.count(), 1)
        self.plan(servings=5, portions_eaten=2)
        self.assertEqual(self.source().portions_eaten, 2)
        leftover = self.source().leftover_meals.get()
        self.assertEqual((leftover.servings, leftover.portions_eaten), (3, 3))

    def test_invalid_eating_adds_nothing_and_does_not_replace_an_existing_slot(self):
        for value in (0, -1, 5, "1.5", "not a number", "NaN"):
            with self.subTest(value=value):
                response = self.plan(portions_eaten=value)
                self.assertEqual(self.messages(response), ["Portions eaten cannot be more than the servings you are cooking."])
                self.assertFalse(MealPlan.objects.exists())
        self.plan(servings=2)
        self.plan(servings=4, portions_eaten=5, recipe=self.other.pk)
        source = self.source()
        self.assertEqual((source.recipe_id, source.servings, source.portions_eaten), (self.recipe.pk, 2, 2))

    def test_repeat_save_keeps_the_same_leftover_and_personal_portions(self):
        self.plan()
        source = self.source()
        leftover = source.leftover_meals.get()
        person = NutritionProfile.objects.create(name="Sam")
        portion = PersonalPortion.objects.create(profile=person, plan=source, servings=Decimal("1.5"))
        before = (leftover.pk, leftover.recipe_id, leftover.servings, leftover.portions_eaten)
        response = self.plan()
        leftover.refresh_from_db()
        self.assertEqual((leftover.pk, leftover.recipe_id, leftover.servings, leftover.portions_eaten), before)
        self.assertEqual(MealPlan.objects.count(), 2)
        self.assertTrue(PersonalPortion.objects.filter(pk=portion.pk).exists())
        self.assertNotIn("Leftovers planned for Tuesday dinner.", self.messages(response))

    def test_occupied_next_slot_is_not_replaced_or_skipped(self):
        occupied = MealPlan.objects.create(recipe=self.other, date=MONDAY + timedelta(days=1), meal="Dinner", servings=2)
        self.plan(servings=6)
        occupied.refresh_from_db()
        self.assertEqual(occupied.recipe_id, self.other.pk)
        self.assertIsNone(occupied.leftover_of_id)
        self.assertEqual(MealPlan.objects.count(), 2)
        self.assertFalse(MealPlan.objects.filter(date=MONDAY + timedelta(days=2)).exists())

    def test_all_meal_types_fill_only_the_matching_next_day_slot(self):
        for meal in ("Breakfast", "Lunch", "Dinner"):
            self.plan(meal=meal)
            leftover = MealPlan.objects.get(date=MONDAY + timedelta(days=1), meal=meal)
            self.assertEqual(leftover.leftover_of.meal, meal)
        self.assertEqual(MealPlan.objects.count(), 6)

    def test_sunday_fills_monday_across_the_week_boundary_and_links_back(self):
        sunday = date(2026, 11, 8)
        self.plan(date=sunday.isoformat())
        source = MealPlan.objects.get(date=sunday)
        leftover = source.leftover_meals.get()
        self.assertEqual(leftover.date, date(2026, 11, 9))
        page = self.client.get(reverse("planner"), {"week": "2026-11-09"})
        self.assertContains(page, "Leftovers from Sunday's dinner · 2 portions")
        self.assertContains(page, f'/planner/?week=2026-11-02#plan-{source.pk}')

    def test_removing_leftovers_releases_portions_and_keeps_the_source(self):
        self.plan()
        source = self.source()
        leftover = source.leftover_meals.get()
        response = self.client.post(reverse("plan_remove", args=[leftover.pk]))
        self.assertEqual(self.messages(response), ["Leftovers removed. 2 portions are spare again."])
        source.refresh_from_db()
        self.assertEqual((source.servings, source.portions_eaten), (4, 2))
        self.assertFalse(source.leftover_meals.exists())

    def test_fresh_plan_replaces_a_leftover_slot_and_can_have_its_own_leftovers(self):
        self.plan()
        old_leftover = self.source().leftover_meals.get()
        self.plan(date="2026-11-03", recipe=self.other.pk)
        fresh = MealPlan.objects.get(date=date(2026, 11, 3), meal="Dinner")
        self.assertEqual(fresh.pk, old_leftover.pk)
        self.assertIsNone(fresh.leftover_of_id)
        self.assertEqual(fresh.recipe_id, self.other.pk)
        self.assertEqual(fresh.leftover_meals.get().date, date(2026, 11, 4))
        self.assertFalse(self.source().leftover_meals.exists())

    def test_source_changes_reconcile_only_its_dependent_leftovers(self):
        self.plan()
        self.plan(recipe=self.other.pk, servings=6)
        source = self.source()
        self.assertEqual(source.leftover_meals.get().recipe_id, self.other.pk)
        self.plan(recipe=self.other.pk, servings=2)
        self.assertEqual(source.leftover_meals.get().allocated_portions, 0)

    def test_removing_an_uncooked_source_removes_its_expected_leftovers(self):
        self.plan()
        response = self.client.post(reverse("plan_remove", args=[self.source().pk]))
        self.assertEqual(self.messages(response), ["Meal removed. Its expected leftovers on Tuesday dinner were removed too."])
        self.assertFalse(MealPlan.objects.exists())

    def test_cooked_plan_protection_messages_and_rows_stay_unchanged(self):
        self.plan()
        source = self.source()
        CookingRecord.objects.create(recipe=self.recipe, plan=source, recipe_title=self.recipe.title, servings=4, cooked_on=MONDAY)
        response = self.plan(recipe=self.other.pk, servings=2)
        self.assertEqual(self.messages(response), ["This meal is already cooked. Undo its cooking record before changing the plan."])
        response = self.client.post(reverse("plan_remove", args=[source.pk]))
        self.assertEqual(self.messages(response), ["This meal is already cooked. Undo its cooking record before removing it from your plan."])
        source.refresh_from_db()
        self.assertEqual((source.recipe_id, source.servings, source.portions_eaten), (self.recipe.pk, 4, 2))
        self.assertTrue(source.leftover_meals.exists())

    def test_leftovers_cannot_be_submitted_as_cooking_batches(self):
        self.plan()
        leftover = self.source().leftover_meals.get()
        url = reverse("cook_recipe", args=[self.recipe.pk]) + f"?plan={leftover.pk}"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {}).status_code, 404)
        self.assertFalse(CookingRecord.objects.exists())

    def test_personal_fractional_portions_still_work_on_both_meal_types(self):
        self.plan()
        person = NutritionProfile.objects.create(name="Sam", protein=100)
        source = self.source()
        leftover = source.leftover_meals.get()
        for plan in (source, leftover):
            self.client.post(reverse("personal_portion_save", args=[plan.pk, person.pk]), {"servings": "1.5"})
            self.assertEqual(PersonalPortion.objects.get(plan=plan, profile=person).servings, Decimal("1.5"))
        page = self.client.get(reverse("planner"), {"person": person.pk})
        self.assertEqual(page.status_code, 200)

    def test_planner_and_recipe_dialogs_use_the_patchable_local_day(self):
        self.plan(servings=2)
        page = self.client.get(reverse("planner"))
        self.assertEqual(page.context["today"], MONDAY)
        self.assertEqual(page.context["week"], MONDAY)
        self.assertContains(page, 'name="portions_eaten"')
        self.assertContains(page, "Portions eaten at this meal")
        self.assertContains(page, 'value="2026-11-02"')
        self.assertContains(self.client.get(reverse("recipe_detail", args=[self.recipe.pk])), 'value="2026-11-02"')


class LeftoversMigrationTests(TransactionTestCase):
    def test_existing_seeded_kitchen_and_plan_rows_are_backfilled(self):
        old = ("kitchen", "0006_nutritionprofile_kitchensettings_nutrition_mode_and_more")
        new = ("kitchen", "0007_household_portions_and_leftovers")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes("kitchen")
        executor.migrate([old])
        try:
            apps = executor.loader.project_state([old]).apps
            Settings = apps.get_model("kitchen", "KitchenSettings")
            Plan = apps.get_model("kitchen", "MealPlan")
            Food = apps.get_model("kitchen", "NutritionFood")
            RecipeModel = apps.get_model("kitchen", "Recipe")
            Settings.objects.create(pk=1, name="Sam")
            recipe = RecipeModel.objects.create(title="Existing dinner", servings=4, instructions="Cook.")
            for offset, servings in enumerate((1, 2, 4, 6)):
                Plan.objects.create(recipe=recipe, date=MONDAY + timedelta(days=offset), meal="Dinner", servings=servings)
            # TransactionTestCase may run after another test flushed seed rows.
            Food.objects.get_or_create(name="Existing seed food", defaults={"calories": 100, "protein": 20, "carbs": 0, "fat": 0})
            food_count = Food.objects.count()
            executor = MigrationExecutor(connection)
            executor.migrate([new])
            apps = executor.loader.project_state([new]).apps
            self.assertEqual(apps.get_model("kitchen", "KitchenSettings").objects.get(pk=1).household_portions, 2)
            rows = apps.get_model("kitchen", "MealPlan").objects.order_by("date")
            self.assertEqual(list(rows.values_list("servings", "portions_eaten", "leftover_of_id")), [(1, 1, None), (2, 2, None), (4, 2, None), (6, 2, None)])
            self.assertEqual(apps.get_model("kitchen", "NutritionFood").objects.count(), food_count)
        finally:
            MigrationExecutor(connection).migrate(latest)
