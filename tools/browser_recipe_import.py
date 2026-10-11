"""Exercise pasted recipe drafts in a disposable SQLite kitchen."""
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
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "savor.settings")
SOURCE = """Lemon pancakes
Serves 4
Prep time: 10 minutes
Cook time: 15 minutes

Ingredients
1½ cups plain flour
2 large eggs
1 cup milk
½ tsp salt

Method
1. Whisk the ingredients together.
2. Cook spoonfuls in a warm pan until golden.
"""


def run():
    ARTIFACTS.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="recipe-import-check-", dir=ARTIFACTS) as folder:
        import django
        django.setup()
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connections
        from kitchen.models import Recipe, Ingredient, MealPlan, PantryItem
        connections.close_all()
        settings.DATABASES["default"]["NAME"] = str(Path(folder) / "test.sqlite3")
        call_command("migrate", verbosity=0)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        code = "import os,django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','savor.settings'); django.setup(); from django.conf import settings; settings.DATABASES['default']['NAME']=os.environ['SAVOR_TEST_DATABASE']; from django.core.management import call_command; call_command('runserver',os.environ['SAVOR_TEST_ADDRESS'],use_reloader=False)"
        environment = {**os.environ, "SAVOR_TEST_DATABASE": settings.DATABASES["default"]["NAME"], "SAVOR_TEST_ADDRESS": f"127.0.0.1:{port}"}
        with (ARTIFACTS / "recipe-import-browser-server.log").open("w") as log:
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
                exercise(base)
                connections.close_all()
                assert Recipe.objects.count() == 3
                recipe = Recipe.objects.get(title="Our lemon pancakes")
                assert recipe.ingredients.count() == 5
                assert str(recipe.ingredients.first().exact_quantity) == "3/2"
                assert PantryItem.objects.count() == 0
                assert MealPlan.objects.count() == 1
                assert Ingredient.objects.filter(recipe__title="No JavaScript pancakes").count() == 4
            finally:
                server.terminate()
                server.wait(timeout=10)
                connections.close_all()
    print("Passed: paste/review/cancel/save, corrections, fractions, dynamic ingredients, scaling and nutrition, planner/shopping, invalid draft persistence, no-JavaScript flow, and mobile layout. Temporary kitchen removed.")


def exercise(base):
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        page.get_by_role("link", name="Paste a recipe", exact=True).click()
        expect(page.locator("#id_text")).to_be_visible()
        page.locator("#id_text").fill(SOURCE)
        page.get_by_role("button", name="Create editable draft").click()
        expect(page.locator("#id_title")).to_have_value("Lemon pancakes")
        expect(page.locator("#id_servings")).to_have_value("4")
        expect(page.locator("#id_prep_time")).to_have_value("10")
        expect(page.locator("#id_cook_time")).to_have_value("15")
        expect(page.locator(".ingredient-form")).to_have_count(4)
        expect(page.locator("#id_ingredients-0-quantity")).to_have_value("1½")
        expect(page.locator("#id_ingredients-0-unit")).to_have_value("cup")
        expect(page.locator("#id_ingredients-1-name")).to_have_value("eggs")
        page.get_by_text("See the original pasted recipe", exact=True).click()
        expect(page.locator(".import-review pre")).to_have_text(SOURCE.strip())
        page.get_by_role("link", name="Cancel", exact=True).click()
        expect(page.locator(".recipe-card")).to_have_count(0)
        page.get_by_role("link", name="Paste a recipe", exact=True).click()
        page.locator("#id_text").fill(SOURCE)
        page.get_by_role("button", name="Create editable draft").click()
        page.locator("#id_title").fill("Our lemon pancakes")
        page.locator("#id_category").select_option("Breakfast")
        page.get_by_role("button", name="Add ingredient", exact=True).click()
        page.locator("#id_ingredients-4-quantity").fill("1/4")
        page.locator("#id_ingredients-4-unit").fill("cup")
        page.locator("#id_ingredients-4-name").fill("sugar")
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(ARTIFACTS / "recipe-import-review-desktop.png"), full_page=True)
        page.get_by_role("button", name="Save to recipe box", exact=True).click()
        expect(page.locator("h1")).to_have_text("Our lemon pancakes")
        expect(page.locator(".instruction-list li")).to_have_count(2)
        expect(page.locator(".nutrition-estimate-label")).to_have_text("Estimated nutrition")
        page.locator("#serving-count").fill("8")
        page.locator("#serving-count").press("Enter")
        expect(page.locator(".ingredient-quantity").first).to_have_text("3")
        page.reload()
        expect(page.locator("#serving-count")).to_have_value("8")
        page.get_by_role("button", name="Add to meal plan", exact=True).click()
        page.locator("#plan-date").fill(date.today().isoformat())
        expect(page.locator("#plan-servings")).to_have_value("8")
        page.get_by_role("button", name="Add to my week", exact=True).click()
        expect(page.locator(".planned-meal")).to_contain_text("Our lemon pancakes")
        page.get_by_role("link", name="View shopping list", exact=True).click()
        expect(page.locator(".shopping-item").filter(has_text="plain flour")).to_contain_text("3 cups")

        page.goto(base + "/recipes/import/")
        page.locator("#id_text").fill("Soup\nIngredients\nSalt to taste\nMethod\nSimmer gently.")
        page.get_by_role("button", name="Create editable draft").click()
        expect(page.locator(".import-review")).to_contain_text("No exact amount found")
        expect(page.locator("#id_ingredients-0-quantity")).to_have_value("")
        page.locator("#id_title").fill("Corrected soup")
        page.locator("#recipe-form").evaluate("form => form.noValidate = true")
        page.get_by_role("button", name="Save to recipe box", exact=True).click()
        expect(page.locator("#id_title")).to_have_value("Corrected soup")
        expect(page.locator(".ingredient-form .errorlist")).to_contain_text("This field is required")
        expect(page.locator(".import-review")).to_contain_text("See the original pasted recipe")
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(ARTIFACTS / "recipe-import-review-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#id_ingredients-0-quantity").fill("½")
        page.locator("#id_ingredients-0-unit").fill("tsp")
        page.locator("#id_ingredients-0-name").fill("salt")
        page.get_by_role("button", name="Save to recipe box", exact=True).click()
        expect(page.locator("h1")).to_have_text("Corrected soup")

        for path, name in [("/", "recipe-box"), ("/recipes/import/", "recipe-paste"), ("/recipes/new/", "recipe-new")]:
            page.goto(base + path)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), path
            page.screenshot(path=str(ARTIFACTS / f"{name}-import-mobile.png"), full_page=True)

        no_js = browser.new_context(java_script_enabled=False)
        plain = no_js.new_page()
        plain.goto(base + "/recipes/import/")
        plain.locator("#id_text").fill(SOURCE)
        plain.get_by_role("button", name="Create editable draft").click()
        plain.locator("#id_title").fill("No JavaScript pancakes")
        plain.get_by_role("button", name="Save to recipe box", exact=True).click()
        expect(plain.locator("h1")).to_have_text("No JavaScript pancakes")
        no_js.close()
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    run()
