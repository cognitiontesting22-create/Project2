"""Exercise the real UI in local Chrome; capture desktop and mobile screenshots.

Run against an initialized local development server. Test-created recipes and
meal slots are removed through the same UI after the checks.
"""
from datetime import date, timedelta
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:8000"
ARTIFACTS = Path(__file__).resolve().parent.parent / "test-artifacts"

def run():
    ARTIFACTS.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(BASE)
        page.evaluate("document.fonts.ready")
        expect(page.locator('.recipe-card')).to_have_count(12)
        assert page.evaluate("document.fonts.check('16px Inter') && document.fonts.check('16px Fraunces')")
        assert page.locator('img').evaluate_all("imgs => imgs.every(img => img.complete && img.naturalWidth > 0)")
        page.screenshot(path=str(ARTIFACTS / "recipe-box-desktop.png"), full_page=True)

        heart = page.locator('[data-favorite-url]').first
        original = heart.get_attribute('aria-pressed')
        heart.click()
        expect(heart).to_have_attribute('aria-pressed', 'false' if original == 'true' else 'true')
        page.reload()
        heart = page.locator('[data-favorite-url]').first
        expect(heart).to_have_attribute('aria-pressed', 'false' if original == 'true' else 'true')
        heart.click()
        expect(heart).to_have_attribute('aria-pressed', original)

        page.locator('#recipe-search').fill('garlic')
        page.locator('#recipe-search').press('Enter')
        expect(page).to_have_url(BASE + '/?q=garlic&sort=newest')
        assert page.locator('.recipe-card').count() > 0
        page.get_by_role('button', name='Vegetarian', exact=True).click()
        assert 'filter=vegetarian' in page.url
        expect(page.locator('#recipe-search')).to_have_value('garlic')
        page.goto(BASE)
        page.get_by_role('button', name='List view').click()
        expect(page.locator('#recipe-grid')).to_have_class('recipe-grid list-view')
        page.reload()
        expect(page.locator('#recipe-grid')).to_have_class('recipe-grid list-view')
        page.get_by_role('button', name='Grid view').click()

        page.get_by_role('link', name='Meal planner', exact=True).click()
        page.screenshot(path=str(ARTIFACTS / 'meal-planner-desktop.png'), full_page=True)
        page.goto(BASE)
        page.get_by_role('link', name='Lemon & herb roast chicken', exact=True).click()
        page.screenshot(path=str(ARTIFACTS / 'recipe-detail-desktop.png'), full_page=True)
        expect(page.locator('#serving-count')).to_have_value('4')
        page.get_by_role('button', name='More servings').click()
        expect(page.locator('#serving-count')).to_have_value('5')
        expect(page.locator('.ingredient-quantity').first).to_have_text('1000')
        page.get_by_role('button', name='Add to meal plan').click()
        future = date.today() + timedelta(days=90)
        page.locator('#plan-date').fill(future.isoformat())
        expect(page.locator('#plan-servings')).to_have_value('5')
        page.get_by_role('button', name='Add to my week').click()
        expect(page.locator('.planned-meal')).to_have_count(1)
        expect(page.locator('.planned-meal')).to_contain_text('5 servings')
        page.get_by_role('button', name=f'Change {future:%A} Dinner').click()
        page.locator('#plan-servings').fill('6')
        page.get_by_role('button', name='Add to my week').click()
        expect(page.locator('.planned-meal')).to_have_count(1)
        expect(page.locator('.planned-meal')).to_contain_text('6 servings')
        planner_url = page.url
        page.get_by_role('link', name='View shopping list').click()
        expect(page.locator('.shopping-item')).to_have_count(7)
        expect(page.locator('.shopping-item').filter(has_text='Chicken thighs')).to_contain_text('1.2 kg')
        check = page.locator('[data-shopping-key]').first
        check.check()
        expect(page.locator('#checked-count')).to_have_text('1')
        page.reload()
        expect(page.locator('[data-shopping-key]').first).to_be_checked()
        page.locator('[data-shopping-key]').first.uncheck()
        expect(page.locator('#checked-count')).to_have_text('0')
        with page.expect_download() as download:
            page.get_by_role('link', name='Download list').click()
        assert download.value.suggested_filename.startswith('savor-shopping-')
        page.goto(planner_url)
        page.get_by_role('button', name=f'Remove {future:%A} Dinner').click()
        expect(page.locator('.planned-meal')).to_have_count(0)

        page.goto(BASE + '/recipes/new/')
        page.locator('#id_title').fill('Browser check recipe')
        page.locator('#id_description').fill('A temporary recipe used to verify the real cooking flow.')
        page.locator('#id_instructions').fill('Prepare the ingredients.\nCook and serve.')
        page.locator('#id_ingredients-0-quantity').fill('2')
        page.locator('#id_ingredients-0-unit').fill('tbsp')
        page.locator('#id_ingredients-0-name').fill('Olive oil')
        page.locator('#id_ingredients-1-quantity').fill('1')
        page.locator('#id_ingredients-1-name').fill('Lemon')
        page.get_by_role('button', name='Add ingredient', exact=True).click()
        page.locator('#id_ingredients-2-quantity').fill('1')
        page.locator('#id_ingredients-2-name').fill('Avocado')
        page.get_by_role('button', name='Save to recipe box').click()
        expect(page.get_by_role('heading', name='Browser check recipe', exact=True)).to_be_visible()
        expect(page.locator('.ingredient-list li')).to_have_count(3)
        page.get_by_role('link', name='Edit recipe').click()
        page.locator('#id_title').fill('Browser check recipe updated')
        page.locator('.ingredient-form').filter(has=page.locator('input[value="Avocado"]')).get_by_role('button', name='Remove ingredient').click()
        page.get_by_role('button', name='Save changes').click()
        expect(page.get_by_role('heading', name='Browser check recipe updated', exact=True)).to_be_visible()
        expect(page.locator('.ingredient-list li')).to_have_count(2)
        page.get_by_role('button', name='Remove this recipe').click()
        page.get_by_role('button', name='Remove recipe', exact=True).click()
        expect(page.locator('.recipe-card')).to_have_count(12)

        page.goto(BASE + '/shopping/')
        page.screenshot(path=str(ARTIFACTS / 'shopping-desktop.png'), full_page=True)
        page.goto(BASE + '/recipes/new/')
        page.screenshot(path=str(ARTIFACTS / 'new-recipe-desktop.png'), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        for route, name in [('/', 'recipe-box-mobile'), ('/planner/', 'meal-planner-mobile'), ('/shopping/', 'shopping-mobile'), ('/recipes/new/', 'new-recipe-mobile')]:
            page.goto(BASE + route)
            page.screenshot(path=str(ARTIFACTS / f'{name}.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Horizontal overflow on {route}'
        page.goto(BASE)
        page.get_by_role('button', name='Open menu').click()
        expect(page.locator('#sidebar')).to_have_class('sidebar is-open')
        page.get_by_role('link', name='Favorites', exact=False).click()
        expect(page.get_by_role('heading', name='Worth making again.')).to_be_visible()
        page.get_by_role('button', name='Help', exact=True).click()
        expect(page.locator('#help-dialog')).to_be_visible()
        page.get_by_role('button', name='Let’s get cooking').click()
        expect(page.locator('#help-dialog')).not_to_be_visible()

        assert not errors, errors
        context.close()
        browser.close()
    print(json.dumps({"result": "passed", "checks": ["local fonts and photos", "favorites and persistence", "ingredient search and filters", "list and grid views", "serving scaling", "meal creation and replacement", "shopping quantities and saved checks", "shopping download", "recipe creation, editing, and deletion", "mobile layout and navigation", "help dialog", "no browser console errors"], "screenshots": str(ARTIFACTS)}, indent=2))

if __name__ == '__main__':
    run()
