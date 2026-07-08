# Generated manually for Tag slug/label schema

from django.db import migrations, models


def copy_slug_to_label(apps, schema_editor) -> None:
    Tag = apps.get_model("library", "Tag")
    for tag in Tag.objects.all():
        tag.label = tag.slug
        tag.save(update_fields=["label"])


class Migration(migrations.Migration):
    dependencies = [
        ("library", "0002_probe_stage_metadata"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="tagcategory",
            options={"ordering": ["name"], "verbose_name_plural": "tag categories"},
        ),
        migrations.RenameField(
            model_name="tag",
            old_name="name",
            new_name="slug",
        ),
        migrations.AlterField(
            model_name="tag",
            name="slug",
            field=models.SlugField(max_length=80, unique=True),
        ),
        migrations.AddField(
            model_name="tag",
            name="label",
            field=models.CharField(default="", max_length=80),
            preserve_default=False,
        ),
        migrations.RunPython(copy_slug_to_label, migrations.RunPython.noop),
        migrations.AlterModelOptions(
            name="tag",
            options={"ordering": ["label", "slug"]},
        ),
    ]
