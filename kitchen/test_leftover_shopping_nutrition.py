from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from unittest.mock import patch
from uuid import uuid4

from django.contrib.messages import get_messages
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from .inventory import eat_leftovers, record_cooking, undo_cooking, undo_leftovers
from .models import (CookingRecord, Ingredient, KitchenSettings, MealPlan, NutritionAlias, NutritionFood,
                     NutritionProfile, PantryItem, PersonalPortion, PreparedBatch, Recipe, ShoppingCheck)
from .nutrition import recipe_nutrition
from .views import shopping_key, shopping_requirements


MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)


class LeftoverShoppingNutritionTests(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=MONDAY)
        clock.start()
        self.addCleanup(clock.stop)
        KitchenSettings.objects.create(pk=1, household_portions=2, nutrition_mode="household")
        self.person = NutritionProfile.objects.create(name="Alex", calories=2000, protein=100)
        self.other_person = NutritionProfile.objects.create(name="Jamie", calories=1800, protein=90)
        self.food = NutritionFood.objects.create(name="Snapshot rice", calories=130, protein="2.345", carbs="28.125", fat="0.123")
        NutritionAlias.objects.update_or_create(name="rice", defaults={"food": self.food})
        self.recipe = Recipe.objects.create(title="Monday rice", servings=2, instructions="Cook.")
        self.ingredient = Ingredient.objects.create(recipe=self.recipe, name="Rice", quantity="200", unit="g")

    def plan(self, day=MONDAY, servings=4):
        self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": day, "meal": "Dinner", "servings": servings}, follow=True)
        self.source = MealPlan.objects.get(date=day, meal="Dinner")
        self.leftover = self.source.leftover_meals.get()

    def cook(self, servings=4):
        self.record, _ = record_cooking(self.recipe, servings, self.source.date, uuid4(), plan=self.source,
            portions_eaten=2, use_by=self.source.date + timedelta(days=3))
        self.batch = self.record.prepared_batch

    def planner(self, day=MONDAY):
        return self.client.get(reverse("planner"), {"week": day, "person": self.person.pk})

    def daily(self, page, day):
        return next(row for row in page.context["days"] if row["date"] == day)

    def leftover_card(self, page):
        return next(meal["plan"] for row in page.context["days"] for meal in row["meals"]
                    if meal["plan"] and meal["plan"].pk == self.leftover.pk)

    def assign(self, plan, person=None, portions=1):
        return self.client.post(reverse("personal_portion_save", args=[plan.pk, (person or self.person).pk]),
                                {"servings": portions}, follow=True)

    def edit_food(self, calories=200):
        response = self.client.post(reverse("nutrition_food_edit", args=[self.food.pk]), {
            "name": self.food.name, "aliases": "rice", "calories": calories, "protein": "20", "carbs": "40", "fat": "1",
            "description": "", "cup_grams": "", "item_grams": "", "slice_grams": "", "clove_grams": "", "can_grams": "",
            "portion_notes": "", "source_label": "", "source_url": ""})
        self.assertEqual(response.status_code, 302)

    def test_rice_example_is_one_400_gram_row_on_page_and_export(self):
        self.plan()
        page = self.client.get(reverse("shopping"), {"week": MONDAY})
        self.assertEqual(page.context["planned_count"], 1)
        self.assertEqual(len(page.context["items"]), 1)
        item = page.context["items"][0]
        self.assertEqual((item["total"], item["missing"], item["amount_display"], item["unit"], item["recipes"]),
                         (400, 400, "400", "g", [self.recipe.title]))
        self.assertContains(page, 'class="shopping-item ', count=1)
        self.assertContains(page, "400 g")
        self.assertContains(page, "Ingredients from your 1 uncooked meal")
        export = self.client.get(reverse("shopping_export"), {"week": MONDAY})
        self.assertEqual(export.content.decode(), "Savor shopping list · 02 November 2026\n\n[ ] 400 g Rice")
        self.assertEqual(export["Content-Disposition"], 'attachment; filename="savor-shopping-2026-11-02.txt"')

    def test_leftover_only_week_has_no_items_titles_count_or_export_rows(self):
        self.plan(day=MONDAY + timedelta(days=6))
        next_week = MONDAY + timedelta(days=7)
        self.assertEqual(shopping_requirements(next_week), ([], 0))
        page = self.client.get(reverse("shopping"), {"week": next_week})
        self.assertEqual((page.context["planned_count"], page.context["items"], page.context["covered"]), (0, [], []))
        self.assertNotContains(page, self.recipe.title)
        self.assertEqual(self.client.get(reverse("shopping_export"), {"week": next_week}).content.decode(),
                         "Savor shopping list · 09 November 2026\n")

    def test_cooking_and_undo_exclude_leftovers_throughout(self):
        self.plan()
        self.cook()
        self.assertEqual(shopping_requirements(MONDAY), ([], 0))
        self.assertEqual(self.client.post(reverse("shopping_check"), {
            "week": MONDAY, "key": shopping_key("Rice", "mass"), "checked": "true"}).status_code, 400)
        undo_cooking(self.record)
        items, count = shopping_requirements(MONDAY)
        self.assertEqual((count, len(items), items[0]["total"]), (1, 1, 400))

    def test_leftover_recipe_titles_do_not_leak_into_other_cooking_requirements(self):
        self.plan(day=MONDAY + timedelta(days=6))
        other = Recipe.objects.create(title="Fresh rice", servings=2, instructions="Cook.")
        Ingredient.objects.create(recipe=other, name="Rice", quantity="100", unit="g")
        next_week = MONDAY + timedelta(days=7)
        MealPlan.objects.create(recipe=other, date=next_week, meal="Lunch", servings=2)
        items, count = shopping_requirements(next_week)
        self.assertEqual((count, len(items), items[0]["total"], items[0]["recipes"]), (1, 1, 100, [other.title]))
        page = self.client.get(reverse("shopping"), {"week": next_week})
        self.assertNotContains(page, self.recipe.title)

    def test_merged_units_pantry_subtraction_checkboxes_and_restock_use_only_batches(self):
        self.plan()
        other = Recipe.objects.create(title="Another rice dinner", servings=2, instructions="Cook.")
        Ingredient.objects.create(recipe=other, name=" RICE ", quantity="1/10", unit="kg")
        MealPlan.objects.create(recipe=other, date=MONDAY, meal="Lunch", servings=2)
        PantryItem.objects.create(name="Rice", quantity="1/10", unit="kg")
        items, count = shopping_requirements(MONDAY)
        self.assertEqual((count, len(items), items[0]["total"], items[0]["available"], items[0]["missing"]), (2, 1, 500, 100, 400))
        key = items[0]["key"]
        response = self.client.post(reverse("shopping_check"), {"week": MONDAY, "key": key, "checked": "true"})
        self.assertEqual(response.json(), {"checked": True})
        self.assertTrue(shopping_requirements(MONDAY)[0][0]["checked"])
        self.assertIn("[x] 400 g", self.client.get(reverse("shopping_export"), {"week": MONDAY}).content.decode())
        self.client.post(reverse("shopping_stock"), {"week": MONDAY})
        total = sum(item.exact_quantity * (1000 if item.unit == "kg" else 1) for item in PantryItem.objects.all())
        self.assertEqual(total, 500)
        self.assertEqual(shopping_requirements(MONDAY)[0][0]["missing"], 0)
        self.assertFalse(ShoppingCheck.objects.get(week=MONDAY, key=key).checked)

    def test_stale_leftover_only_checkbox_cannot_restock_or_check_ingredients(self):
        self.plan(day=MONDAY + timedelta(days=6))
        next_week = MONDAY + timedelta(days=7)
        key = shopping_key("Rice", "mass")
        ShoppingCheck.objects.create(week=next_week, key=key, checked=True)
        response = self.client.post(reverse("shopping_check"), {"week": next_week, "key": key, "checked": "true"})
        self.assertEqual(response.status_code, 400)
        self.client.post(reverse("shopping_stock"), {"week": next_week})
        self.assertFalse(PantryItem.objects.exists())

    def test_cooked_snapshot_stays_130_while_recipe_and_ordinary_plan_move_to_200(self):
        self.plan()
        self.assign(self.leftover)
        self.assign(self.source)
        self.cook()
        self.assertEqual(self.batch.nutrition_snapshot, {"per_serving": {
            "calories": "130", "protein": "469/200", "carbs": "225/8", "fat": "123/1000"},
            "complete": True, "available": True})
        before = deepcopy(self.batch.nutrition_snapshot)
        self.edit_food()
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.nutrition_snapshot, before)
        recipe = self.client.get(reverse("recipe_detail", args=[self.recipe.pk]))
        self.assertEqual(recipe.context["nutrition"]["per_serving"]["calories"], "200")
        self.assertContains(recipe, 'data-nutrient="calories">200')
        page = self.planner()
        self.assertContains(page, "130 kcal per portion")
        self.assertTrue(self.leftover_card(page).leftover_nutrition["complete"])
        tuesday = self.daily(page, TUESDAY)
        self.assertEqual([card["total"] for card in tuesday["nutrition_cards"]], ["130", "2.3", "28.1", "0.1"])
        self.assertEqual(tuesday["nutrition_cards"][0]["difference"], "1870")
        self.assertEqual(self.daily(page, MONDAY)["nutrition_cards"][0]["total"], "200")

    def test_uncooked_leftover_card_and_totals_follow_live_food_edits(self):
        self.plan()
        self.assign(self.leftover)
        self.assertContains(self.planner(), "130 kcal per portion")
        self.edit_food()
        page = self.planner()
        self.assertContains(page, "200 kcal per portion")
        self.assertEqual(self.daily(page, TUESDAY)["nutrition_cards"][0]["total"], "200")
        self.assertFalse(PreparedBatch.objects.exists())

    def test_food_deletion_and_recipe_ingredient_or_base_yield_edits_do_not_change_snapshot(self):
        self.plan()
        self.assign(self.leftover)
        self.cook()
        before = deepcopy(self.batch.nutrition_snapshot)
        def assert_stable(live_calories):
            page = self.planner()
            self.assertContains(page, "130 kcal per portion")
            self.assertEqual(self.daily(page, TUESDAY)["nutrition_cards"][0]["total"], "130")
            self.batch.refresh_from_db()
            self.assertEqual(self.batch.nutrition_snapshot, before)
            self.assertEqual(self.client.get(reverse("recipe_detail", args=[self.recipe.pk])).context["nutrition"]["per_serving"]["calories"], live_calories)
        self.ingredient.quantity = "300"
        self.ingredient.save()
        assert_stable("195")
        Recipe.objects.filter(pk=self.recipe.pk).update(servings=4)
        assert_stable("195/2")
        self.client.post(reverse("nutrition_food_delete", args=[self.food.pk]))
        assert_stable(None)
        self.ingredient.delete()
        assert_stable(None)

    def test_cross_week_leftover_uses_source_snapshot_outside_visible_week(self):
        sunday = MONDAY + timedelta(days=6)
        next_week = MONDAY + timedelta(days=7)
        self.plan(day=sunday)
        self.cook()
        self.assign(self.leftover)
        self.edit_food()
        page = self.planner(next_week)
        self.assertContains(page, "130 kcal per portion")
        self.assertEqual(self.daily(page, next_week)["nutrition_cards"][0]["total"], "130")

    def test_fractional_values_are_stored_exactly_and_scaled_by_personal_portions(self):
        self.ingredient.quantity, self.ingredient.unit = "1/3", "kg"
        self.ingredient.save()
        self.plan()
        self.cook(servings=6)
        self.assertEqual(self.batch.yield_portions, 6)
        values = self.batch.nutrition_snapshot["per_serving"]
        self.assertEqual(values["calories"], "650/3")
        self.assertEqual(Fraction(values["protein"]), Fraction(Decimal("2.345")) * Fraction(5, 3))
        self.assign(self.leftover, portions="1.5")
        self.assign(self.leftover, self.other_person, ".5")
        self.assertEqual(self.daily(self.planner(), TUESDAY)["nutrition_cards"][0]["total"], "325")
        page = self.client.get(reverse("planner"), {"week": MONDAY, "person": self.other_person.pk})
        self.assertEqual(self.daily(page, TUESDAY)["nutrition_cards"][0]["total"], "108")
        self.assertContains(page, "217 kcal per portion")

    def test_partial_snapshot_keeps_known_values_and_incomplete_totals_after_food_is_fixed(self):
        unknown = Ingredient.objects.create(recipe=self.recipe, name="Mystery rice garnish", quantity="50", unit="g")
        self.plan()
        self.assign(self.leftover)
        self.cook()
        self.assertFalse(self.batch.nutrition_snapshot["complete"])
        self.assertEqual(self.batch.nutrition_snapshot["per_serving"]["calories"], "130")
        unknown.delete()
        page = self.planner()
        self.assertContains(page, "130 kcal per portion · Partial estimate")
        day = self.daily(page, TUESDAY)
        self.assertEqual(day["nutrition_cards"][0]["total"], "130")
        self.assertTrue(day["nutrition_incomplete"])
        self.assertIsNone(day["nutrition_cards"][0]["difference"])
        self.assertTrue(recipe_nutrition(self.recipe, 4)["complete"])

    def test_unknown_nutrients_are_missing_and_never_turn_into_zero_or_live_estimates(self):
        self.ingredient.name = "Unlisted rice"
        self.ingredient.save()
        self.plan()
        self.assign(self.leftover)
        self.cook()
        values = self.batch.nutrition_snapshot["per_serving"]
        self.assertEqual(values, dict.fromkeys(["calories", "protein", "carbs", "fat"]))
        self.assertFalse(self.batch.nutrition_snapshot["available"])
        NutritionAlias.objects.create(name="unlisted rice", food=self.food)
        page = self.planner()
        self.assertContains(page, "— kcal per portion · Partial estimate")
        self.assertEqual(self.daily(page, TUESDAY)["nutrition_cards"][0]["total"], "—")
        self.assertEqual(recipe_nutrition(self.recipe, 4)["per_serving"]["calories"], "130")

    def test_known_zero_nutrients_and_sub_one_calories_keep_existing_rounding(self):
        self.food.calories, self.food.protein, self.food.carbs, self.food.fat = 0, 0, 0, 0
        self.food.save()
        self.plan()
        self.assign(self.leftover)
        self.cook()
        self.assertEqual(self.batch.nutrition_snapshot["per_serving"], dict.fromkeys(["calories", "protein", "carbs", "fat"], "0"))
        self.assertContains(self.planner(), "0 kcal per portion")
        self.assertEqual(self.daily(self.planner(), TUESDAY)["nutrition_cards"][0]["total"], "0")
        undo_cooking(self.record)
        self.food.calories = Decimal("0.001")
        self.food.save()
        self.cook()
        self.assertEqual(self.batch.nutrition_snapshot["per_serving"]["calories"], "1/1000")
        self.assertContains(self.planner(), "&lt;1 kcal per portion")

    def test_personal_portions_keep_existing_cap_and_message_for_leftover_meals(self):
        self.plan()
        self.cook()
        self.assign(self.leftover, portions="1.5")
        page = self.assign(self.leftover, self.other_person, "0.51")
        self.assertIn("Personal portions exceed the cooking quantity. Increase the planned servings or reduce another person's portions first.",
                      [str(message) for message in get_messages(page.wsgi_request)])
        self.assertFalse(PersonalPortion.objects.filter(plan=self.leftover, profile=self.other_person).exists())
        self.assign(self.leftover, self.other_person, "0.5")
        self.assertEqual(sum(self.leftover.personal_portions.values_list("servings", flat=True)), 2)

    def test_undo_returns_to_live_nutrition_and_recooking_takes_a_fresh_snapshot(self):
        self.plan()
        self.assign(self.leftover)
        self.cook()
        first_batch = self.batch.pk
        self.edit_food()
        undo_cooking(self.record)
        self.assertFalse(PreparedBatch.objects.filter(pk=first_batch).exists())
        page = self.planner()
        self.assertContains(page, "Expected leftovers")
        self.assertContains(page, "200 kcal per portion")
        self.cook()
        self.assertNotEqual(self.batch.pk, first_batch)
        self.assertEqual(self.batch.nutrition_snapshot["per_serving"]["calories"], "200")

    def test_cooking_retries_eating_and_leftover_undo_keep_snapshot_unchanged(self):
        self.plan()
        self.cook()
        before = deepcopy(self.batch.nutrition_snapshot)
        self.edit_food()
        for token in (self.record.action_token, uuid4()):
            record, created = record_cooking(self.recipe, 6, MONDAY, token, plan=self.source, portions_eaten=0,
                                             use_by=MONDAY + timedelta(days=3))
            self.assertFalse(created)
            self.assertEqual(record.prepared_batch.nutrition_snapshot, before)
        eaten, _ = eat_leftovers(self.leftover, uuid4())
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.nutrition_snapshot, before)
        undo_leftovers(eaten)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.nutrition_snapshot, before)

    def test_recipe_page_cooking_without_plan_also_snapshots_zero_remaining_batch(self):
        record, _ = record_cooking(self.recipe, 4, MONDAY, uuid4(), portions_eaten=4)
        batch = record.prepared_batch
        self.assertEqual((batch.remaining_portions, batch.nutrition_snapshot["per_serving"]["calories"]), (0, "130"))

    def test_snapshot_failure_rolls_back_cooking_receipt_and_raw_stock_deductions(self):
        self.plan()
        PantryItem.objects.create(name="Rice", quantity="1000", unit="g")
        before = list(PantryItem.objects.values())
        with patch("kitchen.inventory.nutrition_snapshot", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.cook()
        self.assertEqual(list(PantryItem.objects.values()), before)
        self.assertFalse(CookingRecord.objects.exists())
        self.assertFalse(PreparedBatch.objects.exists())


class NutritionSnapshotMigrationTests(TransactionTestCase):
    def test_existing_batches_get_missing_snapshots_without_inventing_past_estimates(self):
        old = ("kitchen", "0009_leftover_reservations_and_meals")
        new = ("kitchen", "0010_prepared_batch_nutrition_snapshot")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes("kitchen")
        try:
            executor.migrate([old])
            apps = executor.loader.project_state([old]).apps
            receipt = apps.get_model("kitchen", "CookingRecord").objects.create(recipe_title="Old rice", servings=4)
            batch = apps.get_model("kitchen", "PreparedBatch").objects.create(cooking_record=receipt, recipe_title="Old rice",
                yield_portions=4, portions_eaten=2, remaining_portions=2, use_by=date(2026, 11, 5))
            executor = MigrationExecutor(connection)
            executor.migrate([new])
            apps = executor.loader.project_state([new]).apps
            batch = apps.get_model("kitchen", "PreparedBatch").objects.get(pk=batch.pk)
            self.assertEqual(batch.nutrition_snapshot, {"per_serving": dict.fromkeys(["calories", "protein", "carbs", "fat"]),
                                                       "complete": False, "available": False})
            self.assertEqual((batch.yield_portions, batch.remaining_portions), (4, 2))
        finally:
            MigrationExecutor(connection).migrate(latest)
