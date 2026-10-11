"""Conservative, offline parsing of pasted recipes into an editable draft.

Never invent ingredient amounts. The original text accompanies the preview,
and uncertain lines remain editable rows or notes rather than being discarded.
"""
import re
from .measurements import ALIASES, MAX_SERVINGS, VULGAR_FRACTIONS, normalize_unit, parse_quantity

MAX_TEXT_LENGTH = 30000
MAX_LINES = 500
MAX_INGREDIENTS = 100
FRACTIONS = "".join(VULGAR_FRACTIONS)
QUANTITY = re.compile(
    rf"^(\d+\s+\d+\s*[/⁄]\s*\d+|\d+\s*-\s*\d+\s*[/⁄]\s*\d+|"
    rf"\d+\s*[/⁄]\s*\d+|\d*\s*[{FRACTIONS}]|(?:\d+(?:\.\d+)?|\.\d+))"
)
NUMBERED = re.compile(r"^(?:step\s+)?\d+[.)]\s+|^step\s+\d+\s*:\s*", re.I)
COMMAND = re.compile(r"^(?:preheat|heat|mix|stir|whisk|combine|add|pour|place|put|cook|bake|roast|"
                     r"boil|simmer|fry|saute|sauté|season|serve|chop|slice|dice|peel|drain|"
                     r"rinse|wash|toss|fold|beat|blend|melt|let|allow|transfer|remove|cover|"
                     r"bring|set|spread|knead|roll|brush|sprinkle|cool|marinate|grill|enjoy)\b", re.I)
SECTIONS = {
    "ingredients": "ingredients", "ingredient list": "ingredients", "what you need": "ingredients",
    "instructions": "steps", "directions": "steps", "method": "steps", "steps": "steps",
    "preparation": "steps", "how to make it": "steps",
    "notes": "notes", "cook's notes": "notes", "tips": "notes", "serving suggestions": "notes",
}
COUNT_FOODS = set("egg eggs onion onions lemon lemons lime limes tomato tomatoes potato potatoes "
                  "carrot carrots pepper peppers banana bananas avocado avocados apple apples "
                  "orange oranges cucumber cucumbers zucchini courgette courgettes".split())


def clean_line(line):
    line = re.sub(r"^\s*#{1,6}\s*", "", line.strip())
    line = line.replace("**", "").replace("__", "")
    return re.sub(r"^(?:[-*+•▪‣])\s*", "", line).strip()


def section(line):
    head, separator, tail = line.partition(":")
    key = head.strip().casefold() if separator else line.rstrip(":").casefold()
    return SECTIONS.get(key), tail.strip() if separator else ""


def extract_unit(text):
    # Longest recognized prefix, including explicit US/metric/UK measures.
    words = text.split()
    for size in range(min(5, len(words)), 0, -1):
        candidate = " ".join(words[:size]).rstrip(",")
        normalized = normalize_unit(candidate)
        for prefix in ("us customary ", "us ", "metric ", "uk ", "imperial "):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
                break
        if normalized in ALIASES and normalized:
            code = ALIASES[normalized]
            # Retain explicit standards and case-sensitive T (tablespoon).
            unit = candidate if normalize_unit(candidate) != normalized else ("tbsp" if candidate == "T" else code)
            return unit, " ".join(words[size:]).strip()
    return "", text


def parse_ingredient(line):
    source = clean_line(line)
    text = NUMBERED.sub("", source)
    row = {"quantity": "", "unit": "", "name": text, "source": source, "warning": ""}
    match = QUANTITY.match(text)
    if not match:
        row["warning"] = "No exact amount found. Add an amount or move this line to cook’s notes."
        return row
    quantity = match[1].strip()
    rest = text[match.end():].strip()
    # Ranges, alternatives and approximations must be resolved by the cook.
    if re.match(r"^(?:[-–—]|to\b|or\b)", rest, re.I):
        row["warning"] = "This amount is a range or choice. Choose an exact amount."
        return row
    try:
        row["quantity"] = str(parse_quantity(quantity))
    except ValueError:
        row["warning"] = "Check this amount and enter a positive decimal or fraction."
        return row
    # A parenthesized package size is preserved for review, not mistaken for yield.
    package = re.match(r"^\(([^)]+)\)\s*", rest)
    if package:
        rest = rest[package.end():]
        row["warning"] = "Package size found. Check the unit and amount against the original line."
    row["unit"], name = extract_unit(rest)
    if row["unit"] and re.match(r"^(?:[/=]|or\b|\d|\([^)]*\d)", name, re.I):
        row["warning"] = "Another measure or choice follows this amount. Check which measure to use."
    name = name.split(",", 1)[0].strip()
    if not row["unit"]:
        # Also accept “2 garlic cloves”; avoid splitting “cloves of garlic”.
        trailing = re.fullmatch(r"(.+?)\s+(cloves|slices|cans|tins)", name, re.I)
        if trailing:
            name, row["unit"] = trailing[1], ALIASES[trailing[2].casefold()]
    name = re.sub(r"^of\s+", "", name, flags=re.I)
    # Preparation stays in the saved import notes; the food name stays matchable.
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if not row["unit"]:
        name = re.sub(r"^(?:small|medium|large)\s+(?=eggs?\b)", "", name, flags=re.I)
    row["name"] = name
    if not name:
        row["warning"] = "No ingredient name found. Add its name."
    elif re.match(r"^(?:about|approximately|heaped|heaping|rounded|scant)\b", rest, re.I):
        row.update(quantity="", unit="", name=text)
        row["warning"] = "This measure needs checking. Enter an exact amount and unit."
    elif not row["unit"] and re.match(r"^[A-Za-z]+\.?\s+", rest) and name.split()[-1].casefold() not in COUNT_FOODS and not row["warning"]:
        # Bare counts are valid, but an unfamiliar unit might be the first word.
        row["warning"] = "Read as a whole-item count. Check whether the first word is a unit."
    return row


def parse_recipe(text, measurement_system="us"):
    if not text.strip():
        raise ValueError("Paste a recipe first.")
    if len(text) > MAX_TEXT_LENGTH or len(text.splitlines()) > MAX_LINES:
        raise ValueError("Paste one recipe at a time, up to 30,000 characters and 500 lines.")
    initial = {"title": "", "description": "", "category": "Dinner", "prep_time": 0,
               "cook_time": 0, "servings": 2, "measurement_system": measurement_system}
    ingredients, steps, notes, description, warnings = [], [], [], [], []
    mode, seen, paragraph, numbered_step = None, set(), [], False

    def flush_step():
        if paragraph:
            steps.append(" ".join(paragraph))
            paragraph.clear()

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = clean_line(raw)
        if not line:
            flush_step()
            continue
        heading, remainder = section(line)
        if heading:
            flush_step()
            mode = heading
            numbered_step = False
            if not remainder:
                continue
            line = remainder
        title = re.match(r"^(?:title|recipe)\s*:\s*(.+)$", line, re.I)
        if title and mode is None:
            initial["title"] = title[1].strip()
            continue
        servings = re.match(r"^(?:servings?|serves|yield|makes)\s*:?\s*(\d+)\s*(?:servings?|portions?|people)?\s*$", line, re.I)
        if servings and mode != "steps":
            value = int(servings[1]) if len(servings[1]) <= 10 else 0
            if 1 <= value <= MAX_SERVINGS:
                initial["servings"] = value
                seen.add("servings")
                continue
        timing = re.match(r"^(prep(?:aration)?|cook(?:ing)?)\s*(?:time)?\s*:\s*(.+)$", line, re.I)
        if timing and mode != "steps":
            value = timing[2].strip().casefold()
            duration = re.fullmatch(r"(?:(\d+)\s*(?:hours?|hrs?|h)\s*)?(?:(\d+)\s*(?:minutes?|mins?|m))?", value)
            minutes = (int(duration[1] or 0) * 60 + int(duration[2] or 0)) if duration and any(duration.groups()) and all(len(part or "") <= 4 for part in duration.groups()) else None
            if minutes is not None and 0 <= minutes <= 600:
                key = "prep_time" if timing[1].casefold().startswith("prep") else "cook_time"
                initial[key] = minutes
                seen.add(key)
            else:
                notes.append(line)
                warnings.append("A time could not be read exactly; check prep and cook times.")
            continue
        unnumbered = NUMBERED.sub("", line)
        if mode == "notes":
            notes.append(line)
        elif mode == "steps":
            if NUMBERED.match(line):
                flush_step()
                numbered_step = True
            elif not numbered_step:
                flush_step()
            paragraph.append(unnumbered)
        elif mode == "ingredients":
            if re.match(r"^(?:for\s+.+|.+ sauce|.+ topping|.+ filling):$", line, re.I):
                notes.append("Ingredient section: " + line)
            elif COMMAND.match(unnumbered):
                mode = "steps"
                paragraph.append(unnumbered)
                warnings.append("The method was inferred without a heading. Check where ingredients end.")
            else:
                ingredients.append(parse_ingredient(line))
        elif not initial["title"] and not QUANTITY.match(unnumbered):
            initial["title"] = line
        elif COMMAND.match(unnumbered):
            mode = "steps"
            paragraph.append(unnumbered)
        elif QUANTITY.match(unnumbered):
            mode = "ingredients"
            ingredients.append(parse_ingredient(line))
        elif not initial["title"]:
            initial["title"] = line
        else:
            description.append(line)
    flush_step()
    if len(ingredients) > MAX_INGREDIENTS:
        raise ValueError("This recipe has more than 100 ingredient lines. Split it into smaller recipes.")
    if not initial["title"]:
        warnings.append("Add a recipe title.")
    if not ingredients:
        warnings.append("No ingredient list found. Add ingredients in the draft below.")
    if not steps:
        warnings.append("No method found. Add the cooking steps below.")
    if "servings" not in seen:
        warnings.append("Servings were not found. The draft starts at 2; set the recipe’s actual yield.")
    if "prep_time" not in seen or "cook_time" not in seen:
        warnings.append("Any time not found is set to 0 minutes. Check prep and cook times.")
    warnings.append("Check the recipe’s cup and spoon standard before saving.")
    initial["description"] = "\n".join(description) or "Saved from a pasted recipe."
    initial["instructions"] = "\n".join(steps)
    # Retain every ingredient line, including package sizes and prep qualifiers.
    ingredient_notes = "\n".join(row["source"] for row in ingredients)
    initial["notes"] = "\n".join(notes)
    if ingredient_notes:
        initial["notes"] += ("\n\n" if notes else "") + "Original ingredient lines:\n" + ingredient_notes
    return {"initial": initial, "ingredients": ingredients, "warnings": list(dict.fromkeys(warnings)), "source": text}
