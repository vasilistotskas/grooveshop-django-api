import sys

from setuptools import find_packages, setup

__version__ = "1.14.0"

setup(
    name="grooveshop-django-api",
    version=__version__,
    description="GrooveShop Setup",
    author="Vasilis Totskas",
    author_email="vassilistotskas@msn.com",
    packages=find_packages(),
    include_package_data=True,
)

try:
    from semantic_release import setup_hook

    setup_hook(sys.argv)
except ImportError:
    pass
