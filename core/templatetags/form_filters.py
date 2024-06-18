import re

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

class_re = re.compile(r'(<[^>]+class=["\'])(.*)(["\'][^>]*>)')


@register.filter
def add_class(value, css_class):
    string = str(value)
    match = class_re.search(string)
    if match:
        m = re.search(
            r"^%s$|^%s\s|\s%s\s|\s%s$" % (css_class, css_class, css_class, css_class),
            match.group(2),
        )
        if not m:
            modified = class_re.sub(r"\1" + match.group(2) + " " + css_class + r"\3", string)
            return mark_safe(modified)
    else:
        modified = string.replace(">", f' class="{css_class}">', 1)
        return mark_safe(modified)
    return value
