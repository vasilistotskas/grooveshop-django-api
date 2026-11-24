from functools import reduce
from operator import or_

from django.db import migrations
from django.db.models import Q

# The models ``GET /api/v1/tagged-item`` serves as ``contentObject``
# (``core.api.serializers.CONTENT_OBJECT_SERIALIZERS`` when this was
# written), named here so a later change to that registry cannot change
# what this migration deleted.
SERVABLE = (("product", "product"),)


def delete_unservable_tagged_items(apps, schema_editor):
    """Delete the rows the API could never serve.

    A row of another model made every read of it a 500 (the demo seed
    tagged blog posts this way; a blog post has tags of its own).
    """
    TaggedItem = apps.get_model("tag", "TaggedItem")
    servable = reduce(
        or_,
        (
            Q(content_type__app_label=app_label, content_type__model=model)
            for app_label, model in SERVABLE
        ),
    )
    TaggedItem.objects.exclude(servable).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("tag", "0009_remove_german_translations"),
    ]

    operations = [
        # No reverse: the deleted rows were unservable, so there is
        # nothing worth restoring.
        migrations.RunPython(
            delete_unservable_tagged_items, migrations.RunPython.noop
        ),
    ]
