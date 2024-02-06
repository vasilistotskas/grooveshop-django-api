from django.db import models
from django.utils.translation import gettext_lazy as _


class SeoModel(models.Model):
    seo_title = models.CharField(_("Seo Title"), max_length=70, blank=True, null=True)
    seo_description = models.TextField(
        _("Seo Description"), max_length=300, blank=True, null=True
    )
    seo_keywords = models.CharField(
        _("Seo Keywords"), max_length=255, blank=True, null=True
    )

    class Meta:
        abstract = True
