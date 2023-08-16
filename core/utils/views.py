from rest_framework.request import Request


class TranslationsProcessingMixin:
    def process_translations_data(self, request: Request) -> Request:
        if (
            request.content_type
            == "multipart/form-data; boundary=BoUnDaRyStRiNg; charset=utf-8"
        ):
            data = request.data
            translations = {}
            for key, value in data.items():
                if key.startswith("translations."):
                    lang_field, field_name = key.split(".")[1], key.split(".")[2]
                    if lang_field not in translations:
                        translations[lang_field] = {}
                    translations[lang_field][field_name] = value

            # Remove keys like 'translations.en' and 'translations.tr'
            for key in list(data.keys()):
                if key.startswith("translations."):
                    del data[key]

            data["translations"] = translations

        return request
