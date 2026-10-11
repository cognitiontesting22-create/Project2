"""Frozen, reviewed SR Legacy starter values; no network or API key needed."""
import json
from pathlib import Path
from django.db import migrations


def seed(apps, schema_editor):
    from kitchen.measurements import normalize_name
    Food = apps.get_model("kitchen", "NutritionFood")
    Alias = apps.get_model("kitchen", "NutritionAlias")
    database = schema_editor.connection.alias
    source = Path(__file__).resolve().parent.parent / "data/nutrition_usda_sr2018_v1.json"
    for entry in json.loads(source.read_text()):
        aliases = entry.pop("aliases")
        food, _ = Food.objects.using(database).get_or_create(name=entry["name"], defaults=entry)
        for name in {normalize_name(value) for value in [entry["name"], *aliases]}:
            Alias.objects.using(database).get_or_create(name=name, defaults={"food": food})


class Migration(migrations.Migration):
    dependencies = [("kitchen", "0004_nutritionfood_nutritionalias")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
