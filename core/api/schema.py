from django.conf import settings


def generate_schema_multi_lang(model_instance):
    fields = {}
    translated_fields = model_instance._parler_meta.get_all_fields()
    languages = settings.PARLER_LANGUAGES[settings.SITE_ID]

    if not translated_fields or not languages:
        return {"type": "object", "properties": {}}

    for language in languages:
        fields[language["code"]] = {"type": "object", "properties": {}}
        for translated_field in translated_fields:
            fields[language["code"]]["properties"][translated_field] = {
                "type": "string"
            }
    return {"type": "object", "properties": fields}
