from django.contrib.sites.models import Site
from measurement.measures import Weight

from core.units import WeightUnits


def zero_weight():
    return Weight(kg=0)


def convert_weight(weight: Weight, unit: str) -> Weight:
    converted_weight = getattr(weight, unit)
    weight = Weight(**{unit: converted_weight})
    weight.value = round(weight.value, 3)
    return weight


def get_default_weight_unit():
    site = Site.objects.get_current()
    return site.settings.get_setting(key="default_weight_unit", site_id=site.id, default=WeightUnits.KG)


def convert_weight_to_default_weight_unit(weight: Weight) -> Weight:
    default_unit = get_default_weight_unit()
    if weight is not None:
        if weight.unit != default_unit:
            weight = convert_weight(weight, default_unit)
        else:
            weight.value = round(weight.value, 3)
    return weight
