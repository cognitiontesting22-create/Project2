"""Verify exact fractions and cross-recipe shopping conversions in the real UI."""
from datetime import date, timedelta
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

BASE = "http://127.0.0.1:8000"
ARTIFACTS = Path(__file__).resolve().parent.parent / "test-artifacts"

def run():
    ARTIFACTS.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        urls = []
        for title, amounts, units, names in [
            ("Measurement check A", ["1", "500", "1/3"], ["cup", "g", ""], ["Milk", "Flour", "Egg"]),
            ("Measurement check B", ["8", "½", "2/3"], ["tablespoons", "kilograms", "each"], ["milk", "flour", "eggs"]),
        ]:
            page.goto(BASE + "/recipes/new/")
            page.locator('#id_title').fill(title)
            page.locator('#id_description').fill('Temporary recipe for verifying kitchen measurements.')
            page.locator('#id_instructions').fill('Combine the measured ingredients.\nCook and serve.')
            page.locator('#id_measurement_system').select_option('us')
            page.locator('#id_servings').fill('2')
            page.get_by_role('button', name='Add ingredient', exact=True).click()
            for index in range(3):
                page.locator(f'#id_ingredients-{index}-quantity').fill(amounts[index])
                page.locator(f'#id_ingredients-{index}-unit').fill(units[index])
                page.locator(f'#id_ingredients-{index}-name').fill(names[index])
            page.get_by_role('button', name='Save to recipe box').click()
            expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
            urls.append(page.url)

        page.goto(urls[0])
        expect(page.locator('.ingredient-quantity').nth(2)).to_have_text('⅓')
        page.locator('#serving-count').fill('50')
        page.locator('#serving-count').press('Enter')
        expect(page.locator('.ingredient-quantity').nth(2)).to_have_text('8⅓')
        expect(page.locator('#recipe-stat-servings')).to_have_text('50')
        expect(page.locator('.detail-plan-button')).to_have_attribute('data-servings', '50')
        page.reload()
        expect(page.locator('#serving-count')).to_have_value('50')
        expect(page.locator('.ingredient-quantity').nth(2)).to_have_text('8⅓')
        page.screenshot(path=str(ARTIFACTS / 'fraction-scaling-desktop.png'), full_page=True)
        page.locator('#serving-count').fill('0')
        expect(page.locator('#serving-status')).to_have_text('Choose a positive whole number of servings.')
        expect(page.locator('.ingredient-quantity').nth(2)).to_have_text('8⅓')
        page.locator('#serving-count').fill('2')
        page.locator('#serving-count').press('Enter')
        expect(page.locator('.ingredient-quantity').nth(2)).to_have_text('⅓')

        future = date.today() + timedelta(days=180)
        for url, meal in zip(urls, ['Dinner', 'Lunch']):
            page.goto(url)
            page.get_by_role('button', name='Add to meal plan').click()
            page.locator('#plan-date').fill(future.isoformat())
            page.locator('#plan-meal').select_option(meal)
            page.locator('#plan-servings').fill('2')
            page.get_by_role('button', name='Add to my week').click()
            expect(page.locator('.message')).to_contain_text('added to')
        planner_url = page.url
        expect(page.locator('.planned-meal')).to_have_count(2)
        page.get_by_role('link', name='View shopping list').click()
        shopping_url = page.url
        expect(page.locator('.shopping-item')).to_have_count(3)
        milk = page.locator('.shopping-item').filter(has_text='Milk')
        expect(milk.locator('.shopping-quantity')).to_have_text('1½ cups')
        expect(page.locator('.shopping-item').filter(has_text='Flour').locator('.shopping-quantity')).to_have_text('1 kg')
        expect(page.locator('.shopping-item').filter(has_text='Egg').locator('.shopping-quantity')).to_have_text('1')
        expect(milk).to_contain_text('Measurement check A, Measurement check B')
        milk.locator('input').check()
        expect(page.locator('#checked-count')).to_have_text('1')
        page.reload()
        expect(page.locator('.shopping-item').filter(has_text='Milk').locator('input')).to_be_checked()
        with page.expect_download() as download:
            page.get_by_role('link', name='Download list').click()
        text = Path(download.value.path()).read_text()
        assert '1½ cups Milk' in text and '1 kg Flour' in text
        page.screenshot(path=str(ARTIFACTS / 'merged-shopping-desktop.png'), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(ARTIFACTS / 'merged-shopping-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.goto(urls[0] + '?servings=50')
        page.screenshot(path=str(ARTIFACTS / 'fraction-scaling-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        # Delete only these temporary recipes, which also removes their meals.
        for url in urls:
            page.goto(url)
            page.get_by_role('button', name='Remove this recipe').click()
            page.get_by_role('button', name='Remove recipe', exact=True).click()
            expect(page.locator('.message')).to_contain_text('removed')
        assert not errors, errors
        browser.close()
    print('Passed: fraction entry, 50-serving scaling, reload persistence, exact cross-recipe conversions, saved shopping checks, matching downloads, desktop and mobile layouts.')

if __name__ == '__main__':
    run()
