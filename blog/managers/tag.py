from parler.managers import TranslatableManager


class BlogTagManager(TranslatableManager):
    def get_queryset(self):
        return super().get_queryset().filter(active=True)
