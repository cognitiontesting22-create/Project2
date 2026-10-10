from datetime import date, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from kitchen.demo_recipes import RECIPES
from kitchen.models import Ingredient, KitchenSettings, MealPlan, Recipe

class Command(BaseCommand):
    help = "Add 12 starter recipes and an example week without overwriting existing data."

    def add_arguments(self, parser):
        parser.add_argument("--recipes-only", action="store_true", help="Skip the example meal plan.")

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        recipes = {}
        for data in reversed(RECIPES):
            fields = dict(data)
            ingredients = fields.pop("ingredients")
            title = fields.pop("title")
            recipe, created = Recipe.objects.get_or_create(title=title, defaults=fields)
            recipes[title] = recipe
            if created:
                created_count += 1
                Ingredient.objects.bulk_create([Ingredient(recipe=recipe, quantity=Decimal(str(quantity)), unit=unit, name=name, position=index) for index, (quantity, unit, name) in enumerate(ingredients)])
        KitchenSettings.objects.get_or_create(pk=1)
        if not options["recipes_only"]:
            today = date.today()
            start = today - timedelta(days=today.weekday())
            example = [(0, "Dinner", "Basil pesto pasta", 2), (1, "Lunch", "Green goddess bowl", 2), (2, "Dinner", "Honey garlic salmon", 2), (4, "Dinner", "Coconut chickpea curry", 4), (5, "Breakfast", "Fluffy blueberry pancakes", 4)]
            for offset, meal, title, servings in example:
                MealPlan.objects.get_or_create(date=start + timedelta(days=offset), meal=meal, defaults={"recipe": recipes[title], "servings": servings})
        self.stdout.write(self.style.SUCCESS(f"Added {created_count} recipes. Your kitchen is ready!"))
