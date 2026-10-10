# Savor

A personal recipe box and meal planner, built with **Django 5.2 and SQLite**. Savor has a warm, responsive interface, locally served food photography, and a starter collection of twelve recipes.

## Run locally

Python 3.10 or newer is required. This project was tested with Python 3.12.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open **http://127.0.0.1:8000**.

The virtual environment, database, and starter collection have already been initialized in this workspace. To start it again, use:

```sh
.venv/bin/python manage.py runserver
```

To start with your own collection, run migrations and skip `seed_demo`. Use `seed_demo --recipes-only` to add the recipes without the example meal plan. Seeding is explicit and does not overwrite existing recipes or filled meal slots.

## What you can do

- Browse twelve starter recipes with photographs, measured ingredients, cooking steps, and cook’s notes.
- Add, edit, and delete recipes, including your own uploaded photo or an image URL.
- Search recipe names, descriptions, and ingredients. Filter vegetarian, quick, high-protein, sweet, and seasonal recipes; sort by date, name, or cooking time.
- Save favorites and switch between grid and list views. Press **⌘ K** or **Ctrl K** to focus recipe search.
- Type any positive whole serving count or use the +/− controls. Ingredients update as kitchen fractions such as **1½ cups**, **⅓ cup**, and **¾ tsp**, and the chosen count carries into the meal planner.
- Plan breakfast, lunch, and dinner across any week. Choose servings for each meal, replace a filled slot, or remove it.
- Generate a weekly shopping list that merges matching ingredients across recipes, scales each meal’s servings, and converts compatible units. For example, **500 g + ½ kg → 1 kg** and **1 US cup + 8 US tbsp → 1½ cups**. Checkboxes persist, and downloads and printouts use the same readable quantities.
- Keep a pantry with exact amounts, pantry/fridge/freezer locations, optional use-by dates, search, and filters for stock, items to use soon, and empty entries. Adding the same stock lot tops it up; editing sets the actual quantity left.
- Shop for only the shortages: pantry stock is subtracted once from the combined requirements of the week’s uncooked meals. Fully covered ingredients appear separately. **Add checked items to pantry** records the exact listed purchase amounts and updates the list immediately.
- Use **I cooked this** on a recipe or planned meal to review amounts and record your servings, cooking date, and notes. Stock is deducted automatically, and the cooking history keeps a receipt. **Undo & restore pantry** reverses a mistaken record exactly.
- Find meals in **Cook with my pantry**, ranked by how few ingredients are missing for your chosen serving count. Partial stock counts as missing; ready-to-cook recipes have enough of every measured ingredient. Add comma-separated ingredients for a quick search, filter to recipes with all ingredients, or choose **Fewest missing** in the recipe box.
- Enter ingredient quantities as **1½**, **1 1/2**, **1/3**, or decimals. Fractions are stored and calculated exactly, without rounding intermediate quantities.
- Personalize your name and kitchen name in Settings.

## Data and app scope

This is a **personal, single-kitchen application for local use**. There is one shared recipe collection; the application does not include per-user accounts or isolate kitchens. SQLite saves data to `db.sqlite3`, and uploaded photos live in `media/`. Back up both to preserve your kitchen.

The shopping list combines matching ingredient names after normalizing case, whitespace, and a conservative set of singular/plural variants (for example, egg/eggs). Descriptions such as fresh/dried and red/yellow remain distinct. It converts weight units (mg, g, kg, oz, lb) and volume units (ml, l, cups, tablespoons, teaspoons, US fluid ounces, pints, quarts, gallons). Spelled-out units and common abbreviations are accepted. Package counts and unfamiliar units stay separate. **Weight and volume stay separate** because converting flour cups to grams requires an ingredient-specific density; the app does not guess.

**US customary is the default**, as selected for this kitchen: one cup is exactly 236.5882365 ml, 16 tablespoons, or 48 teaspoons. Metric cooking measures use a 250 ml cup, 15 ml tablespoon, and 5 ml teaspoon. Choose the default in Settings and the source standard on each recipe. Changing Settings affects new recipes and shopping-list display; saved recipe quantities retain their source standard. Explicit units such as `US cup`, `metric cup`, or `UK pint` override the recipe setting. Unqualified fluid ounces, pints, quarts, and gallons mean US customary; use `UK` or `imperial` prefixes for British liquid measures.

All conversions and serving calculations use exact rational arithmetic. Common kitchen fractions display as fractions, and metric amounts display as decimals. If a displayed amount needs rounding, **≈** marks it; hover over the quantity for the exact amount. The same notation appears in downloads. Existing decimal amounts survive the database migration, and previous shopping checks carry over to merged rows only when all their original component rows were checked. The fraction-storage migration is intentionally irreversible because reverting to the former two-decimal column would lose exact fractions.

Conversion definitions are based on [NIST’s SI conversion factors](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors) and [NIST’s metric household measures](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication1038.pdf). No nutrient values or ingredient density estimates are inferred.

### Pantry and cooking behavior

Pantry matching uses the same ingredient names, unit dimensions, exact fractions, and source measuring standards as the shopping list. For example, **½ kg of flour** covers a recipe requiring **500 g of flour**, and **8 US tbsp of milk** covers half of a **1 US cup** requirement. A weight amount cannot cover a volume requirement without a density conversion, and a can cannot cover a whole-item count. Use matching ingredient descriptions and compatible units for reliable coverage.

Stock past its use-by date is visible but excluded from shopping coverage, recipe matching, and cooking deductions. Use-by dates are assessed against today in the kitchen’s Europe/London time zone. Stock dated today is still included; **Use soon** covers the next seven days. Different storage locations, use-by dates, or measuring standards can remain separate stock entries. Cooking uses the earliest dated stock first, then undated stock. Amounts never become negative: ingredients used beyond tracked stock are noted separately on the cooking receipt. Cooking a past meal updates current inventory; it does not reconstruct historical stock.

Weekly lists compare the selected week’s remaining meals with current stock. They do not reserve inventory across other weeks. Recording a planned meal removes it from that week’s pending requirements; undo restores the consumed stock and makes that planned meal pending again. Cooked plan slots are protected from replacement or removal until their record is undone. Recording directly from a recipe updates stock without completing a planner slot; use the planner’s **I cooked this** link to complete a planned meal.

Cooking and undo operations are atomic, and repeated submissions do not deduct or restore stock twice. Receipts retain the original recipe title, servings, and exact stock deductions, even if a recipe or pantry entry is later edited or removed. Undo restores the original deduction; if a stock entry was deleted or renamed, it restores a separate entry for the original ingredient. Cooking receipts are read-only in the Django admin so editing them cannot break inventory accounting.

Extra ingredients typed into recipe discovery apply only to that search. Their quantities are unknown, so matches clearly ask you to check amounts. Add measured stock to the pantry for quantity-verified **Ready to cook** results. The pantry starts empty so it reflects what you actually own.

Images and fonts are included under `static/`, so the starter experience does not require an external image or font service at runtime. Photos are illustrative. Your own external image URLs still need an internet connection.

## Tests

```sh
.venv/bin/python manage.py test
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
```

The Django tests cover page rendering, ingredient search, recipe creation/editing and validation, favorites and CSRF protection, meal-slot replacement, exact fraction parsing and formatting, unit conversions and aliases, source measuring standards, shopping quantity calculations and checks, migration of previous checkboxes, exports, deletion, settings, and week navigation. Inventory tests additionally cover partial stock, expiry, pooled weekly subtraction, discovery ranking and servings, purchase restocking, cooking retries, exact undo, modified/deleted stock entries, persistent history, cooked plan protection, and native date fields.

For the browser smoke test, install the development requirements, start the server, and run:

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/browser_smoke.py
.venv/bin/python tools/browser_measurements.py
.venv/bin/python tools/browser_pantry.py
```

The browser script uses an installed Google Chrome through Playwright. It checks real UI interactions, persistence, serving calculations, downloads, and mobile layouts, and saves screenshots in `test-artifacts/`. Run it on a fresh demo database: it expects the twelve starter recipes, creates a temporary recipe and future meal slot, and removes them after verification.

The measurements browser test creates two temporary recipes, verifies fraction entry and 50-serving scaling, plans both recipes, and checks the merged shopping list and download. It also captures desktop and mobile screenshots before removing the temporary recipes and their meal slots.

The pantry browser test starts its own temporary SQLite database and local server. It exercises pantry top-ups and edits, expiry filters, recipe matching and extra-ingredient search, weekly stock subtraction, shopping restocking, cooking receipts, undo, and mobile layouts. It removes the temporary kitchen afterward and leaves your main database untouched.

## Project structure

- `savor/`: Django settings and URL configuration.
- `kitchen/`: models, forms, views, migrations, demo data, and backend tests.
- `templates/kitchen/`: server-rendered pages and shared components.
- `static/`: CSS, vanilla JavaScript, fonts, and photographs.
- `tools/`: asset downloader and browser verification.

The front end uses native HTML dialogs, inline SVG icons, and vanilla JavaScript. There is no JavaScript build step or external API requirement.

## Admin and deployment

Create an optional Django admin user with `python manage.py createsuperuser`, then visit `/admin/`.

The default configuration is for local development. If deploying, provide `DJANGO_SECRET_KEY`, set `DJANGO_DEBUG=0`, and set `DJANGO_ALLOWED_HOSTS` to the deployment hostnames. Run `collectstatic`, serve static files and uploaded media through the deployment platform, and run `savor.wsgi:application` with a WSGI server behind HTTPS. Since the kitchen routes have no login requirement, keep a hosted deployment private behind access control or add user authentication before exposing personal data publicly.

## Asset credits

Demo photography is downloaded from [Unsplash](https://unsplash.com/license) and [Pexels](https://www.pexels.com/license/); the photo identifiers and source URLs are recorded in `tools/fetch_assets.py`. Inter and Fraunces are from [Google Fonts](https://github.com/google/fonts), and their SIL Open Font License files are included in `static/fonts/`.
