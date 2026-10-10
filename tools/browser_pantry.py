"""Exercise the pantry-to-table flow in an isolated SQLite kitchen and Chrome.

Starts its own temporary server/database and leaves the user's kitchen untouched.
Screenshots remain in test-artifacts/; temporary stock and receipts are discarded.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen
from datetime import date, timedelta
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "test-artifacts"
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "savor.settings")


def run():
    ARTIFACTS.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="pantry-check-", dir=ARTIFACTS) as folder:
        import django
        django.setup()
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connections
        from kitchen.models import Recipe, Ingredient, PantryItem, CookingRecord
        connections.close_all()
        settings.DATABASES["default"]["NAME"] = str(Path(folder) / "test.sqlite3")
        call_command("migrate", verbosity=0)
        call_command("seed_demo", recipes_only=True, verbosity=0)
        bread = Recipe.objects.create(title="Pantry milk bread", description="A simple loaf for slow Sunday mornings.", instructions="Mix the ingredients.\nBake until golden.", servings=2, photo="pancakes.jpg")
        Ingredient.objects.create(recipe=bread, name="Flour", quantity="500", unit="g")
        Ingredient.objects.create(recipe=bread, name="Milk", quantity="1", unit="cup")
        milk = Recipe.objects.create(title="Pantry warm milk", description="A little comfort, warmed gently.", instructions="Warm and serve.", servings=2, photo="pancakes.jpg")
        Ingredient.objects.create(recipe=milk, name="Milk", quantity="1/3", unit="cup")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server_code = "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'savor.settings'); django.setup(); from django.conf import settings; settings.DATABASES['default']['NAME'] = os.environ['SAVOR_TEST_DATABASE']; from django.core.management import call_command; call_command('runserver', os.environ['SAVOR_TEST_ADDRESS'], use_reloader=False)"
        environment = {**os.environ, "SAVOR_TEST_DATABASE": settings.DATABASES["default"]["NAME"], "SAVOR_TEST_ADDRESS": f"127.0.0.1:{port}"}
        with (ARTIFACTS / "pantry-browser-server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-c", server_code], cwd=ROOT, env=environment, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urlopen(base + "/pantry/", timeout=1) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Temporary server failed; see pantry-browser-server.log")
                        time.sleep(.1)
                else:
                    raise RuntimeError("Temporary server did not start")
                exercise(base, bread.pk, milk.pk, PantryItem, CookingRecord)
            finally:
                server.terminate()
                server.wait(timeout=10)
                connections.close_all()
    print("Passed: pantry add/top-up/edit, expiry, quantity-aware recipe ranking and extra-ingredient search, weekly subtraction, checked purchase restocking, cooking deduction, history and undo, desktop/mobile layouts. Isolated database removed.")


def exercise(base, bread_id, milk_id, pantry_model, record_model):
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/pantry/")
        expect(page.get_by_role("heading", name="Let’s get to know your kitchen.")).to_be_visible()

        def add(name, amount, unit, location="pantry", expiry=""):
            page.get_by_role("button", name="Add an ingredient", exact=True).click()
            page.locator("#id_name").fill(name)
            page.locator("#id_quantity").fill(amount)
            page.locator("#id_unit").fill(unit)
            page.locator("#id_location").select_option(location)
            page.locator("#id_measurement_system").select_option("us")
            page.locator("#id_expires_on").fill(expiry)
            page.get_by_role("button", name="Add to pantry", exact=True).click()
            expect(page.locator(".message")).to_contain_text("added to your pantry")

        add("Flour", "1/8", "kg")
        add("flour", "1/8", "kilograms")
        expect(page.locator(".pantry-row")).to_have_count(1)
        expect(page.locator(".stock-amount")).to_have_text("0.25 kg")
        add("Milk", "8", "tbsp", "fridge", (date.today() + timedelta(days=3)).isoformat())
        add("Eggs", "2", "each", "fridge")
        add("Tomatoes", "2", "cans", "pantry", (date.today() - timedelta(days=1)).isoformat())
        expect(page.locator(".pantry-row.past-date")).to_have_count(1)
        page.screenshot(path=str(ARTIFACTS / "pantry-desktop.png"), full_page=True)
        page.get_by_role("link", name="Use soon", exact=True).click()
        expect(page.locator(".pantry-row")).to_have_count(1)
        expect(page.locator(".pantry-name")).to_contain_text("Milk")

        page.goto(base + "/cook-with-pantry/?q=pantry")
        expect(page.locator(".recipe-card")).to_have_count(2)
        expect(page.locator(".recipe-card").first).to_contain_text("Pantry warm milk")
        expect(page.locator(".recipe-card").first).to_contain_text("Ready to cook")
        expect(page.locator(".recipe-card").nth(1)).to_contain_text("2 ingredients missing")
        page.locator('[name="servings"]').fill("4")
        page.get_by_role("button", name="Find a meal").click()
        expect(page.locator(".recipe-card").first).to_contain_text("1 ingredient missing")
        expect(page.locator(".recipe-card").first.locator(".recipe-meta")).to_contain_text("4 servings")
        page.locator('[name="servings"]').fill("2")
        page.locator('[name="ingredients"]').fill("flour, milk")
        page.get_by_role("button", name="Find a meal").click()
        expect(page.locator(".recipe-card").first).to_contain_text("Have all ingredients")
        expect(page.locator(".discovery-options")).to_contain_text("check that you have enough")
        page.goto(base + "/cook-with-pantry/")
        page.screenshot(path=str(ARTIFACTS / "pantry-discovery-desktop.png"), full_page=True)

        page.goto(base + f"/recipes/{bread_id}/")
        page.get_by_role("button", name="Add to meal plan").click()
        future = date.today() + timedelta(days=90)
        page.locator("#plan-date").fill(future.isoformat())
        page.locator("#plan-meal").select_option("Dinner")
        page.locator("#plan-servings").fill("2")
        page.get_by_role("button", name="Add to my week").click()
        planner_url = page.url
        page.get_by_role("link", name="View shopping list").click()
        shopping_url = page.url
        expect(page.locator(".shopping-item")).to_have_count(2)
        expect(page.locator(".shopping-item").filter(has=page.get_by_text("Flour", exact=True)).locator(".shopping-quantity")).to_have_text("250 g")
        expect(page.locator(".shopping-item").filter(has=page.get_by_text("Milk", exact=True)).locator(".shopping-quantity")).to_have_text("½ cup")
        expect(page.locator(".shopping-item").filter(has=page.get_by_text("Milk", exact=True))).to_contain_text("Pantry covers ½ cup of 1 cup")
        page.screenshot(path=str(ARTIFACTS / "pantry-shopping-desktop.png"), full_page=True)
        for checkbox in page.locator("[data-shopping-key]").all():
            checkbox.check()
        expect(page.locator("#checked-count")).to_have_text("2")
        page.get_by_role("button", name="Add checked items to pantry").click()
        expect(page.get_by_role("heading", name="Your pantry has this week covered.")).to_be_visible()
        expect(page.locator(".covered-ingredients>span")).to_have_count(2)

        page.goto(planner_url)
        page.get_by_role("link", name="I cooked this").click()
        cook_url = page.url
        expect(page.locator(".cook-available").first).to_have_text("500 g")
        expect(page.locator(".cook-available").nth(1)).to_have_text("1 cup")
        page.screenshot(path=str(ARTIFACTS / "pantry-cook-desktop.png"), full_page=True)
        page.locator("#id_notes").fill("Golden crust. Make this again next Sunday.")
        page.get_by_role("button", name="Record meal & update pantry").click()
        expect(page.locator(".history-card")).to_have_count(1)
        expect(page.locator(".history-note")).to_contain_text("Golden crust")
        page.locator(".history-details summary").click()
        expect(page.locator(".history-details")).to_contain_text("Pantry updated · 4 stock entries")
        page.screenshot(path=str(ARTIFACTS / "pantry-history-desktop.png"), full_page=True)
        page.goto(base + "/pantry/?status=empty")
        expect(page.locator(".pantry-row")).to_have_count(4)
        for amount in page.locator(".stock-amount").all():
            expect(amount).to_contain_text("0")
        page.goto(planner_url)
        expect(page.locator(".planned-cooked-badge")).to_have_text("Cooked")
        expect(page.locator(".planned-history-link")).to_be_visible()
        page.goto(shopping_url)
        expect(page.locator(".shopping-item")).to_have_count(0)
        page.goto(base + "/cooking-history/")
        page.get_by_role("button", name="Undo & restore pantry").click()
        expect(page.locator(".history-badge")).to_have_text("Undone")
        page.goto(shopping_url)
        expect(page.get_by_role("heading", name="Your pantry has this week covered.")).to_be_visible()

        page.goto(base + "/pantry/")
        page.locator(".pantry-row").filter(has_text="0.25 kg").get_by_role("link", name="Edit Flour", exact=True).click()
        page.locator("#id_quantity").fill("0")
        page.get_by_role("button", name="Save pantry item").click()
        page.goto(shopping_url)
        expect(page.locator(".shopping-item")).to_have_count(1)
        expect(page.locator(".shopping-quantity")).to_have_text("250 g")

        page.set_viewport_size({"width": 390, "height": 844})
        for path, name in [("/pantry/", "pantry"), ("/cook-with-pantry/", "pantry-discovery"), (shopping_url.removeprefix(base), "pantry-shopping"), (cook_url.removeprefix(base), "pantry-cook"), ("/cooking-history/", "pantry-history"), (f"/recipes/{milk_id}/", "pantry-recipe")]:
            page.goto(base + path)
            page.screenshot(path=str(ARTIFACTS / f"{name}-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
        page.goto(base + "/pantry/")
        page.get_by_role("button", name="Add an ingredient", exact=True).click()
        expect(page.locator("#pantry-dialog")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(ARTIFACTS / "pantry-dialog-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    # Playwright's synchronous loop is closed before using Django's synchronous ORM.
    assert pantry_model.objects.get(name="Flour", unit="kg").exact_quantity == 0
    assert pantry_model.objects.get(name="Flour", unit="g").exact_quantity == 250
    assert record_model.objects.count() == 1
    assert record_model.objects.get().undone_at is not None


if __name__ == "__main__":
    run()
