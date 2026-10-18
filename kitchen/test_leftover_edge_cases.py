from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.contrib.messages import get_messages
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .inventory import record_cooking, undo_cooking
from .models import CookingRecord, Ingredient, KitchenSettings, LeftoverMeal, MealPlan, PantryItem, PreparedBatch, Recipe


MONDAY = date(2026, 11, 2)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)


class LeftoverEdgeCaseTests(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=MONDAY)
        clock.start()
        self.addCleanup(clock.stop)
        self.settings = KitchenSettings.objects.create(pk=1, household_portions=2)
        self.recipe = Recipe.objects.create(title="Monday dinner", servings=2, instructions="Cook.")
        Ingredient.objects.create(recipe=self.recipe, name="Rice", quantity="200", unit="g")
        PantryItem.objects.create(name="Rice", quantity="2000", unit="g")

    def plan(self, servings=4, eaten=2, **overrides):
        page = self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": MONDAY, "meal": "Dinner",
                                "servings": servings, "portions_eaten": eaten, **overrides}, follow=True)
        self.source = MealPlan.objects.get(date=MONDAY, meal="Dinner")
        return page

    def schedule(self, day=WEDNESDAY, meal="Lunch"):
        return self.client.post(reverse("leftover_schedule", args=[self.source.pk]), {"date": day, "meal": meal}, follow=True)

    def cook(self, servings=4, eaten=2, **overrides):
        page = self.client.post(reverse("cook_recipe", args=[self.recipe.pk]) + f"?plan={self.source.pk}", {
            "servings": servings, "portions_eaten": eaten, "storage_location": "fridge", "cooked_on": MONDAY,
            "use_by": MONDAY + timedelta(days=4), "action_token": str(uuid4()), **overrides}, follow=True)
        return page

    def messages(self, page):
        return [str(message) for message in get_messages(page.wsgi_request)]

    def snapshot(self):
        return {model.__name__: list(model.objects.order_by("pk").values()) for model in apps.get_app_config("kitchen").get_models()}

    def assert_numbers(self, remaining, reserved, unassigned, cooked=False):
        self.source.refresh_from_db()
        accounting = PreparedBatch.objects.get() if cooked else self.source
        self.assertEqual((accounting.remaining_portions, accounting.reserved_portions, accounting.unassigned_portions),
                         (remaining, reserved, unassigned))
        self.assertEqual(self.source.reserved_portions, reserved)
        active = set(LeftoverMeal.objects.filter(undone_at__isnull=True).values_list("plan_id", flat=True))
        allocations = sum(meal.allocated_portions for meal in self.source.leftover_meals.exclude(pk__in=active))
        self.assertEqual(allocations, reserved)

    def test_one_spare_portion_is_visible_without_any_leftover_meal(self):
        page = self.plan(3)
        self.assertEqual(self.messages(page), ["Monday dinner added to Monday dinner.",
            "1 portion available. Not enough for the household's 2 portions, so Tuesday dinner stays open."])
        self.assertContains(page, "1 portion available")
        self.assertNotContains(page, "Schedule leftovers")
        self.assertEqual(MealPlan.objects.count(), 1)
        self.assertFalse(PreparedBatch.objects.exists())
        self.assert_numbers(1, 0, 1)

    def test_two_spare_portions_do_not_feed_a_household_of_three(self):
        self.settings.household_portions = 3
        self.settings.save()
        page = self.plan(5, 3)
        self.assertIn("2 portions available. Not enough for the household's 3 portions, so Tuesday dinner stays open.", self.messages(page))
        self.assertContains(page, "2 portions available")
        self.assertEqual(MealPlan.objects.count(), 1)
        self.assert_numbers(2, 0, 2)

    def test_taken_tomorrow_is_preserved_and_explicit_wednesday_lunch_uses_its_portions(self):
        other = MealPlan.objects.create(recipe=self.recipe, date=TUESDAY, meal="Dinner", servings=2)
        page = self.plan()
        self.assertEqual(self.messages(page), ["Monday dinner added to Monday dinner.",
                                              "Tuesday dinner is already planned. 2 portions are unassigned."])
        self.assertContains(page, "2 portions unassigned")
        self.assertContains(page, "Schedule leftovers")
        self.assertEqual(MealPlan.objects.count(), 2)
        self.assert_numbers(2, 0, 2)
        page = self.schedule()
        self.assertEqual(self.messages(page), ["Leftovers planned for Wednesday lunch."])
        leftover = self.source.leftover_meals.get()
        self.assertEqual((leftover.date, leftover.meal, leftover.recipe_id, leftover.portions_eaten, leftover.allocated_portions),
                         (WEDNESDAY, "Lunch", self.recipe.pk, 2, 2))
        self.assertTrue(MealPlan.objects.filter(pk=other.pk, leftover_of__isnull=True).exists())
        self.assert_numbers(2, 2, 0)

    def test_six_serving_source_can_schedule_extra_portions_before_or_after_cooking(self):
        self.plan(6)
        self.assertContains(self.client.get(reverse("planner")), "Schedule leftovers")
        self.cook(6)
        self.assert_numbers(4, 2, 2, cooked=True)
        page = self.schedule()
        self.assertEqual(self.messages(page), ["Leftovers planned for Wednesday lunch."])
        self.assert_numbers(4, 4, 0, cooked=True)
        self.assertEqual(MealPlan.objects.count(), 3)

    def test_schedule_refusals_do_not_change_any_kitchen_row(self):
        self.plan(6)
        cases = [(TUESDAY, "Dinner", "That slot already has a meal. Choose an empty slot."),
                 (MONDAY, "Lunch", "Leftovers can only be planned after the meal they come from."),
                 (MONDAY - timedelta(days=1), "Breakfast", "Leftovers can only be planned after the meal they come from.")]
        for day, meal, error in cases:
            with self.subTest(day=day, meal=meal):
                before = self.snapshot()
                page = self.schedule(day, meal)
                self.assertEqual(self.messages(page), [error])
                self.assertEqual(self.snapshot(), before)
        self.schedule()
        before = self.snapshot()
        page = self.schedule(WEDNESDAY + timedelta(days=1), "Breakfast")
        self.assertEqual(self.messages(page), ["Not enough unassigned portions for the household."])
        self.assertEqual(self.snapshot(), before)

    def test_cooked_scheduling_respects_use_by_date_and_allows_that_date(self):
        self.plan(6)
        self.cook(6, use_by=WEDNESDAY)
        before = self.snapshot()
        page = self.schedule(WEDNESDAY + timedelta(days=1), "Dinner")
        self.assertEqual(self.messages(page), ["That is after this batch's use by date."])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.messages(self.schedule(WEDNESDAY, "Breakfast")), ["Leftovers planned for Wednesday breakfast."])
        self.assert_numbers(4, 4, 0, cooked=True)

    def test_schedule_requires_post_csrf_valid_fields_and_a_source_meal(self):
        self.plan(6)
        url = reverse("leftover_schedule", args=[self.source.pk])
        before = self.snapshot()
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, {"date": WEDNESDAY, "meal": "Lunch"}).status_code, 403)
        for data in ({}, {"date": "bad", "meal": "Lunch"}, {"date": WEDNESDAY, "meal": "Snack"}):
            self.client.post(url, data, follow=True)
        leftover = self.source.leftover_meals.get()
        self.assertEqual(self.client.post(reverse("leftover_schedule", args=[leftover.pk]), {"date": WEDNESDAY, "meal": "Lunch"}).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_source_removal_names_every_expected_slot_in_date_and_meal_order(self):
        self.plan(10)
        self.schedule(WEDNESDAY, "Dinner")
        self.schedule(WEDNESDAY, "Lunch")
        self.schedule(WEDNESDAY, "Breakfast")
        page = self.client.post(reverse("plan_remove", args=[self.source.pk]), follow=True)
        self.assertEqual(self.messages(page), ["Meal removed. Its expected leftovers on Tuesday dinner, Wednesday breakfast, Wednesday lunch, Wednesday dinner were removed too."])
        self.assertFalse(MealPlan.objects.exists())

    def test_same_recipe_source_shrink_keeps_slots_and_growth_restores_coverage(self):
        self.plan()
        leftover = self.source.leftover_meals.get()
        page = self.plan(2)
        self.assertIn("Tuesday dinner no longer has enough portions.", self.messages(page))
        self.assertContains(page, "Short by 2 portions")
        self.assertNotContains(page, "Expected leftovers")
        self.assert_numbers(0, 0, 0)
        self.assertEqual(self.source.leftover_meals.get().pk, leftover.pk)
        page = self.plan(3)
        self.assertContains(page, "Short by 1 portion")
        self.assert_numbers(1, 1, 0)
        page = self.plan(4)
        self.assertContains(page, "Expected leftovers")
        self.assertNotContains(page, "Short by")
        self.assert_numbers(2, 2, 0)
        self.assertEqual(self.source.leftover_meals.get().pk, leftover.pk)

    def test_eaten_quantity_changes_reconcile_old_slots_instead_of_deleting_them(self):
        self.plan()
        leftover = self.source.leftover_meals.get()
        page = self.plan(4, eaten=3)
        self.assertContains(page, "Short by 1 portion")
        self.assert_numbers(1, 1, 0)
        self.assertEqual(self.source.leftover_meals.get().pk, leftover.pk)
        self.plan(4, eaten=1)
        self.assert_numbers(3, 2, 1)

    def test_shrinking_many_reservations_keeps_earliest_covered_regardless_of_creation_order(self):
        self.plan(8)
        self.schedule(WEDNESDAY, "Dinner")
        self.schedule(WEDNESDAY, "Lunch")
        page = self.plan(5)
        ordered = self.source.leftover_meals.order_by("date", "meal")
        self.assertEqual({(meal.date, meal.meal): meal.allocated_portions for meal in ordered},
                         {(TUESDAY, "Dinner"): 2, (WEDNESDAY, "Lunch"): 1, (WEDNESDAY, "Dinner"): 0})
        self.assertIn("Wednesday lunch no longer has enough portions.", self.messages(page))
        self.assertIn("Wednesday dinner no longer has enough portions.", self.messages(page))
        self.assert_numbers(3, 3, 0)

    def test_recipe_replacement_removes_old_leftovers_and_reports_their_slots(self):
        self.plan(6)
        self.schedule()
        old_ids = list(self.source.leftover_meals.values_list("pk", flat=True))
        other = Recipe.objects.create(title="New dinner", servings=2, instructions="Cook.")
        page = self.plan(2, recipe=other.pk)
        self.assertIn("Its expected leftovers on Tuesday dinner, Wednesday lunch were removed too.", self.messages(page))
        self.assertFalse(MealPlan.objects.filter(pk__in=old_ids).exists())
        self.assertEqual(MealPlan.objects.count(), 1)

    def test_growth_only_runs_auto_rule_when_no_leftover_meal_exists(self):
        self.plan(2)
        self.plan(4)
        self.assertEqual(self.source.leftover_meals.count(), 1)
        self.plan(8)
        self.assertEqual(self.source.leftover_meals.count(), 1)
        self.schedule()
        self.client.post(reverse("plan_remove", args=[self.source.leftover_meals.get(date=TUESDAY).pk]), follow=True)
        self.plan(10)
        self.assertFalse(MealPlan.objects.filter(date=TUESDAY).exists())
        self.assertEqual(self.source.leftover_meals.count(), 1)

    def test_cooking_three_portions_cuts_reservation_and_eats_only_one(self):
        self.plan()
        page = self.cook(3)
        self.assertEqual(self.messages(page), ["Meal recorded and pantry updated. Enjoy every bite!", "Tuesday dinner is short by 1 portion."])
        self.assert_numbers(1, 1, 0, cooked=True)
        page = self.client.get(reverse("planner"))
        self.assertContains(page, "Short by 1 portion · 1 of 2 portions available")
        self.assertNotContains(page, "Leftovers ready")
        leftover = self.source.leftover_meals.get()
        before_stock = list(PantryItem.objects.values())
        token = str(uuid4())
        url = reverse("leftover_eat", args=[leftover.pk])
        page = self.client.post(url, {"action_token": token}, follow=True)
        self.assertEqual(self.messages(page), ["Leftovers eaten. 1 portion used from Monday's dinner."])
        self.assertEqual(LeftoverMeal.objects.get().portions, 1)
        self.assert_numbers(0, 0, 0, cooked=True)
        self.assertEqual(list(PantryItem.objects.values()), before_stock)
        for retry in (token, str(uuid4())):
            before = self.snapshot()
            self.client.post(url, {"action_token": retry}, follow=True)
            self.assertEqual(self.snapshot(), before)
        record = LeftoverMeal.objects.get()
        before = self.snapshot()
        page = self.client.post(reverse("cooking_undo", args=[CookingRecord.objects.get().pk]), follow=True)
        self.assertEqual(self.messages(page), ["Undo the leftover meals eaten from this batch first."])
        self.assertEqual(self.snapshot(), before)
        page = self.client.post(reverse("plan_remove", args=[leftover.pk]), follow=True)
        self.assertEqual(self.messages(page), ["These leftovers are already eaten. Undo that record before changing the plan."])
        self.assertEqual(self.snapshot(), before)
        page = self.client.post(reverse("leftover_undo", args=[record.pk]), follow=True)
        self.assertEqual(self.messages(page), ["Leftovers restored. 1 portion is back."])
        self.assert_numbers(1, 1, 0, cooked=True)
        before = self.snapshot()
        self.client.post(reverse("leftover_undo", args=[record.pk]), follow=True)
        self.assertEqual(self.snapshot(), before)
        undo_cooking(CookingRecord.objects.get())
        self.assert_numbers(2, 2, 0)
        self.assertContains(self.client.get(reverse("planner")), "Expected leftovers")

    def test_cooking_two_portions_keeps_empty_leftover_and_refuses_eating_without_writes(self):
        self.plan()
        page = self.cook(2, use_by="")
        self.assertIn("Tuesday dinner is short by 2 portions.", self.messages(page))
        self.assert_numbers(0, 0, 0, cooked=True)
        self.assertContains(self.client.get(reverse("planner")), "Short by 2 portions · no leftovers available")
        before = self.snapshot()
        page = self.schedule()
        self.assertEqual(self.messages(page), ["Not enough unassigned portions for the household."])
        self.assertEqual(self.snapshot(), before)
        page = self.client.post(reverse("leftover_eat", args=[self.source.leftover_meals.get().pk]), {"action_token": str(uuid4())}, follow=True)
        self.assertEqual(self.messages(page), ["There are no portions left to eat."])
        self.assertEqual(self.snapshot(), before)
        undo_cooking(CookingRecord.objects.get())
        self.assert_numbers(2, 2, 0)
        self.assertEqual(self.source.servings, 4)

    def test_cooking_shortfall_cuts_latest_dates_first_and_undo_restores_original_plan(self):
        self.plan(8)
        self.schedule(WEDNESDAY, "Dinner")
        self.schedule(WEDNESDAY, "Lunch")
        page = self.cook(5)
        self.assertEqual(self.messages(page)[1:], ["Wednesday lunch is short by 1 portion.", "Wednesday dinner is short by 2 portions."])
        self.assertEqual({meal.meal: meal.allocated_portions for meal in self.source.leftover_meals.filter(date=WEDNESDAY)}, {"Lunch": 1, "Dinner": 0})
        self.assert_numbers(3, 3, 0, cooked=True)
        undo_cooking(CookingRecord.objects.get())
        self.assertEqual(self.source.leftover_meals.filter(allocated_portions=2).count(), 3)
        self.assert_numbers(6, 6, 0)
        self.assertEqual(self.source.servings, 8)

    def test_cooking_more_preserves_reservations_and_extra_is_unassigned(self):
        self.plan()
        self.cook(6)
        self.assert_numbers(4, 2, 2, cooked=True)
        self.assertEqual(self.source.leftover_meals.get().allocated_portions, 2)
        self.schedule()
        undo_cooking(CookingRecord.objects.get())
        self.assert_numbers(4, 4, 0)
        self.assertEqual(self.source.servings, 6)

    def test_cooking_more_does_not_silently_fill_an_already_short_reservation(self):
        self.plan()
        self.plan(2)
        self.cook(6)
        self.assert_numbers(4, 0, 4, cooked=True)
        self.assertEqual(self.source.leftover_meals.get().allocated_portions, 0)

    def test_removing_or_replacing_partial_reservation_releases_only_what_was_reserved(self):
        self.plan()
        self.cook(3)
        leftover = self.source.leftover_meals.get()
        self.client.post(reverse("plan_remove", args=[leftover.pk]), follow=True)
        self.assert_numbers(1, 0, 1, cooked=True)
        undo_cooking(CookingRecord.objects.get())
        self.plan()
        self.cook(3)
        self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": TUESDAY, "meal": "Dinner", "servings": 2}, follow=True)
        self.assert_numbers(1, 0, 1, cooked=True)

    def test_failed_reconciliation_rolls_back_plan_and_batch_and_stock(self):
        self.plan()
        before = self.snapshot()
        with patch("kitchen.inventory.reconcile_leftovers", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                record_cooking(self.recipe, 3, MONDAY, uuid4(), plan=self.source, portions_eaten=2, use_by=WEDNESDAY)
        self.assertEqual(self.snapshot(), before)


class LeftoverAllocationsMigrationTests(TransactionTestCase):
    def test_existing_uneaten_reservations_get_allocations_but_eaten_meals_do_not(self):
        old = ("kitchen", "0010_prepared_batch_nutrition_snapshot")
        new = ("kitchen", "0011_leftover_allocations_and_planned_yield")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes("kitchen")
        try:
            executor.migrate([old])
            historical = executor.loader.project_state([old]).apps
            RecipeModel = historical.get_model("kitchen", "Recipe")
            Plan = historical.get_model("kitchen", "MealPlan")
            recipe = RecipeModel.objects.create(title="Old meal", servings=6)
            source = Plan.objects.create(recipe=recipe, date=MONDAY, servings=6, portions_eaten=2, reserved_portions=2)
            eaten = Plan.objects.create(recipe=recipe, date=TUESDAY, servings=2, portions_eaten=2, leftover_of=source)
            expected = Plan.objects.create(recipe=recipe, date=WEDNESDAY, servings=2, portions_eaten=2, leftover_of=source)
            record = historical.get_model("kitchen", "CookingRecord").objects.create(recipe_title=recipe.title, plan=source, servings=6)
            batch = historical.get_model("kitchen", "PreparedBatch").objects.create(cooking_record=record, recipe_title=recipe.title,
                yield_portions=6, portions_eaten=2, remaining_portions=2, reserved_portions=2, use_by=WEDNESDAY)
            historical.get_model("kitchen", "LeftoverMeal").objects.create(batch=batch, plan=eaten, portions=2)
            executor = MigrationExecutor(connection)
            executor.migrate([new])
            historical = executor.loader.project_state([new]).apps
            Plan = historical.get_model("kitchen", "MealPlan")
            self.assertEqual(Plan.objects.get(pk=eaten.pk).allocated_portions, 0)
            self.assertEqual(Plan.objects.get(pk=expected.pk).allocated_portions, 2)
            self.assertEqual(Plan.objects.get(pk=source.pk).reserved_portions, 2)
        finally:
            MigrationExecutor(connection).migrate(latest)
