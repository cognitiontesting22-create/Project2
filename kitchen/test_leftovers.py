from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps as django_apps
from django.contrib.messages import get_messages
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .models import (CookingRecord, Ingredient, KitchenSettings, LeftoverMeal, MealPlan, NutritionAlias,
                     NutritionFood, NutritionProfile, PantryItem, PersonalPortion, PreparedBatch, Recipe)


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


class LeftoversWorkflowTests(TestCase):
    """Whole-feature acceptance checks use the actual pages and POST routes."""

    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=MONDAY)
        clock.start()
        self.addCleanup(clock.stop)
        KitchenSettings.objects.create(pk=1, household_portions=2)
        self.recipe = Recipe.objects.create(title="Rice dinner", servings=2, instructions="Cook.")
        Ingredient.objects.create(recipe=self.recipe, name="Acceptance rice", quantity="200", unit="g")
        self.food = NutritionFood.objects.create(name="Acceptance rice", calories=130, protein=3, carbs=28, fat=0)
        NutritionAlias.objects.create(name="acceptance rice", food=self.food)
        self.stock = PantryItem.objects.create(name="Acceptance rice", quantity="2000", unit="g")
        self.use_by = date(2026, 11, 5)

    def messages(self, page):
        return [str(message) for message in get_messages(page.wsgi_request)]

    def plan(self, servings=4, **overrides):
        page = self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": MONDAY,
            "meal": "Dinner", "servings": servings, "portions_eaten": 2, **overrides}, follow=True)
        self.source = MealPlan.objects.get(date=MONDAY, meal="Dinner")
        return page

    def cook(self, plan=None, **overrides):
        url = reverse("cook_recipe", args=[self.recipe.pk])
        if plan:
            url += f"?plan={plan.pk}"
        data = {"servings": 4, "portions_eaten": 2, "storage_location": "fridge", "use_by": self.use_by,
                "cooked_on": MONDAY, "action_token": str(uuid4()), **overrides}
        return self.client.post(url, data, follow=True)

    def prepare(self, servings=4):
        self.plan(servings)
        self.cook(self.source, servings=servings)
        self.leftover = self.source.leftover_meals.get()
        self.record = CookingRecord.objects.get()
        self.batch = self.record.prepared_batch

    def eat(self, token=None):
        return self.client.post(reverse("leftover_eat", args=[self.leftover.pk]), {"action_token": str(token or uuid4())}, follow=True)

    def undo_eat(self, record):
        return self.client.post(reverse("leftover_undo", args=[record.pk]), follow=True)

    def schedule(self, day=date(2026, 11, 4), meal="Lunch"):
        return self.client.post(reverse("leftover_schedule", args=[self.source.pk]), {"date": day, "meal": meal}, follow=True)

    def snapshot(self):
        return {model.__name__: list(model.objects.order_by("pk").values()) for model in django_apps.get_app_config("kitchen").get_models()}

    def assert_batch_numbers(self, remaining, reserved, unassigned):
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.remaining_portions, self.batch.reserved_portions, self.batch.unassigned_portions),
                         (remaining, reserved, unassigned))

    def test_cooking_creates_one_batch_with_actual_portions_location_date_and_receipt(self):
        self.plan()
        page = self.cook(self.source)
        batch = PreparedBatch.objects.get()
        self.assertEqual((batch.yield_portions, batch.portions_eaten, batch.remaining_portions, batch.reserved_portions,
                          batch.storage_location, batch.use_by), (4, 2, 2, 2, "fridge", self.use_by))
        self.assertContains(page, "Made 4 · ate 2 · 2 left in the fridge · use by 5 Nov 2026")
        self.assertContains(self.client.get(reverse("planner")), "Leftovers ready")

    def test_missing_or_early_use_by_records_nothing_and_changes_no_stock(self):
        self.plan()
        for value, error in (("", "Choose a use by date for the portions you are keeping."),
                             ("2026-11-01", "Use by must be on or after the day you cooked.")):
            with self.subTest(value=value):
                before = self.snapshot()
                page = self.cook(self.source, use_by=value)
                self.assertContains(page, error)
                self.assertEqual(self.snapshot(), before)

    def test_repeated_token_and_second_record_of_same_plan_leave_one_batch(self):
        self.plan()
        token = str(uuid4())
        self.cook(self.source, action_token=token)
        for retry in (token, str(uuid4())):
            before = self.snapshot()
            self.cook(self.source, action_token=retry, servings=6, portions_eaten=1)
            self.assertEqual(self.snapshot(), before)
        self.assertEqual((PreparedBatch.objects.count(), CookingRecord.objects.count()), (1, 1))

    def test_cooking_undo_removes_batch_and_restores_stock(self):
        before = list(PantryItem.objects.values())
        self.prepare()
        self.client.post(reverse("cooking_undo", args=[self.record.pk]), follow=True)
        self.assertFalse(PreparedBatch.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, before[0]["quantity"])
        self.assertContains(self.client.get(reverse("planner")), "Expected leftovers")

    def test_eating_yield_four_reduces_remaining_and_reserved_without_touching_pantry(self):
        self.prepare()
        before = list(PantryItem.objects.values())
        self.assert_batch_numbers(2, 2, 0)
        page = self.eat()
        self.assertEqual(self.messages(page), ["Leftovers eaten. 2 portions used from Monday's dinner."])
        self.assert_batch_numbers(0, 0, 0)
        self.assertEqual(list(PantryItem.objects.values()), before)
        self.assertContains(page, "Eaten")

    def test_eating_yield_six_preserves_unassigned_portions_and_pantry(self):
        self.prepare(6)
        before = list(PantryItem.objects.values())
        self.assert_batch_numbers(4, 2, 2)
        self.eat()
        self.assert_batch_numbers(2, 0, 2)
        self.assertEqual(list(PantryItem.objects.values()), before)

    def test_repeated_eat_and_repeated_undo_change_nothing(self):
        self.prepare()
        token = str(uuid4())
        self.eat(token)
        for retry in (token, str(uuid4())):
            before = self.snapshot()
            page = self.eat(retry)
            self.assertEqual(self.messages(page), ["These leftovers were already recorded. Portions haven't been deducted twice."])
            self.assertEqual(self.snapshot(), before)
        record = LeftoverMeal.objects.get()
        self.assertContains(self.client.get(reverse("cooking_history")), "Ate leftovers · 2 portions from Monday's dinner")
        self.undo_eat(record)
        self.assert_batch_numbers(2, 2, 0)
        before = self.snapshot()
        page = self.undo_eat(record)
        self.assertEqual(self.messages(page), ["This was already undone. Portions haven't been added twice."])
        self.assertEqual(self.snapshot(), before)

    def test_cooking_undo_and_removal_are_refused_while_leftovers_are_eaten(self):
        self.prepare()
        self.eat()
        for route, pk, message in (("cooking_undo", self.record.pk, "Undo the leftover meals eaten from this batch first."),
                                  ("plan_remove", self.leftover.pk, "These leftovers are already eaten. Undo that record before changing the plan.")):
            before = self.snapshot()
            page = self.client.post(reverse(route, args=[pk]), follow=True)
            self.assertEqual(self.messages(page), [message])
            self.assertEqual(self.snapshot(), before)

    def test_rice_shopping_page_and_export_ask_for_400_grams_once(self):
        PantryItem.objects.all().delete()
        self.plan()
        page = self.client.get(reverse("shopping"))
        self.assertEqual(page.context["planned_count"], 1)
        self.assertEqual(len(page.context["items"]), 1)
        self.assertContains(page, "400 g")
        export = self.client.get(reverse("shopping_export"))
        self.assertEqual(export.content.decode(), "Savor shopping list · 02 November 2026\n\n[ ] 400 g Acceptance rice")

    def test_snapshot_keeps_130_after_food_edit_to_200_in_card_and_personal_total(self):
        self.prepare()
        person = NutritionProfile.objects.create(name="Alex", calories=2000)
        self.client.post(reverse("personal_portion_save", args=[self.leftover.pk, person.pk]), {"servings": "1"}, follow=True)
        page = self.client.post(reverse("nutrition_food_edit", args=[self.food.pk]), {
            "name": self.food.name, "aliases": "acceptance rice", "calories": 200, "protein": 3, "carbs": 28, "fat": 0}, follow=True)
        self.assertEqual(page.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.calories, 200)
        recipe_page = self.client.get(reverse("recipe_detail", args=[self.recipe.pk]))
        self.assertContains(recipe_page, 'data-nutrient="calories">200')
        page = self.client.get(reverse("planner"), {"person": person.pk})
        self.assertContains(page, "130 kcal per portion")
        tuesday = next(day for day in page.context["days"] if day["date"] == date(2026, 11, 3))
        self.assertEqual(tuesday["nutrition_cards"][0]["total"], "130")

    def test_one_spare_portion_is_available_and_fills_nothing(self):
        page = self.plan(3)
        self.assertContains(page, "1 portion available")
        self.assertEqual(MealPlan.objects.count(), 1)

    def test_occupied_tuesday_keeps_two_unassigned_and_schedule_uses_an_empty_slot(self):
        occupied = MealPlan.objects.create(recipe=self.recipe, date=date(2026, 11, 3), meal="Dinner", servings=2)
        page = self.plan()
        self.assertIn("Tuesday dinner is already planned. 2 portions are unassigned.", self.messages(page))
        self.assertContains(page, "2 portions unassigned")
        self.assertContains(page, "Schedule leftovers")
        page = self.schedule()
        self.assertEqual(self.messages(page), ["Leftovers planned for Wednesday lunch."])
        self.assertEqual(self.source.leftover_meals.get().meal, "Lunch")
        self.assertTrue(MealPlan.objects.filter(pk=occupied.pk, leftover_of__isnull=True).exists())

    def test_schedule_refuses_occupied_earlier_insufficient_and_after_use_by_with_no_writes(self):
        self.prepare(6)
        cases = [(date(2026, 11, 3), "Dinner", "That slot already has a meal. Choose an empty slot."),
                 (MONDAY, "Lunch", "Leftovers can only be planned after the meal they come from."),
                 (date(2026, 11, 6), "Lunch", "That is after this batch's use by date.")]
        for day, meal, error in cases:
            before = self.snapshot()
            page = self.schedule(day, meal)
            self.assertEqual(self.messages(page), [error])
            self.assertEqual(self.snapshot(), before)
        self.schedule()
        before = self.snapshot()
        page = self.schedule(date(2026, 11, 5))
        self.assertEqual(self.messages(page), ["Not enough unassigned portions for the household."])
        self.assertEqual(self.snapshot(), before)

    def test_removing_uncooked_source_also_removes_its_expected_leftovers(self):
        self.plan()
        page = self.client.post(reverse("plan_remove", args=[self.source.pk]), follow=True)
        self.assertEqual(self.messages(page), ["Meal removed. Its expected leftovers on Tuesday dinner were removed too."])
        self.assertFalse(MealPlan.objects.exists())

    def test_shrinking_source_shows_short_by_two_and_growing_clears_it(self):
        self.plan()
        leftover_id = self.source.leftover_meals.get().pk
        page = self.plan(2)
        self.assertContains(page, "Short by 2 portions")
        self.assertEqual(self.source.leftover_meals.get().pk, leftover_id)
        page = self.plan(4)
        self.assertNotContains(page, "Short by")
        self.assertContains(page, "Expected leftovers")

    def test_cooking_three_of_four_shows_one_available_and_eating_uses_one(self):
        self.plan()
        self.cook(self.source, servings=3)
        self.leftover = self.source.leftover_meals.get()
        page = self.client.get(reverse("planner"))
        self.assertContains(page, "Short by 1 portion · 1 of 2 portions available")
        page = self.eat()
        self.assertEqual(self.messages(page), ["Leftovers eaten. 1 portion used from Monday's dinner."])
        self.assertEqual(LeftoverMeal.objects.get().portions, 1)

    def test_prepared_shelf_empty_state_and_links_beside_pantry(self):
        page = self.client.get(reverse("prepared_shelf"))
        self.assertContains(page, "Use these first")
        self.assertContains(page, "No prepared food yet.")
        self.assertContains(page, "Cook a batch bigger than tonight's meal and the rest shows up here.")
        html = page.content.decode()
        self.assertLess(html.index('<span>My pantry</span>'), html.index('<span>Prepared food</span>'))
        self.assertLess(html.index('<span>Prepared food</span>'), html.index('<span>Cook with my pantry</span>'))
        self.assertContains(self.client.get(reverse("pantry")), reverse("prepared_shelf"), count=2)

    def test_shelf_use_these_first_orders_date_then_fridge_then_cooked_earlier(self):
        batches = []
        for use_by, location, cooked_on in [(date(2026, 11, 6), "fridge", MONDAY),
                                           (self.use_by, "freezer", date(2026, 10, 30)),
                                           (self.use_by, "fridge", MONDAY),
                                           (self.use_by, "fridge", date(2026, 11, 1)),
                                           (self.use_by, "fridge", date(2026, 11, 1))]:
            self.cook(storage_location=location, cooked_on=cooked_on, use_by=use_by)
            batches.append(PreparedBatch.objects.latest("pk"))
        page = self.client.get(reverse("prepared_shelf"))
        self.assertEqual([batch.pk for batch in page.context["batches"]], [batches[i].pk for i in (3, 4, 2, 1, 0)])
        ids = [page.content.decode().index(f'id="batch-{batches[i].pk}"') for i in (3, 4, 2, 1, 0)]
        self.assertEqual(ids, sorted(ids))

    def test_shelf_uses_stored_title_numbers_location_date_and_nutrition_snapshot(self):
        self.prepare()
        Recipe.objects.filter(pk=self.recipe.pk).update(title="Edited recipe")
        page = self.client.get(reverse("prepared_shelf"))
        for text in ("Rice dinner", "2 of 4 portions left", "2 reserved · 0 unassigned", "Fridge", "Use by 5 Nov 2026", "130 kcal per portion"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Edited recipe")

    def test_shelf_hides_zero_remaining_and_undone_cooking_records(self):
        self.cook(servings=2, portions_eaten=2, use_by="")
        zero = PreparedBatch.objects.get()
        self.cook()
        undone = CookingRecord.objects.latest("pk")
        # Defensive filtering also hides legacy/inconsistent receipts still retaining a batch.
        CookingRecord.objects.filter(pk=undone.pk).update(undone_at="2026-11-02T12:00:00Z")
        page = self.client.get(reverse("prepared_shelf"))
        self.assertEqual(list(page.context["batches"]), [])
        self.assertNotContains(page, f'id="batch-{zero.pk}"')
        self.assertContains(page, "No prepared food yet.")

    def test_shelf_labels_past_use_by_using_the_patchable_local_day(self):
        self.prepare()
        with patch("django.utils.timezone.localdate", return_value=date(2026, 11, 6)):
            page = self.client.get(reverse("prepared_shelf"))
            self.assertContains(page, "Past use by")
            self.assertEqual(len(page.context["batches"]), 1)
        with patch("django.utils.timezone.localdate", return_value=self.use_by):
            self.assertNotContains(self.client.get(reverse("prepared_shelf")), "Past use by")

    def test_freeze_and_unfreeze_change_only_location_and_explicit_date(self):
        self.prepare()
        for route, day, location, message in (("prepared_freeze", "2026-12-03", "freezer", "Moved to the freezer. Use by 3 Dec 2026."),
                                               ("prepared_unfreeze", "2026-11-06", "fridge", "Moved to the fridge. Use by 6 Nov 2026.")):
            before = self.snapshot()
            page = self.client.post(reverse(route, args=[self.batch.pk]), {"use_by": day}, follow=True)
            self.assertEqual(self.messages(page), [message])
            self.batch.refresh_from_db()
            for row in before["PreparedBatch"]:
                if row["id"] == self.batch.pk:
                    row.update(storage_location=location, use_by=date.fromisoformat(day))
            self.assertEqual(self.snapshot(), before)
            self.assertContains(page, "Unfreeze" if location == "freezer" else "Freeze")

    def test_freeze_and_unfreeze_refuse_missing_unreadable_and_already_there_dates(self):
        self.prepare()
        for route, location in (("prepared_freeze", "freezer"), ("prepared_unfreeze", "fridge")):
            for value in (None, "", "not a date", "2026-02-30"):
                before = self.snapshot()
                page = self.client.post(reverse(route, args=[self.batch.pk]), {} if value is None else {"use_by": value}, follow=True)
                self.assertEqual(self.messages(page), ["Choose a new use by date."])
                self.assertEqual(self.snapshot(), before)
            self.client.post(reverse(route, args=[self.batch.pk]), {"use_by": "2026-12-03"}, follow=True)
            before = self.snapshot()
            page = self.client.post(reverse(route, args=[self.batch.pk]), {"use_by": "2026-12-04"}, follow=True)
            self.assertEqual(self.messages(page), [f"This batch is already in the {location}."])
            self.assertEqual(self.snapshot(), before)

    def test_new_storage_date_flags_planner_conflicts_without_moving_leftovers_or_blocking_eating(self):
        self.prepare()
        self.client.post(reverse("prepared_freeze", args=[self.batch.pk]), {"use_by": "2026-12-03"}, follow=True)
        self.client.post(reverse("prepared_unfreeze", args=[self.batch.pk]), {"use_by": "2026-11-01"}, follow=True)
        self.assertContains(self.client.get(reverse("prepared_shelf")), "Past use by")
        page = self.client.get(reverse("planner"))
        self.assertContains(page, "After use by")
        self.leftover.refresh_from_db()
        self.assertEqual((self.leftover.date, self.leftover.meal), (date(2026, 11, 3), "Dinner"))
        self.assertContains(self.eat(), "Leftovers eaten.")

    def test_shelf_snapshot_shows_partial_or_missing_values_without_recalculation(self):
        Ingredient.objects.create(recipe=self.recipe, name="Unknown garnish", quantity="10", unit="g")
        self.cook()
        page = self.client.get(reverse("prepared_shelf"))
        self.assertContains(page, "130 kcal per portion · Partial estimate")
        self.client.post(reverse("nutrition_food_delete", args=[self.food.pk]), follow=True)
        self.cook()
        page = self.client.get(reverse("prepared_shelf"))
        self.assertContains(page, "130 kcal per portion · Partial estimate")
        self.assertContains(page, "— kcal per portion · Partial estimate")

    def test_every_new_post_route_refuses_get_and_requires_csrf(self):
        self.prepare(6)
        self.eat()
        eaten = LeftoverMeal.objects.get()
        routes = [("leftover_eat", self.leftover.pk, {"action_token": str(uuid4())}),
                  ("leftover_undo", eaten.pk, {}),
                  ("leftover_schedule", self.source.pk, {"date": "2026-11-04", "meal": "Lunch"}),
                  ("prepared_freeze", self.batch.pk, {"use_by": "2026-12-03"}),
                  ("prepared_unfreeze", self.batch.pk, {"use_by": "2026-11-06"})]
        secured = Client(enforce_csrf_checks=True)
        for name, pk, data in routes:
            with self.subTest(route=name):
                before = self.snapshot()
                url = reverse(name, args=[pk])
                self.assertEqual(self.client.get(url).status_code, 405)
                self.assertEqual(secured.post(url, data).status_code, 403)
                self.assertEqual(self.snapshot(), before)


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
