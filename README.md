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
- Choose **Paste a recipe** to turn plain text into an editable draft with a title, ingredient amounts and units, and cooking steps. Review and save it through the normal recipe editor.
- Search recipe names, descriptions, and ingredients. Filter vegetarian, quick, high-protein, sweet, and seasonal recipes; sort by date, name, or cooking time.
- Save favorites and switch between grid and list views. Press **⌘ K** or **Ctrl K** to focus recipe search.
- Type any positive whole serving count or use the +/− controls. Ingredients update as kitchen fractions such as **1½ cups**, **⅓ cup**, and **¾ tsp**, and the chosen count carries into the meal planner.
- Plan breakfast, lunch, and dinner across any week. Choose cooking servings and portions eaten at the meal. If the spare portions cover your household, the next day's empty matching slot becomes expected leftovers.
- Generate a weekly shopping list that merges matching ingredients across recipes, scales each meal’s servings, and converts compatible units. For example, **500 g + ½ kg → 1 kg** and **1 US cup + 8 US tbsp → 1½ cups**. Checkboxes persist, and downloads and printouts use the same readable quantities.
- Keep a pantry with exact amounts, pantry/fridge/freezer locations, optional use-by dates, search, and filters for stock, items to use soon, and empty entries. Adding the same stock lot tops it up; editing sets the actual quantity left.
- Shop for only the shortages: pantry stock is subtracted once from the combined requirements of the week’s uncooked meals. Fully covered ingredients appear separately. **Add checked items to pantry** records the exact listed purchase amounts and updates the list immediately.
- Use **I cooked this** on a recipe or planned meal to review amounts and record your servings, cooking date, and notes. Stock is deducted automatically, and the cooking history keeps a receipt. **Undo & restore pantry** reverses a mistaken record exactly.
- Find meals in **Cook with my pantry**, ranked by how few ingredients are missing for your chosen serving count. Partial stock counts as missing; ready-to-cook recipes have enough of every measured ingredient. Add comma-separated ingredients for a quick search, filter to recipes with all ingredients, or choose **Fewest missing** in the recipe box.
- Enter ingredient quantities as **1½**, **1 1/2**, **1/3**, or decimals. Fractions are stored and calculated exactly, without rounding intermediate quantities.
- Personalize your name, kitchen name, and household portions (1–20, default 2) in Settings.
- See estimated calories, protein, carbohydrates, and fat per serving or for the whole batch. Nutrition recalculates from the local table when servings, ingredients, or food values change.
- Browse and edit the **Nutrition table**, with 57 starter foods from USDA FoodData Central. Add product-label values per 100 g and explicit ingredient matching names, plus optional household portion weights.

## Data and app scope

This is a **personal, single-kitchen application for local use**. There is one shared recipe collection; the application does not include per-user accounts or isolate kitchens. SQLite saves data to `db.sqlite3`, and uploaded photos live in `media/`. Back up both to preserve your kitchen.

The shopping list combines matching ingredient names after normalizing case, whitespace, and a conservative set of singular/plural variants (for example, egg/eggs). Descriptions such as fresh/dried and red/yellow remain distinct. It converts weight units (mg, g, kg, oz, lb) and volume units (ml, l, cups, tablespoons, teaspoons, US fluid ounces, pints, quarts, gallons). Spelled-out units and common abbreviations are accepted. Package counts and unfamiliar units stay separate. **Weight and volume stay separate** because converting flour cups to grams requires an ingredient-specific density; the app does not guess.

**US customary is the default**, as selected for this kitchen: one cup is exactly 236.5882365 ml, 16 tablespoons, or 48 teaspoons. Metric cooking measures use a 250 ml cup, 15 ml tablespoon, and 5 ml teaspoon. Choose the default in Settings and the source standard on each recipe. Changing Settings affects new recipes and shopping-list display; saved recipe quantities retain their source standard. Explicit units such as `US cup`, `metric cup`, or `UK pint` override the recipe setting. Unqualified fluid ounces, pints, quarts, and gallons mean US customary; use `UK` or `imperial` prefixes for British liquid measures.

All conversions and serving calculations use exact rational arithmetic. Common kitchen fractions display as fractions, and metric amounts display as decimals. If a displayed amount needs rounding, **≈** marks it; hover over the quantity for the exact amount. The same notation appears in downloads. Existing decimal amounts survive the database migration, and previous shopping checks carry over to merged rows only when all their original component rows were checked. The fraction-storage migration is intentionally irreversible because reverting to the former two-decimal column would lose exact fractions.

Conversion definitions are based on [NIST’s SI conversion factors](https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors) and [NIST’s metric household measures](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication1038.pdf). No nutrient values or ingredient density estimates are inferred.

### Pantry and cooking behavior

Pantry matching uses the same ingredient names, unit dimensions, exact fractions, and source measuring standards as the shopping list. For example, **½ kg of flour** covers a recipe requiring **500 g of flour**, and **8 US tbsp of milk** covers half of a **1 US cup** requirement. A weight amount cannot cover a volume requirement without a density conversion, and a can cannot cover a whole-item count. Use matching ingredient descriptions and compatible units for reliable coverage.

Stock past its use-by date is visible but excluded from shopping coverage, recipe matching, and cooking deductions. Use-by dates are assessed against today in the kitchen’s Europe/London time zone. Stock dated today is still included; **Use soon** covers the next seven days. Different storage locations, use-by dates, or measuring standards can remain separate stock entries. Cooking uses the earliest dated stock first, then undated stock. Amounts never become negative: ingredients used beyond tracked stock are noted separately on the cooking receipt. Cooking a past meal updates current inventory; it does not reconstruct historical stock.

Weekly lists compare the selected week’s uncooked cooking batches with current stock. Leftover meals contribute no ingredients, recipe titles, or planned-meal count, including when their source is in another week. A recipe serving 2 with 200 g rice, planned at 4 servings plus next-day leftovers, produces one **400 g rice** entry on the page and in the export. Checking ingredients and adding checked purchases use the same requirements. Lists do not reserve inventory across other weeks. Recording a planned batch removes it from that week’s pending requirements; undo restores the consumed stock and makes that batch pending again. Cooked plan slots are protected from replacement or removal until their record is undone. Recording directly from a recipe updates stock without completing a planner slot; use the planner’s **I cooked this** link to complete a planned meal.

Cooking and undo operations are atomic, and repeated submissions do not deduct or restore stock twice. Receipts retain the original recipe title, servings, and exact stock deductions, even if a recipe or pantry entry is later edited or removed. Undo restores the original deduction; if a stock entry was deleted or renamed, it restores a separate entry for the original ingredient. Cooking receipts are read-only in the Django admin so editing them cannot break inventory accounting.

When recording cooking, enter **Portions eaten at this meal** (0 through the actual servings made), choose fridge or freezer under **Store the rest in**, and supply **Use by** whenever you are keeping portions. The use-by date must be on or after the cooking date; the app never supplies a shelf life. If nothing is left, the date is ignored. Planned portions eaten are prefilled from the meal; unplanned meals use household portions capped at the yield.

Every new cooking receipt has one prepared batch, including meals with nothing left. The batch stores the actual yield, eaten and remaining portions, storage location, explicit date, and recipe-title snapshot. History shows, for example, **Made 4 · ate 2 · 2 left in the fridge · use by 5 Nov 2026**. Token retries and double recording a plan keep the original batch unchanged. Undo removes it while restoring raw stock from the receipt; recording the meal again creates a new batch. Existing historical receipts remain intact without inferred portion or use-by data.

Extra ingredients typed into recipe discovery apply only to that search. Their quantities are unknown, so matches clearly ask you to check amounts. Add measured stock to the pantry for quantity-verified **Ready to cook** results. The pantry starts empty so it reflects what you actually own.

### Pasting recipes

**Paste a recipe** is available in the recipe box and on the new-recipe page. Paste one recipe, choose **Create editable draft**, then check and edit the filled-in recipe form. Creating a draft does not save a recipe, add meals, or change pantry stock. Saving uses the existing recipe and ingredient validation, so imported recipes work with exact serving calculations, pantry matching, shopping lists, and nutrition.

The local parser recognizes common headings (Ingredients, Instructions, Directions, Method, Notes), Markdown headings, bullets, numbered steps, wrapped numbered instructions, fractions such as `1½` and `1 1/2`, decimal amounts, common unit names and abbreviations, and explicit measuring standards such as `US cups`. It also reads servings and labeled prep/cook times in hours or minutes. For simple text without headings, it can infer an ingredient block and a method; review those boundaries. Unnumbered method lines become separate steps.

Amounts are never invented. Ranges, approximate measures, and unquantified lines such as “salt to taste” are flagged and leave the amount blank until you resolve them or move the line to notes. Package sizes, alternative weights, and unfamiliar units are also flagged. Preparation details and original ingredient lines remain in cook’s notes; the full pasted text is visible while reviewing, including after a validation error. No API key or external service is needed, and the paste/review/save flow works without JavaScript.

Missing servings start at **2**, missing prep/cook times at **0**, and the draft uses your kitchen’s measuring standard; the review explicitly asks you to check these. Missing title, method, or ingredients remain editable fields. Limits are 30,000 characters, 500 lines, and 100 ingredients per recipe. Larger input is rejected without truncation. This is a conservative text importer, so unusual layouts or prose recipes can require manual edits.

### Personal and household nutrition

In Settings, choose **Just me** or **Household**, add names and optional daily calorie/protein/carbohydrate/fat goals, and save. Save again to add more people. Just me uses the first person; switching modes preserves all profiles and portions. Profiles belong to the shared kitchen and are not separate login accounts.

Select a person in the planner and assign their portions to each meal (decimals such as `1.5` are supported; `0` means they won't eat it). Household cooking servings still drive shopping and pantry use. Combined personal portions cannot exceed the cooking quantity. Changing a meal's recipe or cooking quantity clears its personal portions for reassignment.

Daily cards show nutrition from assigned planned breakfast, lunch, and dinner portions, with neutral differences against goals. Personal portions can be assigned to expected leftover meals too. Snacks and drinks are not included. Missing recipe nutrition or unassigned portions marks totals incomplete and suppresses target differences. Cooking does not record eating. Goals are user-entered; the app does not prescribe targets or define success ranges.

Cooking also saves an exact nutrition-per-portion snapshot on the prepared batch, including calories, protein, carbs, fat, missing values, and complete/partial status. A cooked source's leftover card and its personal planned totals use that snapshot; uncooked leftovers, ordinary planned meals, and recipe pages use live estimates. For example, cooked leftovers keep **130 kcal per portion** after a food-table edit moves the recipe's live estimate to **200 kcal per serving**. Partial estimates keep their label, and missing calories show a dash. Undo removes the snapshot with its batch; cooking again takes a fresh snapshot. Batches created before this feature show missing estimates because their past nutrition cannot be reconstructed reliably.

### Nutrition estimates

The local SQLite nutrition table is initialized automatically by migrations, with 57 reviewed entries extracted from [USDA FoodData Central SR Legacy](https://fdc.nal.usda.gov/download-datasets/). Each entry retains its original source description, FDC identifier, source link, calories, protein, carbohydrate, and fat per 100 g. The frozen source selection and rebuild tool are included in `tools/build_nutrition_seed.py`; the app needs no API key or network connection for calculations. Calories come from the source energy value, rather than being inferred from protein/carbs/fat.

Recipes match foods through explicit normalized names in the table. Dry and cooked rice are distinct. The default generic entries assume whole milk, whole-milk Greek yogurt, dry grains/pasta, full-fat canned coconut milk, and edible raw meat/fish weights. Proxy matches, such as Parmesan composition for vegetarian hard cheese, are recorded in the entry's description and portion notes. Add separate entries and matching names for a different brand or preparation.

Mass measurements convert to grams using the existing exact factors. For nutrition only, volumes and item counts can use food-specific typical weights: grams per US cup, whole item, slice, clove, or can. Typical conversions are labeled in the ingredient breakdown. USDA household cup weights are treated as US customary cups (236.5882365 ml); weights derived from spoons assume 16 tbsp or 48 tsp per cup. Metric and explicit measuring standards are converted before estimating grams. These are household estimates, not precise densities. The sourdough slice uses an explicitly labeled app estimate of 40 g; other starter weights derive from reviewed USDA portions. Unknown package sizes and unsupported units require a measured gram amount. **These weights never change pantry or shopping conversions.**

Ingredients without a food match or usable weight are visibly omitted, with a **Partial estimate** label and a list of gaps. If no ingredient can be calculated, cards show a dash instead of zero. Actual zero-calorie foods remain included. Calculations use exact fractions until display: calories round to whole kcal and macros to 0.1 g; tiny positive values display as `<1` kcal or `<0.1` g. Brands, edible yield, oil left in a pan, cooking losses, and discarded ingredients can affect actual nutrition.

Scaling a recipe increases the entire batch's ingredient amounts and nutrient totals. Nutrition **per serving stays constant** because the batch is divided into proportionally more servings. Editing the recipe's base yield without increasing ingredients changes the per-serving result. Both views and the batch summary update automatically, including after reload and without JavaScript.

### Expected leftovers in the planner

**Household portions** sets how many portions your household usually eats at a meal. In the plan dialog, leave **Portions eaten at this meal** blank to use that number, capped at the cooking servings, or enter a whole number from 1 through the cooking quantity.

With household portions set to 2, planning Monday dinner for 4 servings schedules 2 expected leftover portions for Tuesday dinner if that slot is empty. A 6-serving batch also fills Tuesday only; the other 2 portions stay spare. A 2-serving batch creates no leftover meal. The rule works across week boundaries, never overwrites another plan, and never fills a second day automatically.

Leftover cards link to their source batch and have no cooking action. Once the source is recorded, they show **Leftovers ready** and **I ate this**. Eating creates a consumption receipt and reduces both remaining and reserved portions, without touching raw pantry stock. The card then shows **Eaten**. History lists **Ate leftovers · 2 portions from Monday's dinner**, with **Undo** to restore those portions and their planner reservation.

Source cards distinguish **remaining** (physically left), **reserved** (promised to uneaten planner meals), and **unassigned** (remaining minus reserved). For 4 made and 2 eaten, Tuesday's reservation gives **2 remaining · 2 reserved · 0 unassigned**, becoming all zero after eating. With 6 made and 2 eaten, the balance goes from **4 remaining · 2 reserved · 2 unassigned** to **2 remaining · 0 reserved · 2 unassigned**. These counts are stored and changed under the kitchen lock in an atomic transaction. Cooking refuses an actual remainder smaller than the portions already reserved; change the leftover plan first.

Remove an uneaten leftover card to release its reservation, or replace it with a fresh recipe. Eaten cards require consumption undo before removal or replacement. Undo every active leftover consumption before undoing the source cooking record; cooking undo restores raw stock and returns the cards to **Expected leftovers**, preserving their reservations against the plan. Retry tokens survive undo and batch removal. Source edits reconcile expected leftovers, and removing an uncooked source removes its expected follow-on meal. See [the leftovers design](docs/leftovers-design.md) for the implemented stages and remaining proposal.

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
.venv/bin/python tools/browser_nutrition.py
.venv/bin/python tools/browser_recipe_import.py
.venv/bin/python tools/browser_leftovers.py
```

The browser script uses an installed Google Chrome through Playwright. It checks real UI interactions, persistence, serving calculations, downloads, and mobile layouts, and saves screenshots in `test-artifacts/`. Run it on a fresh demo database: it expects the twelve starter recipes, creates a temporary recipe and future meal slot, and removes them after verification.

The measurements browser test creates two temporary recipes, verifies fraction entry and 50-serving scaling, plans both recipes, and checks the merged shopping list and download. It also captures desktop and mobile screenshots before removing the temporary recipes and their meal slots.

The pantry browser test starts its own temporary SQLite database and local server. It exercises pantry top-ups and edits, expiry filters, recipe matching and extra-ingredient search, weekly stock subtraction, shopping restocking, cooking receipts, undo, and mobile layouts. It removes the temporary kitchen afterward and leaves your main database untouched.

The nutrition browser test also uses an isolated kitchen. It checks food entry/edit/removal, live calories and macros, batch and per-serving scaling, source cup weights, partial estimates, reload persistence, no-JavaScript forms, and mobile layouts. Backend nutrition tests cover exact arithmetic, source standards, count weights, zero versus missing data, explicit matching, table validation, recipe edits, and independence from shopping conversions.

The recipe-import browser test uses an isolated kitchen to exercise paste/review/cancel/save, editable extracted fields, dynamic ingredient rows, exact scaling, nutrition, planner and shopping integration, correction of an invalid draft, mobile layouts, and the no-JavaScript flow. Backend importer tests cover ambiguous quantities, package sizes, units, missing sections, limits, source preservation, HTML escaping, CSRF, and recipe saving.

The leftovers browser test starts its own temporary seeded kitchen and local server. It checks household portions in Settings, the 4/6/2-serving examples, exact card text and source links, repeated saves, removal, replacement, and edit prefills. It also checks explicit use-by validation, the prepared-batch receipt, eating and undo on desktop/mobile, yield 4/6 reservation balances, cooking undo protection, and recording again. The rice and calorie examples cover shopping/export amounts, the leftover card, and personal totals after editing a food. Backend tests additionally cover cross-week shopping exclusions and snapshots, checkbox persistence and restocking, exact fractional nutrients, partial/zero/missing estimates, snapshot stability through food/recipe edits and retries, fresh snapshots after undo, unchanged pantry stock when eating, atomic rollback, migrations, cooked/eaten-plan protections, and personal portions.

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
