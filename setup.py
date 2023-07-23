import sys

from setuptools import setup

__version__ = "0.8.9"

setup(
    name="grooveshop-django-api",
    version=__version__,
    description="GrooveShop Setup",
    author="Vasilis Totskas",
    author_email="vassilistotskas@msn.com",
)

try:
    from semantic_release import setup_hook
    setup_hook(sys.argv)
except ImportError:
    pass
