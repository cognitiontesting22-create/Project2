from datetime import date, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.messages import get_messages
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from .inventory import change_reservation, eat_leftovers, lock_kitchen, record_cooking, undo_leftovers
from .models import CookingRecord, Ingredient, KitchenSettings, LeftoverMeal, MealPlan, PantryItem, PreparedBatch, Recipe


MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
USE_BY = date(2026, 11, 5)
EATEN_PROTECTION = "These leftovers are already eaten. Undo that record before changing the plan."
DUPLICATE_EAT = "These leftovers were already recorded. Portions haven't been deducted twice."
DUPLICATE_UNDO = "This was already undone. Portions haven't been added twice."


class LeftoverMealTests(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=MONDAY)
        clock.start()
        self.addCleanup(clock.stop)
        KitchenSettings.objects.create(pk=1, household_portions=2)
        self.recipe = Recipe.objects.create(title="Monday pasta", servings=4, instructions="Cook.")
        Ingredient.objects.create(recipe=self.recipe, name="Flour", quantity="400", unit="g")
        self.stock = PantryItem.objects.create(name="Flour", quantity="1000", unit="g", expires_on=USE_BY)

    def plan(self, servings=4, day=MONDAY, **overrides):
        return self.client.post(reverse("plan_add"), {"recipe": self.recipe.pk, "date": day.isoformat(),
            "meal": "Dinner", "servings": servings, "portions_eaten": 2, **overrides}, follow=True)

    def prepare(self, servings=4, day=MONDAY):
        self.plan(servings, day)
        self.source = MealPlan.objects.get(date=day, meal="Dinner")
        self.leftover = self.source.leftover_meals.get()
        self.cooking, _ = record_cooking(self.recipe, servings, day, uuid4(), plan=self.source,
            portions_eaten=2, use_by=day + timedelta(days=3))
        self.batch = self.cooking.prepared_batch

    def eat(self, token=None):
        return self.client.post(reverse("leftover_eat", args=[self.leftover.pk]),
            {"action_token": str(token or uuid4())}, follow=True)

    def undo(self, record=None):
        return self.client.post(reverse("leftover_undo", args=[(record or LeftoverMeal.objects.get()).pk]), follow=True)

    def messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def assert_numbers(self, remaining, reserved, unassigned):
        self.batch.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual((self.batch.remaining_portions, self.batch.reserved_portions, self.batch.unassigned_portions),
                         (remaining, reserved, unassigned))
        self.assertEqual(self.source.reserved_portions, reserved)
        page = self.client.get(reverse("planner"), {"week": self.source.date.isoformat()})
        self.assertContains(page, f"{remaining} remaining · {reserved} reserved · {unassigned} unassigned")

    def pantry_snapshot(self):
        return list(PantryItem.objects.order_by("pk").values())

    def test_four_portion_worked_example_and_history_never_touch_pantry(self):
        self.prepare()
        self.assert_numbers(2, 2, 0)
        before = self.pantry_snapshot()
        receipt = (self.cooking.deductions, self.cooking.shortages)
        token = uuid4()
        with patch("django.utils.timezone.localdate", return_value=TUESDAY):
            page = self.eat(token)
        self.assertEqual(self.messages(page), ["Leftovers eaten. 2 portions used from Monday's dinner."])
        self.assert_numbers(0, 0, 0)
        self.assertEqual(self.pantry_snapshot(), before)
        self.cooking.refresh_from_db()
        self.assertEqual((self.cooking.deductions, self.cooking.shortages), receipt)
        record = LeftoverMeal.objects.get()
        self.assertEqual((record.batch_id, record.plan_id, record.portions, record.eaten_on, record.action_token, record.undone_at),
                         (self.batch.pk, self.leftover.pk, 2, TUESDAY, token, None))
        self.assertContains(page, "Eaten")
        self.assertNotContains(page, "I ate this")
        history = self.client.get(reverse("cooking_history"))
        self.assertContains(history, "Ate leftovers · 2 portions from Monday's dinner")
        self.assertContains(history, reverse("leftover_undo", args=[record.pk]))

    def test_six_portion_variant_preserves_unassigned_portions(self):
        self.prepare(6)
        self.assert_numbers(4, 2, 2)
        self.eat()
        self.assert_numbers(2, 0, 2)
        self.undo()
        self.assert_numbers(4, 2, 2)

    def test_plans_store_reservations_before_cooking_and_resaves_do_not_duplicate(self):
        self.plan(6)
        source = MealPlan.objects.get(date=MONDAY)
        self.assertEqual((source.remaining_portions, source.reserved_portions, source.unassigned_portions), (4, 2, 2))
        leftover = source.leftover_meals.get()
        for _ in range(2):
            self.plan(6)
        source.refresh_from_db()
        self.assertEqual(source.reserved_portions, 2)
        self.assertEqual(source.leftover_meals.get().pk, leftover.pk)
        self.plan(2)
        source.refresh_from_db()
        self.assertEqual((source.remaining_portions, source.reserved_portions, source.unassigned_portions), (0, 0, 0))
        self.assertFalse(source.leftover_meals.exists())

    def test_retries_same_token_and_same_meal_with_different_tokens(self):
        self.prepare()
        token = uuid4()
        self.eat(token)
        before = self.pantry_snapshot()
        for retry_token in (token, uuid4()):
            page = self.eat(retry_token)
            self.assertEqual(self.messages(page), [DUPLICATE_EAT])
            self.assert_numbers(0, 0, 0)
        self.assertEqual(LeftoverMeal.objects.count(), 1)
        self.assertEqual(self.pantry_snapshot(), before)

    def test_undo_and_repeat_undo_restore_the_same_reservation_without_stock_changes(self):
        self.prepare()
        self.eat()
        before = self.pantry_snapshot()
        record = LeftoverMeal.objects.get()
        page = self.undo(record)
        self.assertEqual(self.messages(page), ["Leftovers restored. 2 portions are back."])
        record.refresh_from_db()
        self.assertIsNotNone(record.undone_at)
        self.assert_numbers(2, 2, 0)
        self.assertNotContains(page, "Ate leftovers ·")
        self.assertContains(self.client.get(reverse("planner")), "I ate this")
        page = self.undo(record)
        self.assertEqual(self.messages(page), [DUPLICATE_UNDO])
        self.assert_numbers(2, 2, 0)
        self.assertEqual(self.pantry_snapshot(), before)

    def test_old_eat_and_undo_tokens_cannot_change_a_new_consumption(self):
        self.prepare()
        token = uuid4()
        self.eat(token)
        old_record = LeftoverMeal.objects.get()
        self.undo(old_record)
        self.assertEqual(self.messages(self.eat(token)), [DUPLICATE_EAT])
        self.assert_numbers(2, 2, 0)
        self.eat()
        self.assertEqual(LeftoverMeal.objects.filter(undone_at__isnull=True).count(), 1)
        self.assertEqual(self.messages(self.undo(old_record)), [DUPLICATE_UNDO])
        self.assert_numbers(0, 0, 0)

    def test_expected_leftovers_cannot_be_eaten(self):
        self.plan()
        self.leftover = MealPlan.objects.get(leftover_of__isnull=False)
        before = self.pantry_snapshot()
        self.assertEqual(self.messages(self.eat()), ["Cook Monday's dinner first, these are still expected leftovers."])
        self.assertFalse(LeftoverMeal.objects.exists())
        self.assertFalse(PreparedBatch.objects.exists())
        self.assertEqual(self.pantry_snapshot(), before)
        self.assertEqual(MealPlan.objects.get(date=MONDAY).reserved_portions, 2)

    def test_routes_require_post_csrf_a_leftover_and_a_valid_uuid(self):
        self.prepare()
        eat_url = reverse("leftover_eat", args=[self.leftover.pk])
        self.assertEqual(self.client.get(eat_url).status_code, 405)
        self.assertEqual(self.client.post(reverse("leftover_eat", args=[self.source.pk]), {"action_token": str(uuid4())}).status_code, 404)
        self.assertEqual(Client(enforce_csrf_checks=True).post(eat_url, {"action_token": str(uuid4())}).status_code, 403)
        for value in (None, "bad-token"):
            self.client.post(eat_url, {} if value is None else {"action_token": value})
        self.assertFalse(LeftoverMeal.objects.exists())
        self.assert_numbers(2, 2, 0)
        self.eat()
        undo_url = reverse("leftover_undo", args=[LeftoverMeal.objects.get().pk])
        self.assertEqual(self.client.get(undo_url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(undo_url).status_code, 403)
        self.assert_numbers(0, 0, 0)

    def test_rendered_tokens_are_valid_and_fresh_for_each_card_and_request(self):
        self.prepare()
        self.plan(4, MONDAY + timedelta(days=2))
        second = MealPlan.objects.get(date=MONDAY + timedelta(days=2))
        record_cooking(self.recipe, 4, second.date, uuid4(), plan=second, use_by=USE_BY, portions_eaten=2)
        def tokens():
            page = self.client.get(reverse("planner"))
            plans = [meal["plan"] for day in page.context["days"] for meal in day["meals"] if meal["plan"] and meal["plan"].leftover_of_id]
            values = {UUID(str(plan.eat_action_token)) for plan in plans}
            self.assertEqual(len(values), 2)
            for value in values:
                self.assertContains(page, f'name="action_token" value="{value}"')
            self.assertContains(page, 'name="csrfmiddlewaretoken"')
            return values
        self.assertFalse(tokens() & tokens())

    def test_cross_week_eat_redirects_to_leftover_week(self):
        sunday = MONDAY + timedelta(days=6)
        self.prepare(day=sunday)
        page = self.eat()
        self.assertEqual(page.redirect_chain, [(reverse("planner") + "?week=2026-11-09", 302)])
        self.assertEqual(self.messages(page), ["Leftovers eaten. 2 portions used from Sunday's dinner."])

    def test_cooking_undo_waits_for_consumption_undo_then_keeps_expected_reservation(self):
        self.prepare()
        self.eat()
        before = self.pantry_snapshot()
        url = reverse("cooking_undo", args=[self.cooking.pk])
        page = self.client.post(url, follow=True)
        self.assertEqual(self.messages(page), ["Undo the leftover meals eaten from this batch first."])
        self.cooking.refresh_from_db()
        self.assertIsNone(self.cooking.undone_at)
        self.assert_numbers(0, 0, 0)
        self.assertEqual(self.pantry_snapshot(), before)
        self.undo()
        self.client.post(url)
        self.assertFalse(PreparedBatch.objects.exists())
        self.source.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.exact_quantity, 1000)
        self.assertEqual((self.source.remaining_portions, self.source.reserved_portions, self.source.unassigned_portions), (2, 2, 0))
        page = self.client.get(reverse("planner"))
        self.assertContains(page, "Expected leftovers")
        self.assertNotContains(page, "I ate this")
        record, _ = record_cooking(self.recipe, 4, MONDAY, uuid4(), plan=self.source, portions_eaten=2, use_by=USE_BY)
        self.assertEqual(record.prepared_batch.reserved_portions, 2)

    def test_undone_token_survives_batch_deletion_and_recooking(self):
        self.prepare()
        token = uuid4()
        self.eat(token)
        record = LeftoverMeal.objects.get()
        self.undo(record)
        self.client.post(reverse("cooking_undo", args=[self.cooking.pk]), follow=True)
        record.refresh_from_db()
        self.assertIsNone(record.batch_id)
        cooking, _ = record_cooking(self.recipe, 4, MONDAY, uuid4(), plan=self.source, portions_eaten=2, use_by=USE_BY)
        self.batch = cooking.prepared_batch
        self.assertEqual(self.messages(self.eat(token)), [DUPLICATE_EAT])
        self.assert_numbers(2, 2, 0)
        self.eat()
        self.assert_numbers(0, 0, 0)

    def test_eaten_leftover_cannot_be_removed_or_replaced(self):
        self.prepare()
        self.eat()
        before = self.pantry_snapshot()
        response = self.client.post(reverse("plan_remove", args=[self.leftover.pk]), follow=True)
        self.assertEqual(self.messages(response), [EATEN_PROTECTION])
        self.assertEqual(self.messages(self.plan(2, TUESDAY)), [EATEN_PROTECTION])
        self.leftover.refresh_from_db()
        self.assertEqual(self.leftover.leftover_of_id, self.source.pk)
        self.assert_numbers(0, 0, 0)
        self.assertEqual(self.pantry_snapshot(), before)
        self.undo()
        self.plan(2, TUESDAY)
        self.leftover.refresh_from_db()
        self.assertIsNone(self.leftover.leftover_of_id)
        self.assert_numbers(2, 0, 2)

    def test_removing_ready_leftover_releases_reservation_without_changing_remaining(self):
        self.prepare(6)
        before = self.pantry_snapshot()
        response = self.client.post(reverse("plan_remove", args=[self.leftover.pk]), follow=True)
        self.assertEqual(self.messages(response), ["Leftovers removed. 2 portions are spare again."])
        self.assert_numbers(4, 0, 4)
        self.assertEqual(self.pantry_snapshot(), before)

    def test_removing_expected_leftover_releases_stored_plan_reservation(self):
        self.plan(6)
        source = MealPlan.objects.get(date=MONDAY)
        self.client.post(reverse("plan_remove", args=[source.leftover_meals.get().pk]))
        source.refresh_from_db()
        self.assertEqual((source.remaining_portions, source.reserved_portions, source.unassigned_portions), (4, 0, 4))
        self.plan(6)
        source.refresh_from_db()
        self.assertEqual(source.reserved_portions, 2)

    def test_occupied_slot_reserves_nothing(self):
        self.plan(2, TUESDAY)
        self.plan(6)
        self.assertEqual(MealPlan.objects.get(date=MONDAY).reserved_portions, 0)

    def test_cooking_cannot_leave_fewer_portions_than_reserved(self):
        self.plan()
        source = MealPlan.objects.get(date=MONDAY)
        before = self.pantry_snapshot()
        with self.assertRaisesMessage(ValueError, "These portions are reserved for leftover meals."):
            record_cooking(self.recipe, 4, MONDAY, uuid4(), plan=source, portions_eaten=3, use_by=USE_BY)
        self.assertEqual(self.pantry_snapshot(), before)
        self.assertFalse(CookingRecord.objects.exists())
        self.assertFalse(PreparedBatch.objects.exists())
        source.refresh_from_db()
        self.assertEqual((source.servings, source.portions_eaten, source.reserved_portions), (4, 2, 2))

    def test_reservation_helper_refuses_overbooking_and_negative_reservations(self):
        self.prepare()
        for delta in (1, -3):
            with transaction.atomic():
                lock_kitchen()
                with self.assertRaises(ValueError):
                    change_reservation(self.source, delta)
            self.assert_numbers(2, 2, 0)

    def test_batch_constraints_refuse_negative_or_overbooked_counts(self):
        self.prepare()
        for values in ({"reserved_portions": 3}, {"reserved_portions": -1}, {"remaining_portions": -1}, {"remaining_portions": 1}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                PreparedBatch.objects.filter(pk=self.batch.pk).update(**values)
        self.assert_numbers(2, 2, 0)

    def test_plan_constraints_refuse_negative_remaining_or_overbooked_reservations(self):
        self.plan()
        source = MealPlan.objects.get(date=MONDAY)
        for values in ({"reserved_portions": 3}, {"reserved_portions": -1}, {"portions_eaten": 5}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                MealPlan.objects.filter(pk=source.pk).update(**values)
        source.refresh_from_db()
        self.assertEqual((source.remaining_portions, source.reserved_portions, source.unassigned_portions), (2, 2, 0))

    def test_multiple_reservations_share_one_batch_and_all_must_be_undone(self):
        self.prepare(6)
        with transaction.atomic():
            lock_kitchen()
            second = MealPlan.objects.create(recipe=self.recipe, date=MONDAY + timedelta(days=2),
                servings=2, portions_eaten=2, leftover_of=self.source)
            change_reservation(self.source, 2)
        self.assert_numbers(4, 4, 0)
        self.eat()
        first = LeftoverMeal.objects.get()
        self.assert_numbers(2, 2, 0)
        eat_leftovers(second, uuid4())
        self.assert_numbers(0, 0, 0)
        self.undo(first)
        self.assert_numbers(2, 2, 0)
        page = self.client.post(reverse("cooking_undo", args=[self.cooking.pk]), follow=True)
        self.assertEqual(self.messages(page), ["Undo the leftover meals eaten from this batch first."])
        self.undo(LeftoverMeal.objects.get(plan=second))
        self.assert_numbers(4, 4, 0)

    def test_cooking_retry_preserves_batch_reservation_counts(self):
        self.prepare()
        self.eat()
        token = self.cooking.action_token
        for retry in (token, uuid4()):
            record, changed = record_cooking(self.recipe, 6, MONDAY, retry, plan=self.source,
                portions_eaten=0, use_by=USE_BY)
            self.assertFalse(changed)
            self.assertEqual(record.pk, self.cooking.pk)
            self.assert_numbers(0, 0, 0)
        self.assertEqual(PreparedBatch.objects.count(), 1)

    def test_eating_and_undo_roll_back_every_portion_change_on_failure(self):
        self.prepare()
        with patch.object(PreparedBatch, "save", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                eat_leftovers(self.leftover, uuid4())
        self.assertFalse(LeftoverMeal.objects.exists())
        self.assert_numbers(2, 2, 0)
        self.eat()
        record = LeftoverMeal.objects.get()
        with patch.object(LeftoverMeal, "save", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                undo_leftovers(record)
        record.refresh_from_db()
        self.assertIsNone(record.undone_at)
        self.assert_numbers(0, 0, 0)

    def test_recipe_removal_cannot_bypass_eaten_protection_and_releases_undone_claims(self):
        self.prepare()
        self.eat()
        url = reverse("recipe_delete", args=[self.recipe.pk])
        page = self.client.post(url, follow=True)
        self.assertEqual(self.messages(page), [EATEN_PROTECTION])
        self.assert_numbers(0, 0, 0)
        self.undo()
        self.client.post(url)
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.remaining_portions, self.batch.reserved_portions), (2, 0))
        record = LeftoverMeal.objects.get()
        self.assertIsNone(record.plan_id)


class LeftoverReservationsMigrationTests(TransactionTestCase):
    def test_existing_expected_and_prepared_meals_get_explicit_reservations(self):
        old = ("kitchen", "0008_prepared_batch")
        new = ("kitchen", "0009_leftover_reservations_and_meals")
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes("kitchen")
        try:
            executor.migrate([old])
            apps = executor.loader.project_state([old]).apps
            RecipeModel = apps.get_model("kitchen", "Recipe")
            Plan = apps.get_model("kitchen", "MealPlan")
            Batch = apps.get_model("kitchen", "PreparedBatch")
            Receipt = apps.get_model("kitchen", "CookingRecord")
            recipe = RecipeModel.objects.create(title="Existing pasta", servings=6)
            sources = []
            for index in range(2):
                source = Plan.objects.create(recipe=recipe, date=MONDAY + timedelta(days=index * 2), servings=6, portions_eaten=2)
                Plan.objects.create(recipe=recipe, date=source.date + timedelta(days=1), servings=2, portions_eaten=2, leftover_of=source)
                sources.append(source)
            receipt = Receipt.objects.create(recipe=recipe, plan=sources[1], recipe_title=recipe.title, servings=6, cooked_on=MONDAY)
            batch = Batch.objects.create(cooking_record=receipt, recipe_title=recipe.title, yield_portions=6,
                portions_eaten=2, remaining_portions=4, storage_location="fridge", use_by=USE_BY)
            executor = MigrationExecutor(connection)
            executor.migrate([new])
            apps = executor.loader.project_state([new]).apps
            Plan = apps.get_model("kitchen", "MealPlan")
            self.assertEqual(list(Plan.objects.filter(pk__in=[p.pk for p in sources]).values_list("reserved_portions", flat=True)), [2, 2])
            batch = apps.get_model("kitchen", "PreparedBatch").objects.get(pk=batch.pk)
            self.assertEqual((batch.remaining_portions, batch.reserved_portions), (4, 2))
            self.assertFalse(apps.get_model("kitchen", "LeftoverMeal").objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)
