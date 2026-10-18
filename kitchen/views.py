import hashlib
import uuid
from datetime import date, timedelta
from fractions import Fraction
from urllib.parse import urlencode
from django.contrib import messages
from django.db import transaction
from django.db.models import F, Q, Exists, OuterRef
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.utils import timezone
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from .forms import IngredientFormSet, MealPlanForm, RecipeForm, SettingsForm, PantryForm, CookForm, NutritionFoodForm, RecipePasteForm
from .models import KitchenSettings, MealPlan, Recipe, ShoppingCheck, PantryItem, CookingRecord, Ingredient, NutritionFood, NutritionAlias
from .forms import NutritionProfileFormSet, PersonalPortionForm
from .models import NutritionProfile, PersonalPortion
from .nutrition import recipe_nutrition, NUTRIENTS, estimate_text
from .recipe_import import parse_recipe
from .measurements import MAX_SERVINGS, normalize_name, resolve_unit, quantity_display, shopping_display, parse_quantity, format_quantity
from .inventory import pantry_balances, recipe_match, add_stock, lock_kitchen, record_cooking, undo_cooking, deduction_display, _add_stock

def week_start(value=None):
    try:
        day = date.fromisoformat(value) if value else timezone.localdate()
    except (ValueError, TypeError):
        day = timezone.localdate()
    if not 1900 <= day.year <= 2100:
        day = timezone.localdate()
    return day - timedelta(days=day.weekday())

def week_context(request):
    start = week_start(request.GET.get("week"))
    return {"week": start, "week_end": start + timedelta(days=6), "prev_week": start - timedelta(days=7), "next_week": start + timedelta(days=7), "today": timezone.localdate()}

def recipe_box(request, favorites=False):
    recipes = Recipe.objects.annotate(total=F("prep_time") + F("cook_time"))
    query = request.GET.get("q", "").strip()
    selected = request.GET.get("filter", "all")
    sort = request.GET.get("sort", "newest")
    if favorites:
        recipes = recipes.filter(favorite=True)
    if query:
        recipes = recipes.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(ingredients__name__icontains=query)).distinct()
    filters = {"quick": Q(total__lte=30), "vegetarian": Q(vegetarian=True), "protein": Q(high_protein=True), "baking": Q(category="Dessert") | Q(tag__icontains="bak"), "seasonal": Q(seasonal=True)}
    if selected in filters:
        recipes = recipes.filter(filters[selected])
    recipes = recipes.order_by({"newest": "-created_at", "oldest": "created_at", "name": "title", "quickest": "total"}.get(sort, "-created_at"), "pk")
    if sort == "pantry":
        recipes = list(recipes.prefetch_related("ingredients"))
        balances = pantry_balances()
        for recipe in recipes:
            recipe.pantry_match = recipe_match(recipe, recipe.servings, balances)
        recipes.sort(key=lambda recipe: (not recipe.pantry_match["total_count"], recipe.pantry_match["missing_count"], -recipe.pantry_match["covered_count"], recipe.total_time, recipe.title.casefold()))
    tabs = [("all", "All recipes"), ("quick", "Quick & easy"), ("vegetarian", "Vegetarian"), ("protein", "High protein"), ("baking", "Baking")]
    context = {"recipes": recipes, "q": query, "selected_filter": selected, "sort": sort, "tabs": tabs, "favorites": favorites, "page": "favorites" if favorites else "recipes", "page_title": "Favorites" if favorites else "Recipe box"}
    context["planned_count"] = MealPlan.objects.filter(date__gte=week_start(), date__lte=week_start() + timedelta(days=6)).count()
    return render(request, "kitchen/recipe_box.html", context)

def requested_servings(request, default):
    raw = request.GET.get("servings", str(default))
    try:
        amount = int(raw)
        if not str(raw).isdigit() or not 1 <= amount <= MAX_SERVINGS:
            raise ValueError
        return amount
    except (ValueError, TypeError):
        raise ValueError("Choose a positive whole number of servings.") from None

def scaled_ingredients(recipe, servings):
    ratio = Fraction(servings, recipe.servings)
    return [{"id": ingredient.pk, "name": ingredient.name, **quantity_display(ingredient.exact_quantity * ratio, ingredient.unit, recipe.measurement_system)} for ingredient in recipe.ingredients.all()]

def recipe_detail(request, pk):
    recipe = get_object_or_404(Recipe.objects.prefetch_related("ingredients"), pk=pk)
    try:
        servings = requested_servings(request, recipe.servings)
    except ValueError as error:
        servings = recipe.servings
        messages.error(request, str(error))
    return render(request, "kitchen/recipe_detail.html", {"recipe": recipe, "servings": servings, "scaled_ingredients": scaled_ingredients(recipe, servings), "pantry_match": recipe_match(recipe, servings), "nutrition": recipe_nutrition(recipe, servings, request.GET.get("nutrition_basis")), "page": "recipes", "page_title": "Recipe box", "today": timezone.localdate(), "all_recipes": Recipe.objects.all()})

def recipe_scale(request, pk):
    recipe = get_object_or_404(Recipe.objects.prefetch_related("ingredients"), pk=pk)
    try:
        servings = requested_servings(request, recipe.servings)
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    ingredients = [{key: value for key, value in item.items() if key != "quantity"} for item in scaled_ingredients(recipe, servings)]
    match = recipe_match(recipe, servings)
    nutrition = recipe_nutrition(recipe, servings, request.GET.get("nutrition_basis"))
    return JsonResponse({"servings": servings, "ingredients": ingredients, "pantry": {key: match[key] for key in ["missing_count", "covered_count", "total_count", "ready"]},
        "nutrition": {key: nutrition[key] for key in ["basis", "available", "complete", "matched_count", "total_count", "estimated_count", "per_serving", "batch", "cards"]},
        "nutrition_html": render_to_string("kitchen/nutrition_panel.html", {"nutrition": nutrition, "recipe": recipe, "servings": servings}, request=request)})


def nutrition_table(request):
    query = request.GET.get("q", "").strip()
    foods = NutritionFood.objects.prefetch_related("aliases")
    if query:
        foods = foods.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(aliases__name__icontains=query)).distinct()
    paginator = Paginator(foods, 25)
    return render(request, "kitchen/nutrition_table.html", {"foods": paginator.get_page(request.GET.get("p")), "food_count": NutritionFood.objects.count(), "q": query, "page": "nutrition", "page_title": "Nutrition table"})


def nutrition_food_edit(request, pk=None):
    food = get_object_or_404(NutritionFood, pk=pk) if pk else NutritionFood()
    form = NutritionFoodForm(request.POST or None, instance=food, initial={"name": request.GET.get("name", "")[:120]} if pk is None else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            lock_kitchen()
            # Check again after locking to serialize concurrent alias edits.
            if NutritionAlias.objects.filter(name__in=form.alias_names).exclude(food_id=food.pk).exists():
                form.add_error("aliases", "One of these names was just matched to another food. Choose a different matching name.")
            else:
                form.save()
                messages.success(request, "Nutrition table updated. Recipe estimates now use these values.")
                return redirect("nutrition_table")
    return render(request, "kitchen/nutrition_food_form.html", {"form": form, "food": food, "editing": bool(pk), "page": "nutrition", "page_title": "Edit food nutrition" if pk else "Add food nutrition"})


@require_POST
def nutrition_food_delete(request, pk):
    with transaction.atomic():
        lock_kitchen()
        get_object_or_404(NutritionFood, pk=pk).delete()
    messages.success(request, "Food removed. Recipes will show a nutrition gap for its matching ingredients.")
    return redirect("nutrition_table")

def recipe_import(request):
    form = RecipePasteForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        profile, _ = KitchenSettings.objects.get_or_create(pk=1)
        try:
            draft = parse_recipe(form.cleaned_data["text"], profile.measurement_system)
        except ValueError as error:
            form.add_error("text", str(error))
        else:
            recipe = Recipe()
            recipe_form = RecipeForm(instance=recipe, initial=draft["initial"])
            initial_ingredients = []
            for row in draft["ingredients"]:
                values = dict(row)
                if values["quantity"]:
                    display = format_quantity(parse_quantity(values["quantity"]))
                    if not display.startswith("≈"):
                        values["quantity"] = display
                initial_ingredients.append(values)
            formset = IngredientFormSet(instance=recipe, prefix="ingredients", initial=initial_ingredients)
            # Inline formsets limit initial rows to the number of extra forms.
            # min_num contributes one row, including for an empty draft.
            formset.extra = max(1, len(draft["ingredients"])) - 1
            return render(request, "kitchen/recipe_form.html", {"form": recipe_form, "formset": formset,
                "recipe": recipe, "editing": False, "import_draft": draft, "page": "recipes", "page_title": "Review pasted recipe"})
    return render(request, "kitchen/recipe_import.html", {"form": form, "page": "recipes", "page_title": "Paste a recipe"})


def recipe_edit(request, pk=None):
    recipe = get_object_or_404(Recipe, pk=pk) if pk else Recipe()
    profile, _ = KitchenSettings.objects.get_or_create(pk=1)
    form = RecipeForm(request.POST or None, request.FILES or None, instance=recipe, initial={"measurement_system": recipe.measurement_system if pk else profile.measurement_system})
    formset = IngredientFormSet(request.POST or None, instance=recipe, prefix="ingredients")
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                lock_kitchen()
                recipe = form.save()
                formset.instance = recipe
                ingredients = formset.save(commit=False)
                for removed in formset.deleted_objects:
                    removed.delete()
                for index, ingredient in enumerate(ingredients):
                    ingredient.position = index
                    ingredient.save()
            messages.success(request, "Recipe updated. Looking delicious!" if pk else "A new recipe for your box. Happy cooking!")
            return redirect("recipe_detail", pk=recipe.pk)
        messages.error(request, "A few details need your attention. Please check the fields below.")
    import_draft = None
    if not pk and request.method == "POST" and request.POST.get("import_source"):
        try:
            import_draft = parse_recipe(request.POST["import_source"], profile.measurement_system)
        except ValueError:
            pass
    return render(request, "kitchen/recipe_form.html", {"form": form, "formset": formset, "recipe": recipe, "editing": bool(pk), "import_draft": import_draft, "page": "recipes", "page_title": "Edit recipe" if pk else "New recipe"})

@require_POST
def recipe_delete(request, pk):
    with transaction.atomic():
        lock_kitchen()
        get_object_or_404(Recipe, pk=pk).delete()
    messages.success(request, "Recipe removed from your box.")
    return redirect("recipe_box")

@require_POST
def favorite_toggle(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    recipe.favorite = not recipe.favorite
    recipe.save(update_fields=["favorite"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"favorite": recipe.favorite, "count": Recipe.objects.filter(favorite=True).count()})
    return redirect("recipe_detail", pk=pk)

def planner(request):
    context = week_context(request)
    plans = MealPlan.objects.filter(date__gte=context["week"], date__lte=context["week_end"]).select_related("recipe", "leftover_of")
    cooked = {record.plan_id: record for record in CookingRecord.objects.filter(plan__in=plans, undone_at__isnull=True)}
    for plan in plans:
        plan.cooked_record = cooked.get(plan.pk)
        if plan.leftover_of_id:
            plan.source_week = week_start(plan.leftover_of.date.isoformat())
    slots = {(p.date, p.meal): p for p in plans}
    settings, _ = KitchenSettings.objects.get_or_create(pk=1)
    profiles = NutritionProfile.objects.all()
    if settings.nutrition_mode == "personal":
        profiles = profiles[:1]
    profiles = list(profiles)
    selected = next((p for p in profiles if str(p.pk) == request.GET.get("person")), profiles[0] if profiles else None)
    allocations = {p.plan_id: p.servings for p in PersonalPortion.objects.filter(profile=selected, plan__in=plans)} if selected else {}
    days = []
    for offset in range(7):
        day = context["week"] + timedelta(days=offset)
        days.append({"date": day, "meals": [{"name": meal, "plan": slots.get((day, meal))} for meal, _ in MealPlan.MEAL_CHOICES]})
    for day in days:
        totals = {key: Fraction(0) for key, _, _ in NUTRIENTS}
        complete, available, unassigned = True, False, 0
        assigned = False
        for meal in day["meals"]:
            plan = meal["plan"]
            if not plan:
                continue
            portion = allocations.get(plan.pk)
            meal["personal_portion"] = portion
            if portion is None:
                unassigned += 1
                complete = False
                continue
            assigned = True
            if portion == 0:
                continue
            nutrition = recipe_nutrition(plan.recipe, plan.servings)
            complete = complete and nutrition["complete"]
            available = available or nutrition["available"]
            for key in totals:
                if nutrition["per_serving"][key] is not None:
                    totals[key] += Fraction(nutrition["per_serving"][key]) * Fraction(portion)
        day["nutrition_incomplete"] = not complete
        day["unassigned"] = unassigned
        day["nutrition_cards"] = []
        for key, label, unit in NUTRIENTS:
            goal = getattr(selected, key) if selected else None
            difference = Fraction(goal) - totals[key] if goal else None
            day["nutrition_cards"].append({"label": label, "unit": unit,
                "total": estimate_text(totals[key], 0 if key == "calories" else 1) if available or (assigned and complete) else "—",
                "goal": goal,
                "difference": estimate_text(abs(difference), 0 if key == "calories" else 1) if difference is not None and complete and (available or assigned) else None,
                "over": difference is not None and difference < 0})
    context.update({"nutrition_profiles": profiles, "selected_person": selected, "days": days, "all_recipes": Recipe.objects.all(), "planned_count": plans.count(), "cooked_count": len(cooked), "page": "planner", "page_title": "Meal planner"})
    return render(request, "kitchen/planner.html", context)

@require_POST
def plan_add(request):
    form = MealPlanForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            lock_kitchen()
            settings = KitchenSettings.objects.get(pk=1)
            portions_eaten = data["portions_eaten"] if data["portions_eaten"] is not None else min(settings.household_portions, data["servings"])
            previous = MealPlan.objects.filter(date=data["date"], meal=data["meal"]).first()
            if previous and CookingRecord.objects.filter(plan=previous, undone_at__isnull=True).exists():
                messages.error(request, "This meal is already cooked. Undo its cooking record before changing the plan.")
            else:
                changed_batch = previous and (previous.recipe_id != data["recipe"].pk or previous.servings != data["servings"] or previous.leftover_of_id is not None)
                if changed_batch:
                    previous.personal_portions.all().delete()
                    messages.info(request, "Cooking plan changed. Assign personal portions again for this meal.")
                if previous and (changed_batch or previous.portions_eaten != portions_eaten):
                    previous.leftover_meals.all().delete()
                plan, _ = MealPlan.objects.update_or_create(date=data["date"], meal=data["meal"], defaults={
                    "recipe": data["recipe"], "servings": data["servings"], "portions_eaten": portions_eaten, "leftover_of": None})
                messages.success(request, f'{data["recipe"].title} added to {data["date"]:%A} {data["meal"].lower()}.')
                if plan.spare_portions >= settings.household_portions:
                    next_day = plan.date + timedelta(days=1)
                    _, created = MealPlan.objects.get_or_create(date=next_day, meal=plan.meal, defaults={
                        "recipe": plan.recipe, "servings": settings.household_portions,
                        "portions_eaten": settings.household_portions, "leftover_of": plan})
                    if created:
                        messages.success(request, f"Leftovers planned for {next_day:%A} {plan.meal.lower()}.")
        start = week_start(data["date"].isoformat())
    else:
        if "portions_eaten" in form.errors:
            messages.error(request, "Portions eaten cannot be more than the servings you are cooking.")
        else:
            messages.error(request, "We couldn't add that meal. Choose a recipe, a valid date, and a positive whole number of servings.")
        start = week_start()
    return redirect(reverse("planner") + "?" + urlencode({"week": start.isoformat()}))

@require_POST
def plan_remove(request, pk):
    with transaction.atomic():
        lock_kitchen()
        plan = get_object_or_404(MealPlan, pk=pk)
        start = week_start(plan.date.isoformat())
        if CookingRecord.objects.filter(plan=plan, undone_at__isnull=True).exists():
            messages.error(request, "This meal is already cooked. Undo its cooking record before removing it from your plan.")
        else:
            leftover_portions = plan.portions_eaten if plan.leftover_of_id else None
            plan.delete()
            if leftover_portions is not None:
                messages.success(request, f"Leftovers removed. {leftover_portions} portions are spare again.")
            else:
                messages.success(request, "Meal removed. Room for something new!")
    return redirect(reverse("planner") + "?" + urlencode({"week": start.isoformat()}))

def shopping_key(name, dimension):
    return hashlib.sha256("\0".join((name, dimension)).encode()).hexdigest()

def shopping_requirements(start):
    active_cooking = CookingRecord.objects.filter(plan_id=OuterRef("pk"), undone_at__isnull=True)
    plans = MealPlan.objects.filter(date__gte=start, date__lte=start + timedelta(days=6)).annotate(cooked=Exists(active_cooking)).filter(cooked=False).select_related("recipe").prefetch_related("recipe__ingredients")
    grouped = {}
    profile, _ = KitchenSettings.objects.get_or_create(pk=1)
    for plan in plans:
        for ingredient in plan.recipe.ingredients.all():
            unit = resolve_unit(ingredient.unit, plan.recipe.measurement_system)
            key = (normalize_name(ingredient.name), unit.dimension)
            item = grouped.setdefault(key, {"name": ingredient.name.strip(), "total": Fraction(0), "units": [], "recipes": set()})
            item["total"] += ingredient.exact_quantity * Fraction(plan.servings, plan.recipe.servings) * unit.factor
            item["units"].append(unit)
            item["recipes"].add(plan.recipe.title)
    checks = dict(ShoppingCheck.objects.filter(week=start).values_list("key", "checked"))
    balances = pantry_balances()
    result = []
    for key, item in sorted(grouped.items()):
        item["key"] = shopping_key(*key)
        item["checked"] = checks.get(item["key"], False)
        item["available"] = min(balances.get(key, Fraction(0)), item["total"])
        item["missing"] = item["total"] - item["available"]
        item["required_display"] = shopping_display(item["total"], item["units"], profile.measurement_system)
        item["pantry_display"] = shopping_display(item["available"], item["units"], profile.measurement_system)
        item.update(shopping_display(item["missing"], item["units"], profile.measurement_system))
        item["dimension"] = key[1]
        item["recipes"] = sorted(item["recipes"])
        result.append(item)
    return result, plans.count()

def shopping_items(start):
    requirements, count = shopping_requirements(start)
    return [item for item in requirements if item["missing"] > 0], count

def shopping(request):
    context = week_context(request)
    requirements, count = shopping_requirements(context["week"])
    items = [item for item in requirements if item["missing"] > 0]
    context.update({"items": items, "covered": [item for item in requirements if not item["missing"]], "planned_count": count, "checked_count": sum(i["checked"] for i in items), "page": "shopping", "page_title": "Shopping list"})
    return render(request, "kitchen/shopping.html", context)

@require_POST
def shopping_check(request):
    start = week_start(request.POST.get("week"))
    key = request.POST.get("key", "")
    items, _ = shopping_items(start)
    if key not in {item["key"] for item in items}:
        return JsonResponse({"error": "This ingredient is no longer on your list."}, status=400)
    check, _ = ShoppingCheck.objects.get_or_create(week=start, key=key)
    check.checked = request.POST.get("checked") == "true"
    check.save(update_fields=["checked"])
    return JsonResponse({"checked": check.checked})

def shopping_export(request):
    start = week_start(request.GET.get("week"))
    items, _ = shopping_items(start)
    lines = [f"Savor shopping list · {start:%d %B %Y}", ""]
    lines += [f'{"[x]" if i["checked"] else "[ ]"} {i["amount_display"]} {i["unit"]} {i["name"]}'.replace("  ", " ") for i in items]
    response = HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="savor-shopping-{start}.txt"'
    return response

def settings_view(request):
    profile, _ = KitchenSettings.objects.get_or_create(pk=1)
    data = request.POST.copy() if request.method == "POST" else None
    if data is not None:
        # Older clients that save the original settings fields keep this value.
        data.setdefault("household_portions", profile.household_portions)
    form = SettingsForm(data, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your kitchen, your way. Settings saved.")
        return redirect("settings")
    return render(request, "kitchen/settings.html", {"nutrition_profiles_formset": NutritionProfileFormSet(queryset=NutritionProfile.objects.all(), prefix="people"), "nutrition_mode": profile.nutrition_mode, "form": form, "page": "settings", "page_title": "Kitchen settings"})

def pantry_context(request, form=None):
    profile, _ = KitchenSettings.objects.get_or_create(pk=1)
    all_items = list(PantryItem.objects.all())
    today = timezone.localdate()
    for item in all_items:
        item.display = quantity_display(item.exact_quantity, item.unit, item.measurement_system)
        item.use_soon = bool(item.exact_quantity and item.expires_on and today <= item.expires_on <= today + timedelta(days=7))
    status = request.GET.get("status", "all")
    query = request.GET.get("q", "").strip()
    items = [item for item in all_items if not query or query.casefold() in item.name.casefold()]
    if status == "stock":
        items = [item for item in items if item.exact_quantity and not item.expired]
    elif status == "soon":
        items = [item for item in items if item.use_soon]
    elif status == "empty":
        items = [item for item in items if not item.exact_quantity]
    elif status == "expired":
        items = [item for item in items if item.expired]
    return {"items": items, "stock_count": sum(bool(item.exact_quantity) and not item.expired for item in all_items), "soon_count": sum(item.use_soon for item in all_items), "empty_count": sum(not item.exact_quantity for item in all_items), "q": query, "status": status,
        "form": form if form is not None else PantryForm(initial={"measurement_system": profile.measurement_system, "location": "pantry"}),
        "ingredient_names": Ingredient.objects.order_by("name").values_list("name", flat=True).distinct(), "page": "pantry", "page_title": "My pantry"}

def pantry(request):
    return render(request, "kitchen/pantry.html", pantry_context(request))

@require_POST
def pantry_add(request):
    form = PantryForm(request.POST)
    if form.is_valid():
        add_stock(form.cleaned_data)
        messages.success(request, f'{form.cleaned_data["name"].capitalize()} added to your pantry. Your shopping list is up to date.')
        return redirect("pantry")
    return render(request, "kitchen/pantry.html", {**pantry_context(request, form), "open_pantry_dialog": True})

def pantry_edit(request, pk):
    item = get_object_or_404(PantryItem, pk=pk)
    form = PantryForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            lock_kitchen()
            form.save()
        messages.success(request, "Pantry updated. Your recipes and shopping list now use the new stock amount.")
        return redirect("pantry")
    return render(request, "kitchen/pantry_form.html", {"form": form, "item": item, "ingredient_names": Ingredient.objects.order_by("name").values_list("name", flat=True).distinct(), "page": "pantry", "page_title": "Update pantry item"})

@require_POST
def pantry_delete(request, pk):
    with transaction.atomic():
        lock_kitchen()
        get_object_or_404(PantryItem, pk=pk).delete()
    messages.success(request, "Item removed from your pantry.")
    return redirect("pantry")

def discover(request):
    query = request.GET.get("q", "").strip()
    extra_text = request.GET.get("ingredients", "")[:4000]
    extras = {normalize_name(name.strip()) for name in extra_text.split(",")[:30] if name.strip() and len(name.strip()) <= 120}
    recipes = Recipe.objects.all()
    if query:
        recipes = recipes.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(ingredients__name__icontains=query)).distinct()
    try:
        servings = requested_servings(request, 2)
    except ValueError:
        servings = 2
        messages.error(request, "Choose a positive whole number of servings.")
    balances = pantry_balances()
    recipes = list(recipes.prefetch_related("ingredients"))
    for recipe in recipes:
        recipe.pantry_match = recipe_match(recipe, servings, balances, extras)
    recipes.sort(key=lambda recipe: (not recipe.pantry_match["total_count"], recipe.pantry_match["missing_count"], -recipe.pantry_match["covered_count"], recipe.total_time, recipe.title.casefold()))
    ready_count = sum(recipe.pantry_match["ready"] for recipe in recipes)
    if request.GET.get("ready") == "1":
        recipes = [recipe for recipe in recipes if recipe.pantry_match["ready"]]
    return render(request, "kitchen/discover.html", {"recipes": recipes, "ready_count": ready_count, "q": query, "ingredients": extra_text, "extras": sorted(extras), "servings": servings,
        "only_ready": request.GET.get("ready") == "1", "page": "discover", "page_title": "Cook with my pantry", "discovering": True})

def cook_recipe(request, pk):
    recipe = get_object_or_404(Recipe.objects.prefetch_related("ingredients"), pk=pk)
    plan = None
    if request.GET.get("plan"):
        plan_id = request.GET["plan"]
        if not plan_id.isascii() or not plan_id.isdigit() or len(plan_id) > 18:
            from django.http import Http404
            raise Http404("Meal not found")
        plan = get_object_or_404(MealPlan, pk=int(plan_id), recipe=recipe, leftover_of__isnull=True)
    try:
        servings = requested_servings(request, plan.servings if plan else recipe.servings)
    except ValueError as error:
        servings = plan.servings if plan else recipe.servings
        messages.error(request, str(error))
    form = CookForm(request.POST or None, initial={"servings": servings, "cooked_on": timezone.localdate(), "action_token": uuid.uuid4()})
    if request.method == "POST":
        if form.is_valid():
            try:
                record, created = record_cooking(recipe, form.cleaned_data["servings"], form.cleaned_data["cooked_on"], form.cleaned_data["action_token"], form.cleaned_data["notes"], plan)
            except ValueError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, "Meal recorded and pantry updated. Enjoy every bite!" if created else "This meal was already recorded. Your pantry hasn’t been deducted again.")
                return redirect(reverse("cooking_history") + f"#cooking-{record.pk}")
        else:
            messages.error(request, "Check the details below before recording this meal.")
        servings = form.cleaned_data.get("servings", servings)
    active_record = CookingRecord.objects.filter(plan=plan, undone_at__isnull=True).first() if plan else None
    profile, _ = KitchenSettings.objects.get_or_create(pk=1)
    return render(request, "kitchen/cook_recipe.html", {"recipe": recipe, "plan": plan, "form": form, "servings": servings,
        "match": recipe_match(recipe, servings, system=profile.measurement_system), "active_record": active_record, "today": timezone.localdate(), "page": "history", "page_title": "Record a cooked meal"})

def cooking_history(request):
    records = list(CookingRecord.objects.select_related("recipe", "plan").all())
    for record in records:
        record.deductions_display = [deduction_display(item) for item in record.deductions]
    return render(request, "kitchen/cooking_history.html", {"records": records, "cooked_count": sum(not record.undone_at for record in records), "page": "history", "page_title": "Cooking history"})

@require_POST
def cooking_undo(request, pk):
    record = get_object_or_404(CookingRecord, pk=pk)
    changed = undo_cooking(record)
    messages.success(request, "Cooking record undone. The deducted ingredients are back in your pantry." if changed else "This record was already undone. Stock hasn’t been added twice.")
    return redirect(reverse("cooking_history") + f"#cooking-{pk}")

@require_POST
def shopping_stock(request):
    start = week_start(request.POST.get("week"))
    with transaction.atomic():
        lock_kitchen()
        items, _ = shopping_items(start)
        checked = [item for item in items if item["checked"]]
        profile = KitchenSettings.objects.get(pk=1)
        for item in checked:
            _add_stock(item["name"], item["quantity"], item["unit"], profile.measurement_system)
        ShoppingCheck.objects.filter(week=start, key__in=[item["key"] for item in checked]).update(checked=False)
    messages.success(request, f"{len(checked)} checked ingredient{'s' if len(checked) != 1 else ''} added to your pantry." if checked else "No checked shortages to add. Your pantry is already up to date.")
    return redirect(reverse("shopping") + "?" + urlencode({"week": start.isoformat()}))


@require_POST
def nutrition_profiles_save(request):
    settings, _ = KitchenSettings.objects.get_or_create(pk=1)
    formset = NutritionProfileFormSet(request.POST, queryset=NutritionProfile.objects.all(), prefix="people")
    mode = request.POST.get("nutrition_mode")
    if mode in {"personal", "household"} and formset.is_valid():
        with transaction.atomic():
            formset.save()
            settings.nutrition_mode = mode
            settings.save(update_fields=["nutrition_mode"])
        messages.success(request, "Nutrition goals saved. Switching modes keeps everyone's goals and portions.")
        return redirect("settings")
    return render(request, "kitchen/settings.html", {"form": SettingsForm(instance=settings), "nutrition_profiles_formset": formset, "nutrition_mode": mode, "page": "settings", "page_title": "Kitchen settings"}, status=400)


@require_POST
def personal_portion_save(request, pk, person):
    profile = get_object_or_404(NutritionProfile, pk=person)
    plan = get_object_or_404(MealPlan, pk=pk)
    form = PersonalPortionForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            lock_kitchen()
            plan = MealPlan.objects.select_for_update().get(pk=pk)
            allocated = sum(p.servings for p in plan.personal_portions.exclude(profile=profile))
            if allocated + form.cleaned_data["servings"] > plan.servings:
                messages.error(request, "Personal portions exceed the cooking quantity. Increase the planned servings or reduce another person's portions first.")
            else:
                PersonalPortion.objects.update_or_create(profile=profile, plan=plan, defaults=form.cleaned_data)
                messages.success(request, "Personal portion saved.")
    else:
        messages.error(request, "Use a portion from 0 to 1000 with at most two decimal places.")
    return redirect(reverse("planner") + "?" + urlencode({"week": week_start(plan.date.isoformat()), "person": person}))
