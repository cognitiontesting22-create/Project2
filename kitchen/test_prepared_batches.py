from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .inventory import record_cooking, undo_cooking
from .models import CookingRecord, Ingredient, KitchenSettings, MealPlan, PantryItem, PreparedBatch, Recipe


COOKED_ON = date(2026, 11, 2)
USE_BY = date(2026, 11, 5)


class PreparedBatchTests(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=COOKED_ON)
        clock.start()
        self.addCleanup(clock.stop)
        self.settings = KitchenSettings.objects.create(pk=1, household_portions=2)
        self.recipe = Recipe.objects.create(title="Monday dinner", servings=4, instructions="Cook.")
        Ingredient.objects.create(recipe=self.recipe, name="Flour", quantity="400", unit="g")
        self.stock = PantryItem.objects.create(name="Flour", quantity="1000", unit="g")

    def plan(self, **overrides):
        self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": COOKED_ON, "meal": "Dinner", "servings": 4,
                                             "portions_eaten": 2, **overrides}, follow=True)
        return MealPlan.objects.get(date=overrides.get("date", COOKED_ON), meal="Dinner")

    def data(self, **overrides):
        return {"servings": 4, "portions_eaten": 2, "storage_location": "fridge", "use_by": USE_BY.isoformat(),
                "cooked_on": COOKED_ON.isoformat(), "action_token": str(uuid4()), "notes": "", **overrides}

    def cook(self, plan=None, **overrides):
        url = reverse("cook_recipe", args=[self.recipe.pk])
        if plan:
            url += f"?plan={plan.pk}"
        return self.client.post(url, self.data(**overrides), follow=True)

    def assert_untouched(self):
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 1000)
        self.assertFalse(CookingRecord.objects.exists())
        self.assertFalse(PreparedBatch.objects.exists())

    def batch_numbers(self):
        return list(PreparedBatch.objects.values("cooking_record_id", "recipe_title", "yield_portions", "portions_eaten",
                                                "remaining_portions", "storage_location", "use_by"))

    def test_form_prefills_plan_or_capped_household_and_never_invents_use_by(self):
        plan = self.plan(portions_eaten=3)
        url = reverse("cook_recipe", args=[self.recipe.pk])
        page = self.client.get(url, {"plan": plan.pk})
        self.assertEqual(page.context["form"]["portions_eaten"].value(), 3)
        self.assertEqual(page.context["form"]["storage_location"].value(), "fridge")
        self.assertIsNone(page.context["form"]["use_by"].value())
        for label in ("Portions eaten at this meal", "Store the rest in", "Use by"):
            self.assertContains(page, label)
        self.assertContains(page, 'type="date" name="use_by"')
        self.assertEqual(self.client.get(url, {"servings": 1}).context["form"]["portions_eaten"].value(), 1)
        self.assertEqual(self.client.get(url, {"servings": 6}).context["form"]["portions_eaten"].value(), 2)
        self.settings.household_portions = 5
        self.settings.save()
        self.assertEqual(self.client.get(url, {"servings": 6}).context["form"]["portions_eaten"].value(), 5)
        self.assert_untouched()

    def test_worked_example_creates_batch_and_exact_receipt_and_ready_card(self):
        plan = self.plan()
        leftover = plan.leftover_meals.get()
        self.assertContains(self.client.get(reverse("planner")), "Expected leftovers")
        page = self.cook(plan)
        record = CookingRecord.objects.get()
        batch = PreparedBatch.objects.get(cooking_record=record)
        self.assertEqual((batch.recipe_title, batch.yield_portions, batch.portions_eaten, batch.remaining_portions,
                          batch.storage_location, batch.use_by), (self.recipe.title, 4, 2, 2, "fridge", USE_BY))
        self.assertEqual(record.prepared_batch.pk, batch.pk)
        self.assertContains(page, "Made 4 · ate 2 · 2 left in the fridge · use by 5 Nov 2026")
        self.assertContains(page, "Meal recorded and pantry updated. Enjoy every bite!")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 600)
        self.assertEqual(record.deductions[0]["quantity"], "400")
        self.assertEqual(record.shortages, [])
        plan.refresh_from_db()
        self.assertEqual((plan.servings, plan.portions_eaten), (4, 2))
        planner = self.client.get(reverse("planner"))
        self.assertContains(planner, "Leftovers ready")
        self.assertNotContains(planner, "Expected leftovers")
        self.assertContains(planner, "Leftovers from Monday's dinner · 2 portions")
        self.assertNotContains(planner, f'?plan={leftover.pk}"')

    def test_actual_yield_and_eaten_update_the_plan(self):
        plan = self.plan(portions_eaten=1)
        self.cook(plan, servings=6, portions_eaten=3)
        plan.refresh_from_db()
        self.assertEqual((plan.servings, plan.portions_eaten), (6, 3))
        batch = PreparedBatch.objects.get()
        self.assertEqual((batch.yield_portions, batch.portions_eaten, batch.remaining_portions), (6, 3, 3))

    def test_recipe_without_a_plan_and_freezer_create_the_same_batch(self):
        page = self.cook(storage_location="freezer")
        record = CookingRecord.objects.get()
        self.assertIsNone(record.plan_id)
        self.assertEqual(record.prepared_batch.storage_location, "freezer")
        self.assertEqual(record.prepared_batch.remaining_portions, 2)
        self.assertContains(page, "Made 4 · ate 2 · 2 left in the freezer · use by 5 Nov 2026")

    def test_missing_or_early_use_by_refuses_every_write(self):
        for value, error in (("", "Choose a use by date for the portions you are keeping."),
                             ("2026-11-01", "Use by must be on or after the day you cooked.")):
            with self.subTest(value=value):
                page = self.cook(use_by=value)
                self.assertContains(page, error)
                self.assertContains(page, "Check the details below before recording this meal.")
                self.assert_untouched()

    def test_invalid_eaten_or_storage_refuses_every_write(self):
        for value in (-1, 5, "1.5", "NaN"):
            with self.subTest(value=value):
                self.assertContains(self.cook(portions_eaten=value), "Portions eaten cannot be more than the servings you made.")
                self.assert_untouched()
        self.cook(storage_location="pantry")
        self.assert_untouched()

    def test_invalid_use_by_does_not_mutate_the_plan(self):
        plan = self.plan()
        self.cook(plan, servings=6, portions_eaten=1, use_by="")
        plan.refresh_from_db()
        self.assertEqual((plan.servings, plan.portions_eaten), (4, 2))
        self.assertEqual(plan.leftover_meals.count(), 1)
        self.assert_untouched()

    def test_zero_eaten_keeps_the_whole_yield_and_updates_the_plan(self):
        plan = self.plan()
        self.cook(plan, portions_eaten=0)
        batch = PreparedBatch.objects.get()
        self.assertEqual((batch.portions_eaten, batch.remaining_portions), (0, 4))
        plan.refresh_from_db()
        self.assertEqual(plan.portions_eaten, 0)
        plan.full_clean()

    def test_nothing_left_still_has_a_batch_and_ignores_every_use_by_value(self):
        for value in ("", "2026-01-01", "not a date"):
            with self.subTest(value=value):
                page = self.cook(servings=2, portions_eaten=2, use_by=value)
                batch = PreparedBatch.objects.latest("pk")
                self.assertEqual((batch.yield_portions, batch.portions_eaten, batch.remaining_portions, batch.use_by), (2, 2, 0, None))
                self.assertContains(page, "Made 2 · ate 2 · nothing left")
        self.assertEqual(PreparedBatch.objects.count(), 3)
        self.assertEqual(CookingRecord.objects.count(), 3)

    def test_use_by_can_equal_cooked_on_and_need_not_be_after_today(self):
        self.cook(cooked_on="2026-10-30", use_by="2026-10-30")
        self.assertEqual(PreparedBatch.objects.get().use_by, date(2026, 10, 30))

    def test_same_action_token_keeps_the_original_record_batch_and_numbers(self):
        token = str(uuid4())
        self.cook(action_token=token)
        before = self.batch_numbers()
        page = self.cook(action_token=token, servings=6, portions_eaten=0, storage_location="freezer", use_by="2026-12-01")
        self.assertContains(page, "This meal was already recorded. Your pantry hasn’t been deducted again.")
        self.assertEqual(self.batch_numbers(), before)
        self.assertEqual(CookingRecord.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 600)

    def test_same_plan_with_different_tokens_keeps_the_original_batch(self):
        plan = self.plan()
        self.cook(plan)
        before = self.batch_numbers()
        page = self.cook(plan, servings=6, portions_eaten=0, use_by="2026-12-01")
        self.assertContains(page, "This meal was already recorded. Your pantry hasn’t been deducted again.")
        self.assertEqual(self.batch_numbers(), before)
        self.assertEqual(CookingRecord.objects.count(), 1)
        plan.refresh_from_db()
        self.assertEqual((plan.servings, plan.portions_eaten), (4, 2))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 600)

    def test_undo_removes_batch_restores_stock_once_and_keeps_receipt(self):
        plan = self.plan()
        self.cook(plan)
        record = CookingRecord.objects.get()
        deductions = record.deductions
        url = reverse("cooking_undo", args=[record.pk])
        page = self.client.post(url, follow=True)
        self.assertContains(page, "Cooking record undone. The deducted ingredients are back in your pantry.")
        self.assertFalse(PreparedBatch.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 1000)
        record.refresh_from_db()
        self.assertIsNotNone(record.undone_at)
        self.assertEqual(record.deductions, deductions)
        self.assertEqual(CookingRecord.objects.count(), 1)
        page = self.client.post(url, follow=True)
        self.assertContains(page, "This record was already undone. Stock hasn’t been added twice.")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 1000)
        self.assertContains(self.client.get(reverse("planner")), "Expected leftovers")
        self.assertNotContains(self.client.get(reverse("planner")), "Leftovers ready")
        self.assertFalse(self.client.get(reverse("cook_recipe", args=[self.recipe.pk]), {"plan": plan.pk}).context["active_record"])

    def test_recording_again_after_undo_creates_a_new_batch(self):
        plan = self.plan()
        self.cook(plan)
        original = CookingRecord.objects.get()
        old_batch_pk = original.prepared_batch.pk
        self.client.post(reverse("cooking_undo", args=[original.pk]), follow=True)
        self.cook(plan)
        batch = PreparedBatch.objects.get()
        self.assertNotEqual(batch.pk, old_batch_pk)
        self.assertNotEqual(batch.cooking_record_id, original.pk)
        self.assertEqual(CookingRecord.objects.count(), 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 600)

    def test_retry_of_an_undone_token_does_not_recreate_prepared_food(self):
        token = str(uuid4())
        self.cook(action_token=token)
        record = CookingRecord.objects.get()
        undo_cooking(record)
        self.cook(action_token=token)
        self.assertFalse(PreparedBatch.objects.exists())
        self.assertEqual(CookingRecord.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 1000)

    def test_batch_title_is_a_snapshot_and_history_reads_numbers_from_batch(self):
        self.cook()
        self.recipe.title = "Renamed dinner"
        self.recipe.save()
        batch = PreparedBatch.objects.get()
        self.assertEqual(batch.recipe_title, "Monday dinner")
        # The prepared entry owns the portion figures; the receipt owns deductions.
        PreparedBatch.objects.filter(pk=batch.pk).update(remaining_portions=1)
        self.assertContains(self.client.get(reverse("cooking_history")), "Made 4 · ate 2 · 1 left in the fridge · use by 5 Nov 2026")

    def test_ready_status_also_finds_a_source_from_the_previous_week(self):
        sunday = date(2026, 11, 1)
        plan = self.plan(date=sunday)
        self.cook(plan, cooked_on=sunday.isoformat())
        page = self.client.get(reverse("planner"), {"week": COOKED_ON.isoformat()})
        self.assertContains(page, "Leftovers ready")
        self.assertContains(page, "Leftovers from Sunday's dinner · 2 portions")

    def test_service_rejects_invalid_portion_data_before_any_deduction(self):
        for values, error in (({"portions_eaten": 5}, "Portions eaten cannot be more than the servings you made."),
                              ({"portions_eaten": 2, "use_by": None}, "Choose a use by date for the portions you are keeping."),
                              ({"portions_eaten": 2, "use_by": COOKED_ON - timedelta(days=1)}, "Use by must be on or after the day you cooked.")):
            with self.subTest(values=values):
                with self.assertRaisesMessage(ValueError, error):
                    record_cooking(self.recipe, 4, COOKED_ON, uuid4(), **values)
                self.assert_untouched()

    def test_old_receipts_without_a_batch_still_render_and_undo(self):
        record = CookingRecord.objects.create(recipe=self.recipe, recipe_title="Existing receipt", servings=2, cooked_on=COOKED_ON)
        self.assertContains(self.client.get(reverse("cooking_history")), "Existing receipt")
        self.assertTrue(undo_cooking(record))
        self.assertFalse(PreparedBatch.objects.exists())
