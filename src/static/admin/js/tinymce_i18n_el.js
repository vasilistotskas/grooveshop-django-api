/**
 * Greek labels for the editor strings TinyMCE's own Greek pack leaves
 * untranslated.
 *
 * ``tinymce/langs/el.js`` ships these keys with EMPTY values, and
 * TinyMCE returns a present key's value as is: a Greek-speaking admin
 * saw a blank entry in Insert and a blank tooltip where the FAQ
 * (accordion) item is. Measured on staging, 2026-09-28.
 *
 * Load order matters, and ``TINYMCE_EXTRA_MEDIA`` (settings.py) sets
 * it: django-tinymce puts those scripts between ``tinymce.min.js`` and
 * ``init_tinymce.js``. The pack is loaded first, so these values merge
 * over its empty ones; the editor then finds ``el`` already registered
 * and does not fetch the pack again, which would put the empty values
 * back.
 *
 * ``tests/unit/core/test_rich_text_field.py`` fails when a string the
 * bundled accordion plugin shows is missing here.
 */
tinymce.addI18n('el', {
  'Accordion': 'Ερώτηση & απάντηση',
  'Insert accordion': 'Εισαγωγή ερώτησης & απάντησης',
  'Toggle accordion': 'Άνοιγμα/κλείσιμο ερώτησης',
  'Delete accordion': 'Διαγραφή ερώτησης',
  'Accordion summary...': 'Ερώτηση...',
  'Accordion body...': 'Απάντηση...',
})
