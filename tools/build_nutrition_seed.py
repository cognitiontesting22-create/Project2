"""Rebuild the frozen local table from USDA's SR Legacy CSV archive.

Usage: .venv/bin/python tools/build_nutrition_seed.py /path/to/usda.zip
Download: https://fdc.nal.usda.gov/download-datasets/ (SR Legacy, April 2018 CSV).
Nutrients are copied, not inferred from a 4/4/9 energy formula. Portion indexes
identify reviewed household weights. Cup weights derived from spoons assume
16 tablespoons or 48 teaspoons per US cup; these are nutrition estimates only.
"""
import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import zipfile

# name, FDC ID, explicit aliases, cup portion index, whole-item portion index,
# extra weights (reviewed USDA portion or a clearly labeled app estimate), notes.
SELECTION = [
    ("Chicken thigh, raw", "172385", ["chicken thighs", "chicken thigh"], None, 1, {}, "Meat and skin. Listed weight is treated as edible, boneless weight; bones and cooking losses are not counted."),
    ("Lemon, without peel", "167746", ["lemons", "lemon"], 0, 1, {}, "58 g edible fruit. The estimate counts the listed lemon, including flesh, even if you use only its juice."),
    ("Olive oil", "171413", ["olive oil"], 1, None, {}, "Household cup: 216 g."),
    ("Garlic, raw", "169230", ["garlic"], 0, None, {"clove_grams": "3"}, "1 USDA clove: 3 g."),
    ("Thyme, fresh", "173470", ["fresh thyme"], 0, None, {}, "Cup weight derived from a 0.8 g teaspoon."),
    ("Table salt", "173468", ["salt", "table salt"], 2, None, {}, "A measured zero-calorie ingredient; sodium is not part of this macro estimate."),
    ("Black pepper, ground", "170931", ["black pepper"], 0, None, {}, "Ground pepper household weight."),
    ("Quinoa, dry", "168874", ["quinoa", "dry quinoa", "uncooked quinoa"], 0, None, {}, "Dry grain, before adding cooking water."),
    ("Avocado, raw", "171705", ["avocado", "avocados"], 0, 3, {}, "1 USDA avocado: 201 g edible flesh; cup assumes cubes. Sizes vary."),
    ("Spinach, raw", "168462", ["spinach", "baby spinach"], 0, None, {}, "Loosely packed leaves."),
    ("Cucumber, with peel", "168409", ["cucumber"], 0, 1, {}, "1 USDA cucumber: 301 g, approximately 8¼ inches long."),
    ("Greek yogurt, whole milk", "171304", ["Greek yogurt", "whole milk Greek yogurt"], None, None, {}, "Whole-milk plain yogurt. Choose a different entry for nonfat or sweetened yogurt."),
    ("Parsley, fresh", "170416", ["fresh parsley", "parsley"], 0, None, {}, "Cup assumes chopped parsley."),
    ("Pasta, dry", "169736", ["pasta", "dry pasta", "uncooked pasta"], 2, None, {}, "Dry enriched pasta. Cup assumes elbows; shape changes the weight."),
    ("Basil, fresh", "172232", ["fresh basil", "basil"], 2, None, {}, "Cup assumes whole leaves."),
    ("Pine nuts", "170591", ["pine nuts"], 0, None, {}, "Dried pine nuts."),
    ("Parmesan-style hard cheese", "170848", ["vegetarian hard cheese", "parmesan", "hard cheese"], None, None, {}, "Parmesan composition is a proxy for vegetarian hard cheese; brands vary."),
    ("Tomato, raw", "170457", ["tomato", "tomatoes", "raw tomatoes"], 1, 5, {"slice_grams": "20"}, "1 medium whole tomato: 123 g. Cup assumes chopped or sliced."),
    ("Cherry tomatoes", "170457", ["cherry tomatoes", "cherry tomato"], 0, 3, {}, "1 cherry tomato: 17 g; uses the USDA red-tomato composition."),
    ("Atlantic salmon, raw", "175167", ["salmon fillets", "salmon", "salmon fillet"], None, None, {}, "Farmed Atlantic salmon, raw edible weight."),
    ("Honey", "169640", ["honey"], 0, None, {}, "USDA household cup: 339 g."),
    ("Soy sauce", "174277", ["soy sauce"], 2, None, {}, "Standard shoyu, made with soy and wheat."),
    ("Broccoli, raw", "170379", ["broccoli"], 0, None, {}, "Cup assumes chopped broccoli."),
    ("White rice, dry", "169756", ["rice", "white rice", "dry rice", "uncooked rice"], 0, None, {}, "Dry long-grain rice; cooking water is not counted."),
    ("White rice, cooked", "169757", ["cooked rice", "cooked white rice"], 0, None, {}, "Cooked without salt. Kept separate from dry rice."),
    ("Brown rice, dry", "169703", ["brown rice", "dry brown rice"], 0, None, {}, "Dry long-grain brown rice."),
    ("Sourdough bread", "172675", ["sourdough bread", "sourdough"], None, None, {"slice_grams": "40"}, "App estimate: 40 g per toast slice. USDA French/Vienna bread composition includes sourdough; weigh your slice or use its label."),
    ("Egg, whole, raw", "171287", ["eggs", "egg", "whole egg"], 3, 0, {}, "1 large egg: 50 g edible contents, excluding the shell."),
    ("Red chili, dried", "170932", ["chili flakes", "chilli flakes", "cayenne pepper"], 0, None, {}, "Cayenne composition and teaspoon weight are a proxy for chili flakes."),
    ("Plain wheat flour", "168894", ["plain flour", "flour", "all-purpose flour"], 0, None, {}, "All-purpose white flour, enriched. Cup assumes 125 g."),
    ("Baking powder", "172804", ["baking powder"], 0, None, {}, "Double-acting baking powder; cup weight derived from a 4.6 g teaspoon."),
    ("White sugar", "169655", ["sugar", "white sugar", "granulated sugar"], 2, None, {}, "Granulated sugar."),
    ("Whole milk", "171265", ["milk", "whole milk"], 0, None, {}, "3.25% milkfat. USDA household cup: 244 g."),
    ("Butter, unsalted", "173430", ["butter", "unsalted butter"], 2, None, {}, "USDA household cup: 227 g."),
    ("Blueberries, raw", "171711", ["blueberries", "blueberry"], 0, None, {}, "USDA household cup: 148 g."),
    ("Maple syrup", "169661", ["maple syrup"], 2, None, {}, "Pure maple syrup; household cup: 315 g."),
    ("Risotto rice, dry", "169760", ["arborio rice", "risotto rice"], 0, None, {}, "Dry medium-grain white rice composition is used for arborio rice."),
    ("White mushrooms, raw", "169251", ["mushrooms", "mushroom"], 0, 3, {"slice_grams": "6"}, "1 medium mushroom: 18 g. Cup assumes pieces or slices."),
    ("Onion, raw", "170000", ["onion", "onions", "red onion"], 0, 5, {}, "1 medium onion: 110 g. Generic raw onion composition also estimates red onion."),
    ("Vegetable stock, prepared", "171583", ["vegetable stock", "vegetable broth"], 0, None, {}, "Ready-to-serve broth. Concentrated stock cubes need their own entry."),
    ("Chickpeas, cooked", "173757", ["cooked chickpeas", "cooked chickpea"], 0, None, {}, "Boiled without salt; not dry chickpeas."),
    ("Coconut milk, canned", "170173", ["coconut milk", "canned coconut milk"], 0, None, {}, "Full-fat canned coconut milk, not a diluted coconut drink."),
    ("Tomatoes, canned", "170051", ["chopped tomatoes", "canned tomatoes", "tinned tomatoes"], 0, None, {}, "Packed in tomato juice. Can sizes vary, so no default can weight is assumed."),
    ("Ginger, fresh", "169231", ["fresh ginger", "ginger"], 0, None, {}, "Cup weight derived from a 2 g teaspoon."),
    ("Curry powder", "170924", ["curry powder"], 1, None, {}, "Cup weight derived from a 6.3 g tablespoon."),
    ("Banana, raw", "173944", ["ripe bananas", "bananas", "banana"], 0, 4, {}, "1 medium banana: 118 g edible flesh. Cup assumes mashed banana."),
    ("Brown sugar", "168833", ["brown sugar"], 1, None, {}, "Cup assumes packed brown sugar."),
    ("Cinnamon, ground", "171320", ["ground cinnamon", "cinnamon"], 0, None, {}, "Cup weight derived from a 2.6 g teaspoon."),
    ("Vanilla extract", "173471", ["vanilla extract"], 2, None, {}, "Contains alcohol; source calories need not equal a 4/4/9 macro calculation."),
    ("Small corn tortillas", "175036", ["small tortillas", "corn tortillas", "corn tortilla"], None, 2, {}, "1 USDA corn tortilla: 24 g. Flour tortillas need a different entry."),
    ("Bell pepper, raw", "170108", ["bell peppers", "bell pepper", "red bell pepper"], 0, 4, {}, "1 medium red pepper: 119 g; this is the default estimate for an unspecified bell pepper."),
    ("Courgette, raw", "169291", ["courgette", "zucchini"], 0, 3, {"slice_grams": "9.9"}, "1 medium courgette: 196 g, with skin."),
    ("Black beans, cooked", "173735", ["cooked black beans", "cooked black bean"], 0, None, {}, "Boiled without salt; not dry black beans."),
    ("Lime, raw", "168155", ["lime", "limes"], None, 0, {}, "1 lime: 67 g edible fruit. The estimate counts the flesh even if only juice is used."),
    ("Paprika", "171329", ["smoked paprika", "paprika"], 0, None, {}, "Ground paprika composition also estimates smoked paprika."),
    ("Cumin, ground", "170923", ["ground cumin", "cumin"], 0, None, {}, "Cumin seed composition and household weight estimate ground cumin."),
    ("Semisweet chocolate", "167976", ["chocolate chips", "semisweet chocolate"], None, None, {}, "Generic semisweet chocolate estimates baking chips; use a label entry for a specific brand."),
]


def build(archive):
    with zipfile.ZipFile(archive) as bundle:
        def rows(filename):
            path = next(name for name in bundle.namelist() if name.endswith("/" + filename))
            return list(csv.DictReader(io.TextIOWrapper(bundle.open(path))))
        foods = {row["fdc_id"]: row for row in rows("food.csv")}
        nutrients = {}
        codes = {"1008": "calories", "1003": "protein", "1005": "carbs", "1004": "fat"}
        for row in rows("food_nutrient.csv"):
            if row["nutrient_id"] in codes:
                nutrients.setdefault(row["fdc_id"], {})[codes[row["nutrient_id"]]] = row["amount"]
        portions = {}
        for row in rows("food_portion.csv"):
            portions.setdefault(row["fdc_id"], []).append(row)
        result = []
        for name, food_id, aliases, cup_index, item_index, extra, note in SELECTION:
            values = nutrients[food_id]
            assert set(values) == set(codes.values()) and all(values.values()), food_id
            entry = {"name": name, "description": foods[food_id]["description"], **values, "source_label": f"USDA SR Legacy · FDC {food_id}", "source_url": f"https://fdc.nal.usda.gov/food-details/{food_id}/nutrients", "aliases": aliases, "portion_notes": note, **extra}
            if cup_index is not None:
                portion = portions[food_id][cup_index]
                per_unit = Decimal(portion["gram_weight"]) / Decimal(portion["amount"])
                modifier = portion["modifier"]
                multiplier = 48 if "tsp" in modifier else 16 if "tbsp" in modifier or "tablespoon" in modifier else 1
                assert "cup" in modifier or multiplier != 1, (name, modifier)
                entry["cup_grams"] = str(per_unit * multiplier)
            if item_index is not None:
                portion = portions[food_id][item_index]
                entry["item_grams"] = str(Decimal(portion["gram_weight"]) / Decimal(portion["amount"]))
            result.append(entry)
    destination = Path(__file__).resolve().parent.parent / "kitchen/data/nutrition_usda_sr2018_v1.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(result)} reviewed foods to {destination}")


if __name__ == "__main__":
    build(sys.argv[1])
