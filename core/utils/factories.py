import importlib


def generate_unique_country_codes():
    from faker import Faker

    fake = Faker()

    country_model = importlib.import_module("country.models").Country
    existing_alpha_2_codes = set(country_model.objects.values_list("alpha_2", flat=True))
    existing_alpha_3_codes = set(country_model.objects.values_list("alpha_3", flat=True))

    while True:
        alpha_2_code = fake.country_code(representation="alpha-2")
        alpha_3_code = fake.country_code(representation="alpha-3")
        if alpha_2_code not in existing_alpha_2_codes and alpha_3_code not in existing_alpha_3_codes:
            return alpha_2_code, alpha_3_code
