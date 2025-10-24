import factory
from django.apps import apps
from django.conf import settings

from blog.models.tag import BlogTag

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class BlogTagTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker(
        "random_element",
        elements=[
            "Technology",
            "Innovation",
            "AI",
            "Software",
            "Web Development",
            "Mobile Apps",
            "Cybersecurity",
            "Cloud Computing",
            "Data Science",
            "Travel Tips",
            "Adventure",
            "Destinations",
            "Budget Travel",
            "Luxury Travel",
            "Recipes",
            "Cooking",
            "Healthy Eating",
            "Vegan",
            "Desserts",
            "Fitness",
            "Workout",
            "Nutrition",
            "Mental Health",
            "Wellness",
            "Fashion",
            "Style",
            "Beauty",
            "Skincare",
            "Makeup",
            "Business",
            "Entrepreneurship",
            "Startup",
            "Leadership",
            "Productivity",
            "Entertainment",
            "Movies",
            "Music",
            "Books",
            "TV Shows",
            "Sports",
            "Football",
            "Basketball",
            "Tennis",
            "Fitness Training",
            "Education",
            "Learning",
            "Online Courses",
            "Study Tips",
            "Career",
            "Finance",
            "Investing",
            "Saving Money",
            "Personal Finance",
            "Crypto",
            "DIY",
            "Crafts",
            "Home Improvement",
            "Gardening",
            "Woodworking",
            "Parenting",
            "Family",
            "Kids",
            "Pregnancy",
            "Child Development",
            "Photography",
            "Camera Tips",
            "Photo Editing",
            "Portrait",
            "Landscape",
            "Marketing",
            "SEO",
            "Social Media",
            "Content Creation",
            "Branding",
            "Science",
            "Research",
            "Climate",
            "Space",
            "Nature",
            "Politics",
            "News",
            "Current Events",
            "Opinion",
            "Analysis",
            "Sustainability",
            "Eco-Friendly",
            "Green Living",
            "Environment",
            "Zero Waste",
        ],
    )
    master = factory.SubFactory("blog.factories.tag.BlogTagFactory")

    class Meta:
        model = apps.get_model("blog", "BlogTagTranslation")
        django_get_or_create = ("language_code", "master")


class BlogTagFactory(factory.django.DjangoModelFactory):
    active = factory.Faker("boolean", chance_of_getting_true=90)

    class Meta:
        model = BlogTag
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted is not None:
            self.translations.all().delete()

        if extracted is None and not self.translations.exists():
            translations = [
                BlogTagTranslationFactory(language_code=lang, master=self)
                for lang in available_languages
            ]
        else:
            translations = extracted or []

        for translation in translations:
            translation.master = self
            translation.save()
