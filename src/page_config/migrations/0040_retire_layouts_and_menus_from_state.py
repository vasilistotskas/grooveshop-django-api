"""Retire page_config's layouts and menus: release N, the expand step.

The page builder serves every store's pages and menus, so ``PageLayout``,
``PageSection``, the navigation models and their translations leave Django's
STATE here; their tables stay for the release still serving while this one
rolls out, and the next release drops them (``contract_of`` this migration).

The one database change: the deferred foreign key from
``page_config_navigationlink.content_page_id`` to ``page_config_contentpage``
is dropped. ContentPage stays, and with the link model gone from state
Django no longer cascades a content page's delete into the link table, so
the constraint would refuse that delete at commit. Dropping a constraint is
not a removal the old release notices: it cascades in Python. The name is
generated, so it is found in the catalogue.
"""

from django.db import migrations

DROP_CONTENT_PAGE_FK = """
DO $$
DECLARE fk record;
BEGIN
    FOR fk IN
        SELECT conname FROM pg_constraint
         WHERE contype = 'f'
           AND conrelid = to_regclass('page_config_navigationlink')
           AND confrelid = to_regclass('page_config_contentpage')
    LOOP
        EXECUTE format(
            'ALTER TABLE page_config_navigationlink DROP CONSTRAINT %I',
            fk.conname
        );
    END LOOP;
END $$;
"""

# Back as Django made it, NOT VALID: links of content pages deleted in
# between would refuse a validated constraint.
ADD_CONTENT_PAGE_FK = """
ALTER TABLE page_config_navigationlink
  ADD CONSTRAINT page_config_navigationlink_content_page_id_fk
  FOREIGN KEY (content_page_id) REFERENCES page_config_contentpage (id)
  DEFERRABLE INITIALLY DEFERRED NOT VALID;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('page_config', '0039_retire_unused_section_types'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    DROP_CONTENT_PAGE_FK, reverse_sql=ADD_CONTENT_PAGE_FK
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name='navigationcolumn',
                    name='navigationcolumn_sort_order_ix',
                ),
                migrations.RemoveIndex(
                    model_name='navigationcolumn',
                    name='navigationcolumn_created_at_ix',
                ),
                migrations.RemoveIndex(
                    model_name='navigationcolumn',
                    name='navigationcolumn_updated_at_ix',
                ),
                migrations.RemoveConstraint(
                    model_name='navigationcolumn',
                    name='navigation_column_one_merchant_identity_per_menu',
                ),
                migrations.RemoveField(
                    model_name='navigationcolumntranslation',
                    name='master',
                ),
                migrations.AlterUniqueTogether(
                    name='navigationcolumntranslation',
                    unique_together=None,
                ),
                migrations.RemoveIndex(
                    model_name='navigationlink',
                    name='navigationlink_sort_order_ix',
                ),
                migrations.RemoveIndex(
                    model_name='navigationlink',
                    name='navigationlink_created_at_ix',
                ),
                migrations.RemoveIndex(
                    model_name='navigationlink',
                    name='navigationlink_updated_at_ix',
                ),
                migrations.RemoveConstraint(
                    model_name='navigationlink',
                    name='navigationlink_exactly_one_parent',
                ),
                migrations.RemoveConstraint(
                    model_name='navigationlink',
                    name='navigationlink_exactly_one_target',
                ),
                migrations.RemoveField(
                    model_name='navigationlinktranslation',
                    name='master',
                ),
                migrations.AlterUniqueTogether(
                    name='navigationlinktranslation',
                    unique_together=None,
                ),
                migrations.RemoveIndex(
                    model_name='pagelayout',
                    name='pagelayout_created_at_ix',
                ),
                migrations.RemoveIndex(
                    model_name='pagelayout',
                    name='pagelayout_updated_at_ix',
                ),
                migrations.RemoveIndex(
                    model_name='pagelayout',
                    name='pagelayout_published_at_ix',
                ),
                migrations.RemoveIndex(
                    model_name='pagelayout',
                    name='pagelayout_is_published_ix',
                ),
                migrations.RemoveField(
                    model_name='pagelayouttranslation',
                    name='master',
                ),
                migrations.AlterUniqueTogether(
                    name='pagelayouttranslation',
                    unique_together=None,
                ),
                migrations.RemoveIndex(
                    model_name='pagesection',
                    name='pagesection_sort_order_ix',
                ),
                migrations.RemoveIndex(
                    model_name='pagesection',
                    name='pagesection_created_at_ix',
                ),
                migrations.RemoveIndex(
                    model_name='pagesection',
                    name='pagesection_updated_at_ix',
                ),
                migrations.RemoveField(
                    model_name='navigationcolumn',
                    name='menu',
                ),
                migrations.DeleteModel(
                    name='NavigationColumnTranslation',
                ),
                migrations.RemoveField(
                    model_name='navigationlink',
                    name='column',
                ),
                migrations.RemoveField(
                    model_name='navigationlink',
                    name='content_page',
                ),
                migrations.RemoveField(
                    model_name='navigationlink',
                    name='menu',
                ),
                migrations.RemoveField(
                    model_name='navigationlink',
                    name='page_layout',
                ),
                migrations.DeleteModel(
                    name='NavigationLinkTranslation',
                ),
                migrations.DeleteModel(
                    name='PageLayoutTranslation',
                ),
                migrations.RemoveField(
                    model_name='pagesection',
                    name='layout',
                ),
                migrations.DeleteModel(
                    name='NavigationColumn',
                ),
                migrations.DeleteModel(
                    name='NavigationMenu',
                ),
                migrations.DeleteModel(
                    name='NavigationLink',
                ),
                migrations.DeleteModel(
                    name='PageLayout',
                ),
                migrations.DeleteModel(
                    name='PageSection',
                ),
            ],
        ),
    ]
