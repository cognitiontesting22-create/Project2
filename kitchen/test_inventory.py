"""Inventory behavior: exact arithmetic, real shortages, receipts and safe retries."""
from datetime import timedelta
from fractions import Fraction
from uuid import uuid4
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from .inventory import add_stock, pantry_balances, recipe_match, record_cooking, undo_cooking
from .models import CookingRecord, Ingredient, MealPlan, PantryItem, Recipe, ShoppingCheck
from .views import shopping_items, week_start


class InventoryTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.start = week_start()
        self.recipe = Recipe.objects.create(title="Milk bread", servings=2, instructions="Mix and bake.")
        Ingredient.objects.create(recipe=self.recipe, name="Flour", quantity="500", unit="g")
        Ingredient.objects.create(recipe=self.recipe, name="Milk", quantity="1", unit="cup")

    def stock(self, name, quantity, unit="", **kwargs):
        return PantryItem.objects.create(name=name, quantity=str(quantity), unit=unit, **kwargs)

    def plan(self, recipe=None, meal="Dinner", **kwargs):
        return MealPlan.objects.create(recipe=recipe or self.recipe, date=self.start, meal=meal, servings=2, **kwargs)

    def cook(self, **kwargs):
        return record_cooking(self.recipe, kwargs.pop("servings", 2), self.today, kwargs.pop("action_token", uuid4()), **kwargs)[0]

    def stock_data(self, **overrides):
        return {"name": "Flour", "quantity": "1/3", "unit": "kg", "measurement_system": "us", "location": "pantry", "expires_on": "", **overrides}

    def test_new_pages_render_and_review_is_read_only(self):
        item = self.stock("Flour", 600, "g")
        plan = self.plan()
        for url in ["/pantry/", "/cook-with-pantry/", "/cooking-history/", reverse("pantry_edit", args=[item.pk]), reverse("cook_recipe", args=[self.recipe.pk]) + f"?plan={plan.pk}"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 600)
        self.assertFalse(CookingRecord.objects.exists())

    def test_date_inputs_use_browser_native_iso_values(self):
        item = self.stock("Milk", 1, "cup", expires_on=self.today)
        response = self.client.get(reverse("pantry_edit", args=[item.pk]))
        self.assertContains(response, f'value="{self.today.isoformat()}"')
        response = self.client.get(reverse("cook_recipe", args=[self.recipe.pk]))
        self.assertContains(response, f'value="{self.today.isoformat()}"')

    def test_add_tops_up_matching_lot_edit_sets_actual_stock(self):
        self.client.post(reverse("pantry_add"), self.stock_data())
        self.client.post(reverse("pantry_add"), self.stock_data(name="  FLOUR  ", quantity="2/3", unit="kilograms"))
        item = PantryItem.objects.get()
        self.assertEqual(item.exact_quantity, 1)
        self.client.post(reverse("pantry_edit", args=[item.pk]), self.stock_data(quantity="0", unit="kg"))
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 0)
        self.assertContains(self.client.get("/pantry/?status=empty"), "Flour")

    def test_distinct_dates_locations_and_cup_standards_keep_separate_lots(self):
        data = {"name": "Milk", "quantity": "1", "unit": "cup", "measurement_system": "us", "location": "fridge", "expires_on": None}
        for overrides in [{}, {"expires_on": self.today}, {"location": "freezer"}, {"measurement_system": "metric"}]:
            add_stock({**data, **overrides})
        self.assertEqual(PantryItem.objects.count(), 4)

    def test_pantry_validation_rejects_bad_amounts_and_systems(self):
        for overrides in [{"quantity": "-1"}, {"quantity": "1/0"}, {"quantity": ""}, {"measurement_system": "unknown"}, {"location": "unknown"}, {"expires_on": "bad-date"}]:
            response = self.client.post(reverse("pantry_add"), self.stock_data(**overrides))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
            self.assertContains(response, "data-auto-open")
        self.assertFalse(PantryItem.objects.exists())

    def test_pantry_search_use_soon_and_expired(self):
        self.stock("Flour", 5, "g", expires_on=self.today + timedelta(days=2))
        self.stock("Milk", 2, "cup", expires_on=self.today - timedelta(days=1))
        self.stock("Rice", 0, "g")
        response = self.client.get("/pantry/?status=soon")
        self.assertEqual([i.name for i in response.context["items"]], ["Flour"])
        self.assertEqual(response.context["stock_count"], 1)
        self.assertEqual(response.context["empty_count"], 1)
        self.assertEqual([i.name for i in self.client.get("/pantry/?q=ilk&status=expired").context["items"]], ["Milk"])

    def test_weekly_stock_is_subtracted_once_from_combined_meals(self):
        self.plan()
        self.plan(meal="Lunch")
        self.stock(" FLOUR ", "3/4", "kg")
        self.stock("Milk", 8, "tbsp")
        items, count = shopping_items(self.start)
        self.assertEqual(count, 2)
        by_name = {i["name"]: i for i in items}
        self.assertEqual(by_name["Flour"]["quantity"], 250)
        self.assertEqual(by_name["Milk"]["quantity"], Fraction(3, 2))
        self.assertEqual(by_name["Milk"]["amount_display"], "1½")
        self.assertEqual(by_name["Milk"]["available"], by_name["Milk"]["total"] / 4)

    def test_expired_stock_not_subtracted_and_use_by_today_is_usable(self):
        self.plan()
        self.stock("Milk", 5, "cup", expires_on=self.today - timedelta(days=1))
        self.stock("Milk", "1/2", "cup", expires_on=self.today)
        milk = next(i for i in shopping_items(self.start)[0] if i["name"] == "Milk")
        self.assertEqual(milk["quantity"], Fraction(1, 2))

    def test_mass_does_not_cover_volume_and_counts_do_not_cover_packages(self):
        Ingredient.objects.create(recipe=self.recipe, name="Tomatoes", quantity="2", unit="cans")
        self.stock("Milk", 1000, "g")
        self.stock("Tomato", 10)
        match = recipe_match(self.recipe, 2)
        self.assertEqual(match["missing_count"], 3)

    def test_metric_pantry_cup_converts_to_us_recipe_exactly(self):
        item = self.stock("Milk", 1, "cup", measurement_system="metric")
        match = recipe_match(self.recipe, 2)
        milk = next(r for r in match["rows"] if r["name"] == "Milk")
        self.assertEqual(milk["missing"], 0)
        self.cook()
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 1 - Fraction("236.5882365") / 250)

    def test_fully_covered_items_stay_off_list_and_export(self):
        self.plan()
        self.stock("Flour", 1, "kg")
        response = self.client.get("/shopping/")
        self.assertEqual([i["name"] for i in response.context["covered"]], ["Flour"])
        self.assertNotContains(self.client.get("/shopping/export/"), "Flour")
        self.stock("Milk", 1, "cup")
        self.assertContains(self.client.get("/shopping/"), "Your pantry has this week covered")

    def test_discovery_ranks_fewest_missing_and_checks_selected_servings(self):
        other = Recipe.objects.create(title="Plain milk", servings=2, instructions="Warm.")
        Ingredient.objects.create(recipe=other, name="Milk", quantity="1", unit="cup")
        self.stock("Milk", 1, "cup")
        self.stock("Flour", 250, "g")
        response = self.client.get("/cook-with-pantry/?servings=2")
        self.assertEqual([r.pk for r in response.context["recipes"]], [other.pk, self.recipe.pk])
        self.assertEqual(response.context["ready_count"], 1)
        self.assertEqual(response.context["recipes"][1].pantry_match["missing_count"], 1)
        self.assertEqual(self.client.get("/cook-with-pantry/?servings=4&ready=1").context["recipes"], [])
        self.assertEqual([r.pk for r in self.client.get("/?sort=pantry").context["recipes"]], [other.pk, self.recipe.pk])

    def test_extra_ingredient_search_is_explicitly_unverified_and_does_not_save(self):
        response = self.client.get("/cook-with-pantry/", {"ingredients": "flour, MILK", "q": "bread", "ready": "1"})
        self.assertEqual(response.context["recipes"][0].pk, self.recipe.pk)
        self.assertTrue(response.context["recipes"][0].pantry_match["unverified"])
        self.assertContains(response, "check that you have enough")
        self.assertFalse(PantryItem.objects.exists())

    def test_duplicate_recipe_ingredients_share_stock_and_one_missing_entry(self):
        Ingredient.objects.create(recipe=self.recipe, name="flour", quantity="250", unit="g")
        self.stock("Flour", 500, "g")
        match = recipe_match(self.recipe, 2)
        self.assertEqual(match["total_count"], 2)
        flour = next(r for r in match["rows"] if r["name"] == "Flour")
        self.assertEqual(flour["missing"], 250)

    def test_empty_recipe_not_shown_as_ready_or_cooked(self):
        recipe = Recipe.objects.create(title="Empty")
        self.assertFalse(recipe_match(recipe, 2)["ready"])
        with self.assertRaises(ValueError):
            record_cooking(recipe, 2, self.today, uuid4())
        self.assertFalse(CookingRecord.objects.exists())

    def test_cook_uses_earliest_unexpired_lots_and_never_negative(self):
        late = self.stock("Flour", 1, "kg", expires_on=self.today + timedelta(days=20))
        early = self.stock("flour", 300, "g", expires_on=self.today)
        expired = self.stock("Flour", 500, "g", expires_on=self.today - timedelta(days=1))
        record = self.cook()
        for item in [late, early, expired]:
            item.refresh_from_db()
        self.assertEqual(early.exact_quantity, 0)
        self.assertEqual(late.exact_quantity, Fraction(4, 5))
        self.assertEqual(expired.exact_quantity, 500)
        self.assertEqual([d["pantry_item_id"] for d in record.deductions], [early.pk, late.pk])
        self.assertEqual(record.shortages[0]["name"], "Milk")

    def test_cook_partial_stock_receipt_lists_untracked_amount(self):
        flour = self.stock("Flour", 200, "g")
        record = self.cook()
        flour.refresh_from_db()
        self.assertEqual(flour.exact_quantity, 0)
        self.assertEqual(next(s for s in record.shortages if s["name"] == "Flour")["exact"], "300")
        self.assertContains(self.client.get("/cooking-history/"), "300 g Flour")

    def test_repeated_token_deducts_once(self):
        item = self.stock("Milk", 3, "cup")
        token = uuid4()
        first = self.cook(action_token=token)
        second = self.cook(action_token=token)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CookingRecord.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 2)

    def test_same_planned_meal_different_tokens_deducts_once_and_actual_servings_saved(self):
        item = self.stock("Milk", 3, "cup")
        plan = self.plan()
        first = self.cook(plan=plan, servings=4, use_by=self.today + timedelta(days=3))
        second = self.cook(plan=plan)
        plan.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(item.exact_quantity, 1)
        self.assertEqual(plan.servings, 4)
        self.assertEqual(shopping_items(self.start), ([], 0))

    def test_cooking_changed_or_deleted_plan_rejected_without_deductions(self):
        plan = self.plan()
        other = Recipe.objects.create(title="Other")
        MealPlan.objects.filter(pk=plan.pk).update(recipe=other)
        item = self.stock("Milk", 2, "cup")
        with self.assertRaises(ValueError):
            self.cook(plan=plan)
        plan.delete()
        with self.assertRaises(ValueError):
            self.cook(plan=plan)
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 2)

    def test_cooking_reloads_recipe_after_review(self):
        reviewed = Recipe.objects.prefetch_related("ingredients").get(pk=self.recipe.pk)
        reviewed.ingredients.all()[0]
        self.recipe.ingredients.filter(name="Milk").update(quantity="2")
        item = self.stock("Milk", 3, "cup")
        record_cooking(reviewed, 2, self.today, uuid4())
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 1)

    def test_undo_restores_exact_stock_once_and_planned_meal_becomes_pending(self):
        plan = self.plan()
        item = self.stock("Milk", "5/3", "cup")
        record = self.cook(plan=plan)
        self.assertTrue(undo_cooking(record))
        self.assertFalse(undo_cooking(record))
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, Fraction(5, 3))
        self.assertEqual(shopping_items(self.start)[1], 1)
        self.assertEqual([i["name"] for i in shopping_items(self.start)[0]], ["Flour"])
        self.assertNotEqual(self.cook(plan=plan).pk, record.pk)

    def test_undo_uses_receipt_after_recipe_changed_and_stock_unit_changed(self):
        item = self.stock("Flour", 1, "kg")
        record = self.cook()
        self.recipe.ingredients.filter(name="Flour").update(quantity="1000")
        item.refresh_from_db()
        item.unit, item.quantity = "g", "500"
        item.save()
        undo_cooking(record)
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 1000)
        self.assertEqual(item.unit, "g")

    def test_undo_restores_deleted_stock_and_deleted_recipe_history_survives(self):
        item = self.stock("Flour", 500, "g", location="freezer", expires_on=self.today)
        record = self.cook(plan=self.plan())
        item.delete()
        self.recipe.delete()
        record.refresh_from_db()
        self.assertIsNone(record.recipe)
        self.assertIsNone(record.plan)
        undo_cooking(record)
        restored = PantryItem.objects.get()
        self.assertEqual(restored.exact_quantity, 500)
        self.assertEqual(restored.location, "freezer")
        self.assertEqual(restored.expires_on, self.today)
        self.assertContains(self.client.get("/cooking-history/"), "Milk bread")

    def test_undo_renamed_item_restores_original_ingredient_separately(self):
        item = self.stock("Flour", 600, "g")
        record = self.cook()
        item.refresh_from_db()
        item.name = "Rice"
        item.save()
        undo_cooking(record)
        self.assertEqual(PantryItem.objects.get(name="Flour").exact_quantity, 500)
        self.assertEqual(PantryItem.objects.get(name="Rice").exact_quantity, 100)

    def test_cooked_plan_cannot_be_replaced_or_removed_until_undo(self):
        plan = self.plan()
        record = self.cook(plan=plan)
        self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": self.start, "meal": "Dinner", "servings": 5})
        self.client.post(reverse("plan_remove", args=[plan.pk]))
        plan.refresh_from_db()
        self.assertEqual(plan.servings, 2)
        self.assertContains(self.client.get("/planner/"), "View record")
        undo_cooking(record)
        self.client.post(reverse("plan_remove", args=[plan.pk]))
        self.assertFalse(MealPlan.objects.exists())

    def test_checked_shortages_restock_exactly_once(self):
        self.plan()
        self.stock("Flour", "1/4", "kg")
        flour = next(i for i in shopping_items(self.start)[0] if i["name"] == "Flour")
        ShoppingCheck.objects.create(week=self.start, key=flour["key"], checked=True)
        for _ in range(2):
            self.client.post(reverse("shopping_stock"), {"week": self.start})
        self.assertEqual(pantry_balances()[("flour", "mass")], 500)
        self.assertEqual([i["name"] for i in shopping_items(self.start)[0]], ["Milk"])
        self.assertFalse(ShoppingCheck.objects.get().checked)
        self.assertFalse(PantryItem.objects.filter(name="Milk").exists())

    def test_invalid_cooking_inputs_do_not_mutate_stock(self):
        item = self.stock("Milk", 2, "cup")
        data = {"servings": 2, "action_token": uuid4(), "cooked_on": self.today, "notes": ""}
        for overrides in [{"servings": 0}, {"servings": "1.5"}, {"action_token": "invalid"}, {"cooked_on": self.today + timedelta(days=1)}, {"notes": "x" * 501}]:
            response = self.client.post(reverse("cook_recipe", args=[self.recipe.pk]), {**data, **overrides})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        item.refresh_from_db()
        self.assertEqual(item.exact_quantity, 2)
        self.assertFalse(CookingRecord.objects.exists())
        for value in ["abc", "9" * 100, "-1"]:
            self.assertEqual(self.client.get(reverse("cook_recipe", args=[self.recipe.pk]), {"plan": value}).status_code, 404)

    def test_mutations_require_post_and_csrf(self):
        item = self.stock("Flour", 500, "g")
        record = self.cook()
        urls = [reverse("pantry_add"), reverse("pantry_delete", args=[item.pk]), reverse("cooking_undo", args=[record.pk]), reverse("shopping_stock")]
        client = Client(enforce_csrf_checks=True)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(client.post(url).status_code, 403)
        self.assertEqual(client.post(reverse("cook_recipe", args=[self.recipe.pk])).status_code, 403)
