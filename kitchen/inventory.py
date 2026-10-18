"""Shared quantity-aware pantry matching, cooking receipts and atomic stock changes."""
from datetime import date
from fractions import Fraction
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from .measurements import MAX_SERVINGS, normalize_name, resolve_unit, shopping_display, quantity_display
from .models import CookingRecord, KitchenSettings, LeftoverMeal, MealPlan, PantryItem, PreparedBatch, Recipe
from .nutrition import nutrition_snapshot

def lock_kitchen():
    # Start with a write: SQLite then serializes every read/modify/write stock action.
    if not KitchenSettings.objects.filter(pk=1).update(name=F("name")):
        KitchenSettings.objects.create(pk=1)

def item_key(name, unit, system="us"):
    return normalize_name(name), resolve_unit(unit, system).dimension

def usable_pantry():
    return PantryItem.objects.exclude(quantity="0").filter(Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate()))

def pantry_balances(items=None):
    balances = {}
    for item in usable_pantry() if items is None else items:
        unit = resolve_unit(item.unit, item.measurement_system)
        key = (normalize_name(item.name), unit.dimension)
        balances[key] = balances.get(key, Fraction(0)) + item.exact_quantity * unit.factor
    return balances

def recipe_requirements(recipe, servings):
    grouped = {}
    for ingredient in recipe.ingredients.all():
        unit = resolve_unit(ingredient.unit, recipe.measurement_system)
        key = (normalize_name(ingredient.name), unit.dimension)
        row = grouped.setdefault(key, {"name": ingredient.name, "total": Fraction(0), "units": []})
        row["total"] += ingredient.exact_quantity * Fraction(servings, recipe.servings) * unit.factor
        row["units"].append(unit)
    return grouped

def recipe_match(recipe, servings, balances=None, extra_names=(), system="us"):
    balances = pantry_balances() if balances is None else balances
    requirements = recipe_requirements(recipe, servings)
    rows = []
    for key, row in requirements.items():
        unverified = key[0] in extra_names
        available = row["total"] if unverified else min(balances.get(key, Fraction(0)), row["total"])
        missing = row["total"] - available
        rows.append({**row, "key": key, "available": available, "missing": missing, "unverified": unverified,
            "required_display": shopping_display(row["total"], row["units"], system),
            "available_display": shopping_display(available, row["units"], system),
            "missing_display": shopping_display(missing, row["units"], system)})
    gaps = [row for row in rows if row["missing"] > 0]
    return {"rows": rows, "missing": gaps, "missing_count": len(gaps), "total_count": len(rows), "covered_count": len(rows) - len(gaps),
        "ready": bool(rows) and not gaps, "unverified": any(row["unverified"] for row in rows),
        "coverage_percent": round(100 * (len(rows) - len(gaps)) / len(rows)) if rows else 0}

def _add_stock(name, amount, unit, system="us", location="pantry", expires_on=None):
    """Caller holds the kitchen write lock. Identical stock lots are replenished."""
    target_unit = resolve_unit(unit, system)
    key = (normalize_name(name), target_unit.dimension)
    for item in PantryItem.objects.filter(location=location, expires_on=expires_on).order_by("pk"):
        candidate = resolve_unit(item.unit, item.measurement_system)
        if item_key(item.name, item.unit, item.measurement_system) == key and candidate.factor == target_unit.factor:
            item.quantity = str(item.exact_quantity + amount)
            item.save(update_fields=["quantity", "updated_at"])
            return item
    return PantryItem.objects.create(name=name, quantity=str(amount), unit=unit, measurement_system=system, location=location, expires_on=expires_on)

@transaction.atomic
def add_stock(data):
    lock_kitchen()
    return _add_stock(data["name"], Fraction(data["quantity"]), data["unit"], data["measurement_system"], data["location"], data.get("expires_on"))

@transaction.atomic
def record_cooking(recipe, servings, cooked_on, action_token, notes="", plan=None, *, portions_eaten=None, storage_location="fridge", use_by=None):
    lock_kitchen()
    existing = CookingRecord.objects.filter(action_token=action_token).first()
    if existing:
        return existing, False
    if not isinstance(servings, int) or not 1 <= servings <= MAX_SERVINGS:
        raise ValueError("Choose a positive whole number of servings.")
    try:
        recipe.refresh_from_db()
    except Recipe.DoesNotExist:
        raise ValueError("That recipe was removed. Choose another recipe to cook.") from None
    if not recipe.ingredients.exists():
        raise ValueError("Add measured ingredients before recording this recipe.")
    if plan:
        # Reload after locking so changes made since the review cannot corrupt stock.
        try:
            plan.refresh_from_db()
        except MealPlan.DoesNotExist:
            raise ValueError("That planned meal was removed. Review your plan before recording it.") from None
        if plan.recipe_id != recipe.pk:
            raise ValueError("That meal changed. Review the current recipe before recording it.")
        existing = CookingRecord.objects.filter(plan=plan, undone_at__isnull=True).first()
        if existing:
            return existing, False
        if plan.leftover_of_id:
            raise ValueError("Leftover meals are not cooking batches.")
    if portions_eaten is None:
        portions_eaten = plan.portions_eaten if plan else min(KitchenSettings.objects.get(pk=1).household_portions, servings)
    if not isinstance(portions_eaten, int) or not 0 <= portions_eaten <= servings:
        raise ValueError("Portions eaten cannot be more than the servings you made.")
    if storage_location not in dict(PreparedBatch.STORAGE_CHOICES):
        raise ValueError("Choose fridge or freezer for the portions you are keeping.")
    remaining_portions = servings - portions_eaten
    if remaining_portions:
        if use_by is None:
            raise ValueError("Choose a use by date for the portions you are keeping.")
        if not isinstance(use_by, date):
            raise ValueError("Choose a valid use by date.")
        if use_by < cooked_on:
            raise ValueError("Use by must be on or after the day you cooked.")
    else:
        use_by = None
    stock = list(usable_pantry())
    stock.sort(key=lambda item: (item.expires_on or date.max, item.pk))
    groups = {}
    for item in stock:
        groups.setdefault(item_key(item.name, item.unit, item.measurement_system), []).append(item)
    deductions, shortages = [], []
    for key, requirement in recipe_requirements(recipe, servings).items():
        remaining = requirement["total"]
        for item in groups.get(key, []):
            if remaining <= 0:
                break
            unit = resolve_unit(item.unit, item.measurement_system)
            consumed_base = min(remaining, item.exact_quantity * unit.factor)
            if consumed_base <= 0:
                continue
            consumed = consumed_base / unit.factor
            deductions.append({"pantry_item_id": item.pk, "name": item.name, "quantity": str(consumed), "base_quantity": str(consumed_base), "unit": item.unit,
                "measurement_system": item.measurement_system, "location": item.location, "expires_on": item.expires_on.isoformat() if item.expires_on else None})
            item.quantity = str(item.exact_quantity - consumed)
            item.save(update_fields=["quantity", "updated_at"])
            remaining -= consumed_base
        if remaining:
            display = shopping_display(remaining, requirement["units"], recipe.measurement_system)
            shortages.append({"name": requirement["name"], "amount_display": display["amount_display"], "unit": display["unit"], "exact": display["exact"]})
    record = CookingRecord.objects.create(recipe=recipe, recipe_title=recipe.title, image_snapshot=recipe.image_src, plan=plan, servings=servings,
        cooked_on=cooked_on, action_token=action_token, notes=notes, deductions=deductions, shortages=shortages)
    batch = PreparedBatch.objects.create(cooking_record=record, recipe_title=record.recipe_title, yield_portions=servings,
        portions_eaten=portions_eaten, remaining_portions=remaining_portions,
        storage_location=storage_location, use_by=use_by, nutrition_snapshot=nutrition_snapshot(recipe, servings),
        planned_servings=plan.servings if plan else None, planned_portions_eaten=plan.portions_eaten if plan else None)
    if plan:
        plan.servings = servings
        plan.portions_eaten = portions_eaten
        plan.reserved_portions = 0
        plan.save(update_fields=["servings", "portions_eaten", "reserved_portions"])
        reconcile_leftovers(plan, preserve_allocations=True)
        batch.reserved_portions = plan.reserved_portions
    return record, True

@transaction.atomic
def undo_cooking(record):
    lock_kitchen()
    record.refresh_from_db()
    if record.undone_at:
        return False
    if LeftoverMeal.objects.filter(batch__cooking_record=record, undone_at__isnull=True).exists():
        raise ValueError("Undo the leftover meals eaten from this batch first.")
    for deduction in record.deductions:
        item = PantryItem.objects.filter(pk=deduction["pantry_item_id"]).first()
        original_key = item_key(deduction["name"], deduction["unit"], deduction["measurement_system"])
        if item and item_key(item.name, item.unit, item.measurement_system) == original_key:
            factor = resolve_unit(item.unit, item.measurement_system).factor
            item.quantity = str(item.exact_quantity + Fraction(deduction["base_quantity"]) / factor)
            item.save(update_fields=["quantity", "updated_at"])
        else:
            _add_stock(deduction["name"], Fraction(deduction["quantity"]), deduction["unit"], deduction["measurement_system"], deduction["location"], date.fromisoformat(deduction["expires_on"]) if deduction["expires_on"] else None)
    record.undone_at = timezone.now()
    record.save(update_fields=["undone_at"])
    batch = PreparedBatch.objects.filter(cooking_record=record).first()
    if record.plan_id and batch:
        source = record.plan
        requested = sum(meal.portions_eaten for meal in source.leftover_meals.all())
        source.portions_eaten = batch.planned_portions_eaten if batch.planned_portions_eaten is not None else source.portions_eaten
        # Reservations made from extra actual yield must also survive cooking undo.
        source.servings = max(batch.planned_servings or source.servings, source.portions_eaten + requested)
        source.reserved_portions = 0
        source.save(update_fields=["servings", "portions_eaten", "reserved_portions"])
    PreparedBatch.objects.filter(cooking_record=record).delete()
    if record.plan_id and batch:
        reconcile_leftovers(source)
    return True


def change_reservation(source, portions):
    """Caller holds lock_kitchen inside an atomic transaction, including plan changes.

    The plan mirrors the batch's reservation so cooking undo leaves its promise intact.
    """
    source.refresh_from_db()
    batch = PreparedBatch.objects.filter(cooking_record__plan=source, cooking_record__undone_at__isnull=True).first()
    remaining = batch.remaining_portions if batch else source.remaining_portions
    reserved = source.reserved_portions + portions
    if not 0 <= reserved <= remaining:
        raise ValueError("There aren't enough unassigned portions for this leftover meal.")
    source.reserved_portions = reserved
    source.save(update_fields=["reserved_portions"])
    if batch:
        batch.reserved_portions = reserved
        batch.save(update_fields=["reserved_portions"])


def release_leftover_reservation(plan):
    """Release an uneaten planner promise before removal/replacement, under the lock."""
    if plan.leftover_of_id:
        change_reservation(plan.leftover_of, -plan.allocated_portions)


def ordered_leftovers(source):
    meals = {meal: order for order, (meal, _) in enumerate(MealPlan.MEAL_CHOICES)}
    return sorted(source.leftover_meals.all(), key=lambda plan: (plan.date, meals[plan.meal], plan.pk))


def reconcile_leftovers(source, *, preserve_allocations=False):
    """Caller holds the kitchen lock. Earliest meals receive the available portions first.

    Cooking may cut reservations but never enlarge them; editing an uncooked plan
    or undoing cooking can cover its requested portions again.
    """
    batch = PreparedBatch.objects.filter(cooking_record__plan=source, cooking_record__undone_at__isnull=True).first()
    remaining = batch.remaining_portions if batch else source.remaining_portions
    reserved, short = 0, []
    eaten = set(LeftoverMeal.objects.filter(plan__leftover_of=source, undone_at__isnull=True).values_list("plan_id", flat=True))
    for meal in ordered_leftovers(source):
        wanted = meal.allocated_portions if preserve_allocations else meal.portions_eaten
        allocated = 0 if meal.pk in eaten else min(wanted, remaining - reserved)
        if allocated != meal.allocated_portions:
            meal.allocated_portions = allocated
            meal.save(update_fields=["allocated_portions"])
        reserved += allocated
        if meal.pk not in eaten and meal.shortfall_portions:
            short.append(meal)
    source.reserved_portions = reserved
    source.save(update_fields=["reserved_portions"])
    if batch:
        batch.reserved_portions = reserved
        batch.save(update_fields=["reserved_portions"])
    return short


def meal_slot_label(plan):
    return f"{plan.date:%A} {plan.meal.lower()}"


def portions_text(portions):
    return f"{portions} portion{'s' if portions != 1 else ''}"


def removed_leftovers_text(meals):
    return f"Its expected leftovers on {', '.join(meal_slot_label(meal) for meal in meals)} were removed too."


@transaction.atomic
def schedule_leftovers(source, day, meal):
    lock_kitchen()
    source.refresh_from_db()
    if source.leftover_of_id:
        raise ValueError("Leftover meals are not cooking batches.")
    if day <= source.date:
        raise ValueError("Leftovers can only be planned after the meal they come from.")
    if MealPlan.objects.filter(date=day, meal=meal).exists():
        raise ValueError("That slot already has a meal. Choose an empty slot.")
    if meal not in dict(MealPlan.MEAL_CHOICES):
        raise ValueError("Choose breakfast, lunch or dinner for these leftovers.")
    household = KitchenSettings.objects.get(pk=1).household_portions
    batch = PreparedBatch.objects.filter(cooking_record__plan=source, cooking_record__undone_at__isnull=True).first()
    accounting = batch or source
    if accounting.unassigned_portions < household:
        raise ValueError("Not enough unassigned portions for the household.")
    if batch and batch.use_by and day > batch.use_by:
        raise ValueError("That is after this batch's use by date.")
    plan = MealPlan.objects.create(recipe=source.recipe, date=day, meal=meal, servings=household,
        portions_eaten=household, allocated_portions=household, leftover_of=source)
    change_reservation(source, household)
    return plan


def source_meal_label(source):
    return f"{source.date:%A}'s {source.meal.lower()}"


@transaction.atomic
def move_prepared_batch(batch, location, use_by):
    lock_kitchen()
    batch.refresh_from_db()
    if batch.storage_location == location:
        raise ValueError(f"This batch is already in the {location}.")
    batch.storage_location = location
    batch.use_by = use_by
    batch.save(update_fields=["storage_location", "use_by"])


@transaction.atomic
def eat_leftovers(plan, action_token):
    lock_kitchen()
    plan.refresh_from_db()
    if not plan.leftover_of_id:
        raise ValueError("Choose a leftover meal to record.")
    existing = LeftoverMeal.objects.filter(action_token=action_token).first()
    if not existing:
        existing = LeftoverMeal.objects.filter(plan=plan, undone_at__isnull=True).first()
    if existing:
        return existing, False
    batch = PreparedBatch.objects.filter(cooking_record__plan_id=plan.leftover_of_id,
        cooking_record__undone_at__isnull=True).first()
    if not batch:
        raise ValueError(f"Cook {source_meal_label(plan.leftover_of)} first, these are still expected leftovers.")
    portions = plan.allocated_portions
    if not portions:
        raise ValueError("There are no portions left to eat.")
    if portions > batch.reserved_portions or portions > batch.remaining_portions:
        raise ValueError("There aren't enough reserved portions for this leftover meal.")
    record = LeftoverMeal.objects.create(batch=batch, plan=plan, portions=portions,
        eaten_on=timezone.localdate(), action_token=action_token)
    batch.remaining_portions -= portions
    batch.reserved_portions -= portions
    batch.save(update_fields=["remaining_portions", "reserved_portions"])
    MealPlan.objects.filter(pk=plan.leftover_of_id).update(reserved_portions=batch.reserved_portions)
    plan.allocated_portions = 0
    plan.save(update_fields=["allocated_portions"])
    return record, True


@transaction.atomic
def undo_leftovers(record):
    lock_kitchen()
    record.refresh_from_db()
    if record.undone_at:
        return False
    batch = record.batch
    batch.remaining_portions += record.portions
    batch.reserved_portions += record.portions
    batch.save(update_fields=["remaining_portions", "reserved_portions"])
    MealPlan.objects.filter(pk=record.plan.leftover_of_id).update(reserved_portions=batch.reserved_portions)
    record.plan.allocated_portions = record.portions
    record.plan.save(update_fields=["allocated_portions"])
    record.undone_at = timezone.now()
    record.save(update_fields=["undone_at"])
    return True

def deduction_display(deduction):
    display = quantity_display(Fraction(deduction["quantity"]), deduction["unit"], deduction["measurement_system"])
    return f'{display["amount_display"]} {display["unit"]} {deduction["name"]}'.replace("  ", " ")
