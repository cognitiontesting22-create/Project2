"""Ingredient-aware estimates; household weights never change shopping arithmetic."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
from fractions import Fraction
from .measurements import MAX_SERVINGS, normalize_name, resolve_unit, decimal_text
from .models import NutritionAlias

NUTRIENTS = [("calories", "Calories", "kcal"), ("protein", "Protein", "g"), ("carbs", "Carbs", "g"), ("fat", "Fat", "g")]


def estimate_text(amount, places=1):
    if amount is None:
        return "—"
    amount = Fraction(amount)
    with localcontext() as context:
        context.prec = max(32, len(str(amount.numerator)) + len(str(amount.denominator)) + 8)
        value = Decimal(amount.numerator) / Decimal(amount.denominator)
        step = Decimal(1).scaleb(-places)
        if 0 < value < step / 2:
            return "<" + format(step, "f")
        rounded = value.quantize(step, rounding=ROUND_HALF_UP)
        return format(rounded, "f")


def ingredient_weight(ingredient, food, system):
    """Return base-recipe grams and an explicit conversion assumption, or a gap."""
    amount = ingredient.exact_quantity
    unit = resolve_unit(ingredient.unit, system)
    if unit.dimension == "mass":
        return amount * unit.factor, False, "Measured edible weight"
    if unit.dimension == "volume" and food.cup_grams is not None:
        grams = amount * unit.factor / resolve_unit("cup", "us").factor * Fraction(food.cup_grams)
        return grams, True, f"Typical weight: 1 US cup = {decimal_text(Fraction(food.cup_grams))} g; cup standard converted"
    fields = {"each": ("item_grams", "item"), "slice": ("slice_grams", "slice"), "clove": ("clove_grams", "clove"), "can": ("can_grams", "can")}
    if unit.code in fields:
        field, label = fields[unit.code]
        weight = getattr(food, field)
        if weight is not None:
            return amount * Fraction(weight), True, f"Typical weight: 1 {label} = {decimal_text(Fraction(weight))} g"
    return None, False, "Add a typical weight in the food table, or enter this ingredient in grams."


def recipe_nutrition(recipe, servings, basis="serving"):
    if not isinstance(servings, int) or not 1 <= servings <= MAX_SERVINGS:
        raise ValueError("Choose a positive whole number of servings.")
    basis = "batch" if basis == "batch" else "serving"
    foods = {alias.name: alias.food for alias in NutritionAlias.objects.select_related("food")}
    base_totals = {key: Fraction(0) for key, _, _ in NUTRIENTS}
    rows, missing, estimated_count, matched_count = [], [], 0, 0
    ratio = Fraction(servings, recipe.servings)
    for ingredient in recipe.ingredients.all():
        food = foods.get(normalize_name(ingredient.name))
        row = {"id": ingredient.pk, "name": ingredient.name, "food": food, "status": "missing_food", "estimated": False, "grams": None, "amount_display": "—", "assumption": "No matching food in the local nutrition table."}
        grams = None
        if food is not None:
            grams, estimated, assumption = ingredient_weight(ingredient, food, recipe.measurement_system)
            row.update(status="matched" if grams is not None else "missing_weight", estimated=estimated, assumption=assumption)
        values = None
        if grams is not None:
            matched_count += 1
            estimated_count += row["estimated"]
            values = {key: grams * Fraction(getattr(food, key)) / 100 for key, _, _ in NUTRIENTS}
            for key in base_totals:
                base_totals[key] += values[key]
            row["grams"] = str(grams * ratio)
            row["amount_display"] = estimate_text(grams * ratio)
        else:
            missing.append({"name": ingredient.name, "reason": row["assumption"], "status": row["status"], "food_id": food.pk if food else None})
        row["values"] = [{"key": key, "label": label, "unit": unit, "display": estimate_text(values[key] * ratio if values is not None else None, 0 if key == "calories" else 1)} for key, label, unit in NUTRIENTS]
        rows.append(row)
    per_serving = {key: value / recipe.servings for key, value in base_totals.items()}
    batch = {key: value * ratio for key, value in base_totals.items()}
    selected = batch if basis == "batch" else per_serving
    return {"basis": basis, "servings": servings, "matched_count": matched_count, "total_count": len(rows), "estimated_count": estimated_count,
        "complete": bool(rows) and not missing, "available": matched_count > 0, "missing": missing, "rows": rows,
        "coverage_text": f"{matched_count} of {len(rows)} ingredients included",
        "cards": [{"key": key, "label": label, "unit": unit, "display": estimate_text(selected[key] if matched_count else None, 0 if key == "calories" else 1)} for key, label, unit in NUTRIENTS],
        "per_serving": {key: str(value) if matched_count else None for key, value in per_serving.items()},
        "batch": {key: str(value) if matched_count else None for key, value in batch.items()},
        "serving_calories": estimate_text(per_serving["calories"] if matched_count else None, 0),
        "batch_calories": estimate_text(batch["calories"] if matched_count else None, 0)}
