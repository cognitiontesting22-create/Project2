"""Exact recipe arithmetic and conservative cooking-unit conversions.

US volume factors derive from a 231 cubic-inch gallon and the exact 25.4 mm
inch. A cup, tablespoon and teaspoon are 8, 1/2 and 1/6 US fluid ounces.
Metric cooking measures use 250 ml, 15 ml and 5 ml respectively.
Sources: https://www.nist.gov/pml/special-publication-811/nist-guide-si-appendix-b-conversion-factors
https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication1038.pdf
Mass and volume remain separate: no ingredient densities are assumed.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext, ROUND_HALF_UP
from fractions import Fraction
import re
import unicodedata

MAX_SERVINGS = 2147483647
MEASUREMENT_SYSTEMS = [("us", "US customary · 236.6 ml cup"), ("metric", "Metric · 250 ml cup")]
VULGAR_FRACTIONS = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5", "⅙": "1/6", "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}
FRACTION_GLYPHS = {Fraction(value): key for key, value in VULGAR_FRACTIONS.items()}
KITCHEN_DENOMINATORS = {2, 3, 4, 5, 6, 8, 10, 12, 16}

def parse_quantity(value, allow_zero=False, max_length=80):
    """Accept positive decimals, simple fractions and mixed kitchen fractions."""
    text = str(value).strip().replace("⁄", "/")
    if not text or len(text) > max_length:
        raise ValueError("Enter an amount such as 1½, 1 1/2, 1/3, or 0.5.")
    for glyph, replacement in VULGAR_FRACTIONS.items():
        text = text.replace(glyph, " " + replacement)
    text = re.sub(r"\s+", " ", text).strip()
    mixed = re.fullmatch(r"(\d+)[ -]+(\d+)\s*/\s*(\d+)", text)
    fraction = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    try:
        if mixed:
            whole, numerator, denominator = map(int, mixed.groups())
            if numerator >= denominator:
                raise ValueError("In a mixed amount, the fraction must be less than one.")
            amount = Fraction(whole) + Fraction(numerator, denominator)
        elif fraction:
            amount = Fraction(int(fraction[1]), int(fraction[2]))
        elif re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", text):
            amount = Fraction(text)
        else:
            raise ValueError("Enter an amount such as 1½, 1 1/2, 1/3, or 0.5.")
    except ZeroDivisionError:
        raise ValueError("A fraction’s denominator cannot be zero.") from None
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError("Ingredient amounts must be greater than zero.")
    if len(str(amount)) > max_length:
        raise ValueError("That amount is too long. Please use a simpler fraction.")
    return amount

def validate_quantity(value):
    from django.core.exceptions import ValidationError
    try:
        parse_quantity(value)
    except ValueError as error:
        raise ValidationError(str(error)) from error

def validate_stock_quantity(value):
    from django.core.exceptions import ValidationError
    try:
        parse_quantity(value, allow_zero=True, max_length=1000)
    except ValueError as error:
        raise ValidationError(str(error)) from error

def normalize_name(name):
    """Merge only explicit singular/plural variants, never fuzzy food names."""
    name = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    plurals = {"eggs": "egg", "onions": "onion", "lemons": "lemon", "limes": "lime", "tomatoes": "tomato", "potatoes": "potato", "carrots": "carrot", "peppers": "pepper", "bananas": "banana", "avocados": "avocado", "mushrooms": "mushroom", "courgettes": "courgette", "zucchinis": "zucchini", "cucumbers": "cucumber", "cloves": "clove", "apples": "apple", "oranges": "orange"}
    head, _, last = name.rpartition(" ")
    return " ".join(part for part in [head, plurals.get(last, last)] if part)

def normalize_unit(unit):
    if unit.strip() == "T":
        return "tbsp"
    return " ".join(unicodedata.normalize("NFKC", unit).casefold().replace(".", "").replace("-", " ").split())

@dataclass(frozen=True)
class Unit:
    code: str
    dimension: str
    factor: Fraction
    label: str

US_FL_OZ = Fraction("29.5735295625")
US_FACTORS = {"tsp": US_FL_OZ / 6, "tbsp": US_FL_OZ / 2, "cup": US_FL_OZ * 8, "fl oz": US_FL_OZ, "pint": US_FL_OZ * 16, "quart": US_FL_OZ * 32, "gallon": US_FL_OZ * 128}
METRIC_FACTORS = {"tsp": Fraction(5), "tbsp": Fraction(15), "cup": Fraction(250)}
ALIASES = {}
def _aliases(code, names):
    for name in names:
        ALIASES[name] = code

_aliases("g", ["g", "gram", "grams", "gramme", "grammes"])
_aliases("kg", ["kg", "kgs", "kilogram", "kilograms", "kilogramme", "kilogrammes"])
_aliases("mg", ["mg", "milligram", "milligrams"])
_aliases("oz", ["oz", "ounce", "ounces"])
_aliases("lb", ["lb", "lbs", "pound", "pounds"])
_aliases("ml", ["ml", "milliliter", "milliliters", "millilitre", "millilitres"])
_aliases("l", ["l", "liter", "liters", "litre", "litres"])
_aliases("tsp", ["t", "tsp", "tsps", "teaspoon", "teaspoons"])
_aliases("tbsp", ["tbsp", "tbsps", "tbs", "tablespoon", "tablespoons"])
_aliases("cup", ["c", "cup", "cups"])
_aliases("fl oz", ["fl oz", "floz", "fluid ounce", "fluid ounces"])
_aliases("pint", ["pt", "pts", "pint", "pints"])
_aliases("quart", ["qt", "qts", "quart", "quarts"])
_aliases("gallon", ["gal", "gals", "gallon", "gallons"])
_aliases("each", ["", "each", "count", "piece", "pieces", "pcs", "whole"])
for unit in ["clove", "slice", "can", "tin", "bunch", "sprig", "pinch", "handful", "package", "packet"]:
    _aliases(unit, [unit, unit + ("es" if unit in {"bunch", "pinch"} else "s")])

def resolve_unit(value, system="us"):
    name = normalize_unit(value)
    explicit_system = None
    for prefix, choice in [("us customary ", "us"), ("us ", "us"), ("metric ", "metric")]:
        if name.startswith(prefix):
            explicit_system, name = choice, name[len(prefix):]
            break
    imperial = name.startswith("uk ") or name.startswith("imperial ")
    if imperial:
        name = name.split(" ", 1)[1]
    code = ALIASES.get(name, name)
    mass = {"mg": Fraction(".001"), "g": Fraction(1), "kg": Fraction(1000), "oz": Fraction("28.349523125"), "lb": Fraction("453.59237")}
    if code in mass and not imperial and not explicit_system:
        return Unit(code, "mass", mass[code], code)
    if code in {"ml", "l"} and not imperial and not explicit_system:
        return Unit(code, "volume", Fraction(1000 if code == "l" else 1), code)
    if code in US_FACTORS:
        choice = explicit_system or system
        if imperial:
            factors = {"fl oz": Fraction("28.4130625"), "pint": Fraction("568.26125"), "quart": Fraction("1136.5225"), "gallon": Fraction("4546.09")}
            if code not in factors:
                return Unit(value, "custom:" + normalize_unit(value), Fraction(1), value.strip())
            return Unit("imperial:" + code, "volume", factors[code], "UK " + code)
        if choice == "metric" and code in METRIC_FACTORS:
            factor = METRIC_FACTORS[code]
        else:
            # Unqualified fluid ounces, pints, quarts and gallons mean US.
            factor = US_FACTORS[code]
        label = ("US " if explicit_system == "us" else "metric " if explicit_system == "metric" else "") + code
        return Unit(choice + ":" + code, "volume", factor, label)
    if code == "each":
        return Unit("each", "count", Fraction(1), "")
    return Unit(code, "custom:" + code, Fraction(1), code)

def display_unit(unit, amount):
    if unit.endswith("cup"):
        return unit + ("s" if amount > 1 else "")
    if unit in {"clove", "slice", "can", "tin", "bunch", "sprig", "pinch", "handful", "package", "packet"} and amount > 1:
        return unit + ("es" if unit in {"bunch", "pinch"} else "s")
    return unit

def decimal_text(value):
    """Round only for presentation; never let a positive amount display as zero."""
    with localcontext() as context:
        context.prec = max(32, len(str(value.numerator)) + len(str(value.denominator)) + 8)
        number = Decimal(value.numerator) / Decimal(value.denominator)
        places = max(3, 2 - number.adjusted()) if number else 3
        rounded = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        text = format(rounded, "f").rstrip("0").rstrip(".") if "." in format(rounded, "f") else format(rounded, "f")
        return ("≈" if Fraction(rounded) != value else "") + text

def format_quantity(value, kitchen=True):
    amount = Fraction(value)
    if amount.denominator == 1:
        return str(amount.numerator)
    if kitchen and amount.denominator in KITCHEN_DENOMINATORS:
        whole, remainder = divmod(amount.numerator, amount.denominator)
        part = Fraction(remainder, amount.denominator)
        glyph = FRACTION_GLYPHS.get(part)
        suffix = glyph or str(part)
        return (str(whole) + ("" if glyph else " ") if whole else "") + suffix
    return decimal_text(amount)

def quantity_display(amount, unit, system="us"):
    amount = Fraction(amount)
    resolved = resolve_unit(unit, system)
    return {"quantity": amount, "amount_display": format_quantity(amount, kitchen=resolved.code not in {"g", "kg", "mg", "ml", "l"}), "unit": display_unit(resolved.label, amount), "exact": str(amount)}

def shopping_display(total, units, system="us"):
    """Choose one practical shopping unit without rounding the underlying total."""
    dimension = units[0].dimension
    if dimension == "mass":
        if any(unit.code in {"g", "kg", "mg"} for unit in units):
            code = "kg" if total >= 1000 else "mg" if total < 1 and all(unit.code == "mg" for unit in units) else "g"
        else:
            code = "lb" if total >= Fraction("453.59237") else "oz"
        target = resolve_unit(code, system)
    elif dimension == "volume":
        if any(unit.code in {"ml", "l"} for unit in units):
            target = resolve_unit("l" if total >= 1000 else "ml", system)
        else:
            # Cups and spoons use the cook’s selected measuring tools.
            targets = [resolve_unit(code, system) for code in ["cup", "tbsp", "tsp"]]
            if any(unit.code.endswith(":cup") for unit in units) and total >= targets[0].factor / 4:
                target = targets[0]
            else:
                target = next((unit for unit in targets if total >= unit.factor), targets[-1])
    else:
        target = units[0]
    amount = total / target.factor
    result = quantity_display(amount, target.label, system)
    result["exact_base"] = str(total)
    return result
