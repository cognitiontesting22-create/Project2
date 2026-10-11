"""Verify live recipe nutrition and the local editor in an isolated kitchen."""
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
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "savor.settings")


def run():
    ARTIFACTS.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="nutrition-check-", dir=ARTIFACTS) as folder:
        import django
        django.setup()
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connections
        from kitchen.models import Recipe, Ingredient
        connections.close_all()
        settings.DATABASES["default"]["NAME"] = str(Path(folder) / "test.sqlite3")
        call_command("migrate", verbosity=0)
        call_command("seed_demo", recipes_only=True, verbosity=0)
        recipe = Recipe.objects.create(title="Nutrition check loaf", description="A little bread with a transparent nutrition estimate.", servings=2, instructions="Mix and bake.", photo="bread.jpg")
        Ingredient.objects.create(recipe=recipe, name="Nutrition check flour", quantity="100", unit="g")
        partial = Recipe.objects.create(title="Partial nutrition check", servings=2, instructions="Mix.", photo="bread.jpg")
        Ingredient.objects.create(recipe=partial, name="Nutrition check flour", quantity="100", unit="g")
        Ingredient.objects.create(recipe=partial, name="Unlisted nutrition check", quantity="1", unit="bag")
        cups = Recipe.objects.create(title="Cup nutrition check", servings=2, instructions="Mix.", photo="bread.jpg")
        Ingredient.objects.create(recipe=cups, name="Nutrition check flour", quantity="1", unit="cup")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        code = "import os,django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','savor.settings'); django.setup(); from django.conf import settings; settings.DATABASES['default']['NAME']=os.environ['SAVOR_TEST_DATABASE']; from django.core.management import call_command; call_command('runserver',os.environ['SAVOR_TEST_ADDRESS'],use_reloader=False)"
        environment = {**os.environ, "SAVOR_TEST_DATABASE": settings.DATABASES["default"]["NAME"], "SAVOR_TEST_ADDRESS": f"127.0.0.1:{port}"}
        with (ARTIFACTS / "nutrition-browser-server.log").open("w") as log:
            server = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=environment, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with urlopen(base, timeout=1) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Temporary server exited")
                        time.sleep(.1)
                else:
                    raise RuntimeError("Temporary server did not start")
                exercise(base, recipe.pk, partial.pk, cups.pk)
            finally:
                server.terminate()
                server.wait(timeout=10)
                connections.close_all()
    print("Passed: food table CRUD and matching, live batch/per-serving calories and macros, typical cup weights, partial coverage, reload and no-JavaScript scaling, desktop/mobile layouts. Temporary kitchen removed.")


def exercise(base, recipe_id, partial_id, cup_id):
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + f"/recipes/{recipe_id}/")
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("—")
        expect(page.locator(".nutrition-estimate-label")).to_have_text("No estimate yet")
        page.locator(".nutrition-gaps").get_by_role("link", name="Add food").click()
        expect(page.locator("#id_name")).to_have_value("Nutrition check flour")
        page.locator("#id_description").fill("Dry flour for a test loaf")
        for key, value in {"calories": "400", "protein": "10", "carbs": "70", "fat": "4", "cup_grams": "120"}.items():
            page.locator("#id_" + key).fill(value)
        page.locator("#id_source_label").fill("Test label")
        page.get_by_role("button", name="Save food nutrition").click()
        page.locator('[aria-label="Search nutrition foods"]').fill("Nutrition check flour")
        page.locator('[aria-label="Search nutrition foods"]').press("Enter")
        expect(page.locator(".food-table tbody tr")).to_have_count(1)
        page.locator(".food-name").click()
        food_url = page.url
        expect(page.locator("#id_name")).to_have_value("Nutrition check flour")

        page.goto(base + f"/recipes/{recipe_id}/")
        for key, value in {"calories": "200", "protein": "5.0", "carbs": "35.0", "fat": "2.0"}.items():
            expect(page.locator(f'[data-nutrient="{key}"]')).to_have_text(value)
        page.get_by_role("button", name="Whole batch", exact=True).click()
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("400")
        page.locator(".nutrition-breakdown summary").click()
        page.locator("#serving-count").fill("8")
        page.locator("#serving-count").press("Enter")
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("1600")
        expect(page.locator('[data-nutrient="protein"]')).to_have_text("40.0")
        expect(page.locator(".nutrition-breakdown")).to_have_attribute("open", "")
        expect(page.locator(".nutrition-breakdown")).to_contain_text("400.0 g")
        expect(page.locator(".nutrition-batch-summary")).to_contain_text("Each serving: 200 kcal")
        page.reload()
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("1600")
        page.get_by_role("button", name="Per serving", exact=True).click()
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("200")
        expect(page.locator(".nutrition-batch-summary")).to_contain_text("Whole batch for 8 servings: 1600 kcal")
        page.screenshot(path=str(ARTIFACTS / "nutrition-desktop.png"), full_page=True)

        page.goto(base + f"/recipes/{cup_id}/")
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("240")
        page.locator(".nutrition-breakdown summary").click()
        expect(page.locator(".nutrition-assumption")).to_contain_text("1 US cup = 120 g")
        page.goto(base + f"/recipes/{partial_id}/")
        expect(page.locator(".nutrition-estimate-label")).to_have_text("Partial estimate")
        expect(page.locator(".nutrition-gaps")).to_contain_text("Unlisted nutrition check")
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("200")
        page.screenshot(path=str(ARTIFACTS / "nutrition-partial-desktop.png"), full_page=True)

        page.goto(food_url)
        page.locator("#id_calories").fill("600")
        page.get_by_role("button", name="Save food nutrition").click()
        page.goto(base + f"/recipes/{recipe_id}/")
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("300")

        no_js = browser.new_context(java_script_enabled=False)
        plain = no_js.new_page()
        plain.goto(base + f"/recipes/{recipe_id}/?servings=4")
        expect(plain.locator('[data-nutrient="calories"]')).to_have_text("300")
        plain.get_by_role("button", name="Whole batch", exact=True).click()
        expect(plain.locator('[data-nutrient="calories"]')).to_have_text("1200")
        plain.locator("#serving-count").fill("8")
        plain.get_by_role("button", name="Update servings", exact=True).click()
        expect(plain.locator('[data-nutrient="calories"]')).to_have_text("2400")
        no_js.close()

        page.set_viewport_size({"width": 390, "height": 844})
        for path, name in [(f"/recipes/{recipe_id}/", "nutrition"), (f"/recipes/{partial_id}/", "nutrition-partial"), ("/nutrition/?q=milk", "nutrition-table"), (food_url.removeprefix(base), "nutrition-food")]:
            page.goto(base + path)
            page.screenshot(path=str(ARTIFACTS / f"{name}-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
        page.goto(food_url)
        page.get_by_role("button", name="Remove this food").click()
        page.get_by_role("button", name="Remove food", exact=True).click()
        page.goto(base + f"/recipes/{recipe_id}/")
        expect(page.locator('[data-nutrient="calories"]')).to_have_text("—")
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    run()
