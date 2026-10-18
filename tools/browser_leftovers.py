"""Check Settings and expected leftover cards in an isolated seeded kitchen."""
from datetime import date
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "test-artifacts"
MONDAY = date(2026, 11, 2)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "savor.settings")


def exercise(base, recipe_id, other_id):
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/settings/")
            field = page.get_by_label("Household portions", exact=True)
            expect(field).to_have_value("2")
            field.fill("3")
            page.get_by_role("button", name="Save settings", exact=True).click()
            expect(page.get_by_label("Household portions", exact=True)).to_have_value("3")
            page.get_by_label("Household portions", exact=True).fill("2")
            page.get_by_role("button", name="Save settings", exact=True).click()
            page.goto(base + "/planner/")
            page.get_by_role("button", name="Plan a meal", exact=True).click()
            expect(page.locator("#plan-date")).to_have_value(MONDAY.isoformat())
            expect(page.locator("#plan-portions-eaten")).to_be_empty()
            page.locator("#plan-recipe").select_option(str(recipe_id))
            page.locator("#plan-servings").fill("4")
            page.locator("#plan-form button[type=submit]").click()
            expect(page.locator(".planned-meal")).to_have_count(2)
            source_id = page.locator(".planned-meal").nth(0).get_attribute("id")
            leftover_id = page.locator(".planned-meal").nth(1).get_attribute("id")
            card = page.locator(f"#{leftover_id}")
            expect(card).to_contain_text("Leftovers from Monday's dinner · 2 portions")
            expect(card).to_contain_text("Expected leftovers")
            expect(card.locator(".planned-cook-link")).to_have_count(0)
            expect(page.locator(f"#{source_id}")).to_contain_text("Eating 2 · 2 for leftovers")
            card.get_by_role("link", name="Leftovers from Monday's dinner · 2 portions", exact=True).click()
            assert page.url.endswith(f"#{source_id}")
            page.screenshot(path=str(ARTIFACTS / "leftovers-desktop.png"), full_page=True)

            def save_batch(servings):
                page.locator(f"#{source_id} [data-open-dialog]").click()
                expect(page.locator("#plan-portions-eaten")).to_have_value("2")
                page.locator("#plan-servings").fill(str(servings))
                page.locator("#plan-form button[type=submit]").click()

            save_batch(4)
            expect(page.locator(f"#{leftover_id}")).to_be_visible()
            expect(page.locator(".planned-meal")).to_have_count(2)
            save_batch(6)
            expect(page.locator(".planned-meal")).to_have_count(2)
            expect(page.get_by_role("link", name="Leftovers from Monday's dinner · 2 portions", exact=True)).to_have_count(1)
            save_batch(2)
            expect(page.locator(".planned-meal")).to_have_count(1)
            expect(page.locator(f"#{source_id}")).to_contain_text("Eating 2")
            expect(page.locator(f"#{source_id}")).not_to_contain_text("for leftovers")
            save_batch(4)
            leftover_id = page.locator(".planned-meal").nth(1).get_attribute("id")
            page.locator(f"#{leftover_id}").get_by_role("button", name="Remove Tuesday Dinner", exact=True).click()
            expect(page.locator(".message")).to_contain_text("Leftovers removed. 2 portions are spare again.")
            expect(page.locator(".planned-meal")).to_have_count(1)
            save_batch(4)
            leftover_id = page.locator(".planned-meal").nth(1).get_attribute("id")
            page.locator(f"#{leftover_id} [data-open-dialog]").click()
            page.locator("#plan-recipe").select_option(str(other_id))
            page.locator("#plan-servings").fill("2")
            page.locator("#plan-form button[type=submit]").click()
            expect(page.locator(f"#{leftover_id}")).not_to_contain_text("Expected leftovers")
            expect(page.locator(f"#{leftover_id} .planned-cook-link")).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(ARTIFACTS / "leftovers-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1100})
            page.locator(f"#{leftover_id}").get_by_role("button", name="Remove Tuesday Dinner", exact=True).click()
            save_batch(4)
            page.locator(f"#{source_id} .planned-cook-link").click()
            expect(page.locator('input[name="portions_eaten"]')).to_have_value("2")
            expect(page.locator('select[name="storage_location"]')).to_have_value("fridge")
            expect(page.locator('input[name="use_by"]')).to_be_empty()
            page.get_by_role("button", name="Record meal & update pantry", exact=True).click()
            expect(page.locator(".errorlist")).to_contain_text("Choose a use by date for the portions you are keeping.")
            page.locator('input[name="use_by"]').fill("2026-11-01")
            page.get_by_role("button", name="Record meal & update pantry", exact=True).click()
            expect(page.locator(".errorlist")).to_contain_text("Use by must be on or after the day you cooked.")
            page.locator('input[name="use_by"]').fill("2026-11-05")
            page.get_by_role("button", name="Record meal & update pantry", exact=True).click()
            expect(page.locator(".history-card")).to_contain_text("Made 4 · ate 2 · 2 left in the fridge · use by 5 Nov 2026")
            page.screenshot(path=str(ARTIFACTS / "prepared-batch-receipt.png"), full_page=True)
            page.goto(base + "/planner/")
            expect(page.get_by_text("Leftovers ready", exact=True)).to_be_visible()
            expect(page.get_by_text("Leftovers from Monday's dinner · 2 portions", exact=True)).to_be_visible()
            expect(page.locator(f"#{source_id}")).to_contain_text("2 remaining · 2 reserved · 0 unassigned")
            page.get_by_role("button", name="I ate this", exact=True).click()
            expect(page.locator(".message")).to_contain_text("Leftovers eaten. 2 portions used from Monday's dinner.")
            expect(page.locator(f"#{source_id}")).to_contain_text("0 remaining · 0 reserved · 0 unassigned")
            expect(page.get_by_text("Eaten", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="I ate this", exact=True)).to_have_count(0)
            page.screenshot(path=str(ARTIFACTS / "leftovers-eaten-desktop.png"), full_page=True)
            page.goto(base + "/cooking-history/")
            expect(page.locator(".leftover-history-card")).to_contain_text("Ate leftovers · 2 portions from Monday's dinner")
            page.get_by_role("button", name="Undo & restore pantry", exact=True).click()
            expect(page.locator(".message")).to_contain_text("Undo the leftover meals eaten from this batch first.")
            page.get_by_role("button", name="Undo", exact=True).click()
            expect(page.locator(".message")).to_contain_text("Leftovers restored. 2 portions are back.")
            expect(page.locator(".leftover-history-card")).to_have_count(0)
            page.goto(base + "/planner/")
            expect(page.get_by_role("button", name="I ate this", exact=True)).to_be_visible()
            expect(page.locator(f"#{source_id}")).to_contain_text("2 remaining · 2 reserved · 0 unassigned")
            page.goto(base + "/cooking-history/")
            page.get_by_role("button", name="Undo & restore pantry", exact=True).click()
            expect(page.locator(".message")).to_contain_text("Cooking record undone. The deducted ingredients are back in your pantry.")
            page.goto(base + "/planner/")
            expect(page.get_by_text("Expected leftovers", exact=True)).to_be_visible()
            page.locator(f"#{source_id} .planned-cook-link").click()
            page.locator('input[name="use_by"]').fill("2026-11-05")
            page.get_by_role("button", name="Record meal & update pantry", exact=True).click()
            expect(page.locator(".history-card:not(.history-undone)")).to_have_count(1)
            expect(page.locator(".history-card:not(.history-undone)")).to_contain_text("Made 4 · ate 2 · 2 left in the fridge · use by 5 Nov 2026")
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(ARTIFACTS / "prepared-batch-mobile.png"), full_page=True)
            page.goto(base + "/planner/")
            expect(page.get_by_role("button", name="I ate this", exact=True)).to_be_visible()
            page.get_by_role("button", name="I ate this", exact=True).click()
            expect(page.get_by_text("Eaten", exact=True)).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / "leftovers-eaten-mobile.png"), full_page=True)
            page.goto(base + "/cooking-history/")
            expect(page.locator(".leftover-history-card")).to_be_visible()
            page.get_by_role("button", name="Undo", exact=True).click()
            page.get_by_role("button", name="Undo & restore pantry", exact=True).click()
            page.goto(base + "/planner/")
            page.set_viewport_size({"width": 1440, "height": 1100})
            save_batch(6)
            page.locator(f"#{source_id} .planned-cook-link").click()
            page.locator('input[name="use_by"]').fill("2026-11-05")
            page.get_by_role("button", name="Record meal & update pantry", exact=True).click()
            page.goto(base + "/planner/")
            expect(page.locator(f"#{source_id}")).to_contain_text("4 remaining · 2 reserved · 2 unassigned")
            page.get_by_role("button", name="I ate this", exact=True).click()
            expect(page.locator(f"#{source_id}")).to_contain_text("2 remaining · 0 reserved · 2 unassigned")
            assert not errors, errors
        finally:
            browser.close()


def exercise_shopping_nutrition(base, recipe_id, food_id):
    """Exercise the rice and calorie examples through the actual forms."""
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            page.on("pageerror", lambda error: errors.append(str(error)))
            week = "2026-11-16"
            page.goto(base + "/planner/?week=" + week)
            page.get_by_role("button", name="Plan Monday Dinner", exact=True).click()
            page.locator("#plan-recipe").select_option(str(recipe_id))
            page.locator("#plan-servings").fill("4")
            page.locator("#plan-form button[type=submit]").click()
            source_id = page.locator(".planned-meal").nth(0).get_attribute("id")
            leftover_id = page.locator(".planned-meal").nth(1).get_attribute("id")
            expect(page.locator(f"#{leftover_id}")).to_contain_text("130 kcal per portion")
            page.locator(f"#{leftover_id} input[name=servings]").fill("1")
            page.locator(f"#{leftover_id}").get_by_role("button", name="Save portion", exact=True).click()
            page.goto(base + "/shopping/?week=" + week)
            expect(page.locator(".shopping-item")).to_have_count(1)
            expect(page.locator(".shopping-quantity")).to_have_text("400 g")
            expect(page.locator(".shopping-aside")).to_contain_text("Ingredients from your 1 uncooked meal")
            exported = page.request.get(base + "/shopping/export/?week=" + week)
            assert exported.text().splitlines() == ["Savor shopping list · 16 November 2026", "", "[ ] 400 g Rice"]
            page.screenshot(path=str(ARTIFACTS / "leftovers-shopping-rice.png"), full_page=True)
            page.goto(base + "/planner/?week=" + week)
            page.locator(f"#{source_id} .planned-cook-link").click()
            page.get_by_label("Use by", exact=True).fill("2026-11-05")
            page.get_by_role("button", name="Record meal & update pantry", exact=True).click()
            page.goto(base + "/shopping/?week=" + week)
            expect(page.locator(".shopping-item")).to_have_count(0)
            page.goto(base + f"/nutrition/foods/{food_id}/edit/")
            page.locator("#id_calories").fill("200")
            page.get_by_role("button", name="Save food nutrition", exact=True).click()
            page.goto(base + f"/recipes/{recipe_id}/")
            expect(page.locator('[data-nutrient="calories"]')).to_have_text("200")
            page.goto(base + "/planner/?week=" + week)
            expect(page.locator(f"#{leftover_id}")).to_contain_text("130 kcal per portion")
            expect(page.locator(".planner-day").nth(1).locator(".personal-daily-total")).to_contain_text("Calories: 130 kcal")
            def check_controls_fit():
                card = page.locator(f"#{leftover_id}")
                save = card.get_by_role("button", name="Save portion", exact=True).bounding_box()
                actions = card.locator(".planned-meal-actions").bounding_box()
                assert (save["y"] + save["height"] <= actions["y"] or
                        save["x"] + save["width"] <= actions["x"]), (save, actions)
            check_controls_fit()
            page.screenshot(path=str(ARTIFACTS / "leftovers-nutrition-snapshot.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.reload()
            expect(page.locator(f"#{leftover_id}")).to_contain_text("130 kcal per portion")
            check_controls_fit()
            page.screenshot(path=str(ARTIFACTS / "leftovers-nutrition-mobile.png"), full_page=True)
            assert not errors, errors
        finally:
            browser.close()


def run():
    ARTIFACTS.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="leftovers-check-") as folder:
        import django
        from django.conf import settings
        settings.DATABASES["default"]["NAME"] = str(Path(folder) / "kitchen.sqlite3")
        django.setup()
        from django.core.management import call_command
        from django.db import connections
        from kitchen.models import Ingredient, NutritionAlias, NutritionFood, NutritionProfile, Recipe
        call_command("migrate", verbosity=0)
        call_command("seed_demo", recipes_only=True, verbosity=0)
        recipe, other = list(Recipe.objects.order_by("pk")[:2])
        connections.close_all()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        address = f"127.0.0.1:{port}"
        base = "http://" + address
        code = """import os, django
from datetime import date
from unittest.mock import patch
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'savor.settings')
from django.conf import settings
settings.DATABASES['default']['NAME'] = os.environ['SAVOR_TEST_DATABASE']
django.setup()
from django.core.management import call_command
with patch('django.utils.timezone.localdate', return_value=date(2026, 11, 2)):
    call_command('runserver', os.environ['SAVOR_TEST_ADDRESS'], use_reloader=False)
"""
        environment = {**os.environ, "SAVOR_TEST_DATABASE": settings.DATABASES["default"]["NAME"], "SAVOR_TEST_ADDRESS": address}
        with (ARTIFACTS / "leftovers-browser-server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=environment, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urlopen(base + "/settings/", timeout=1) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Temporary server failed; see leftovers-browser-server.log")
                        time.sleep(.1)
                else:
                    raise RuntimeError("Temporary server did not start")
                exercise(base, recipe.pk, other.pk)
                rice_food = NutritionFood.objects.create(name="Browser snapshot rice", calories=130, protein=3, carbs=28, fat=0)
                NutritionAlias.objects.update_or_create(name="rice", defaults={"food": rice_food})
                rice = Recipe.objects.create(title="Snapshot rice dinner", servings=2, instructions="Cook.", photo="curry.jpg")
                Ingredient.objects.create(recipe=rice, name="Rice", quantity="200", unit="g")
                NutritionProfile.objects.create(name="Alex", calories=2000, protein=100)
                exercise_shopping_nutrition(base, rice.pk, rice_food.pk)
            finally:
                server.terminate()
                server.wait(timeout=10)
                connections.close_all()
    print("PASS: household Settings, 4/6/2-serving plans, exact leftover cards, retries, removal, replacement, explicit use-by validation, prepared batches, eating, reservation balances for yields 4 and 6, leftover undo before cooking undo, recording again, rice shopping/export, frozen nutrition after table edits, personal totals, and desktop/mobile UI; temporary database removed.")


if __name__ == "__main__":
    run()
