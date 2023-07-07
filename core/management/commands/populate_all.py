import io
import time

from django.core import management
from django.core.management import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **options):
        start_time = time.time()
        with io.StringIO() as out:
            populate_users_start_time = time.time()
            management.call_command("populate_users", stdout=out)
            populate_users_response = str(out.getvalue())
            populate_users_end_time = round(
                (time.time() - populate_users_start_time), 2
            )

        with io.StringIO() as out:
            populate_products_start_time = time.time()
            management.call_command("populate_products", stdout=out)
            populate_products_response = str(out.getvalue())
            populate_products_end_time = round(
                (time.time() - populate_products_start_time), 2
            )

        with io.StringIO() as out:
            populate_blog_start_time = time.time()
            management.call_command("populate_blog", stdout=out)
            populate_blog_response = str(out.getvalue())
            populate_blog_end_time = round((time.time() - populate_blog_start_time), 2)

        with io.StringIO() as out:
            populate_reviews_start_time = time.time()
            management.call_command("populate_reviews", stdout=out)
            populate_reviews_response = str(out.getvalue())
            populate_reviews_end_time = round(
                (time.time() - populate_reviews_start_time), 2
            )

        with io.StringIO() as out:
            populate_countries_start_time = time.time()
            management.call_command("populate_countries", stdout=out)
            populate_countries_response = str(out.getvalue())
            populate_countries_end_time = round(
                (time.time() - populate_countries_start_time), 2
            )

        with io.StringIO() as out:
            populate_sliders_start_time = time.time()
            management.call_command("populate_sliders", stdout=out)
            populate_sliders_response = str(out.getvalue())
            populate_sliders_end_time = round(
                (time.time() - populate_sliders_start_time), 2
            )

        with io.StringIO() as out:
            populate_tips_start_time = time.time()
            management.call_command("populate_tips", stdout=out)
            populate_tips_response = str(out.getvalue())
            populate_tips_end_time = round((time.time() - populate_tips_start_time), 2)

        with io.StringIO() as out:
            populate_orders_start_time = time.time()
            management.call_command("populate_orders", stdout=out)
            populate_orders_response = str(out.getvalue())
            populate_orders_end_time = round(
                (time.time() - populate_orders_start_time), 2
            )

        self.stdout.write(
            f"populate_users_response : {populate_users_response} ---> "
            f"{populate_users_end_time} seconds"
        )
        self.stdout.write(
            f"populate_products_response : {populate_products_response} ---> "
            f"{populate_products_end_time} seconds"
        )
        self.stdout.write(
            f"populate_blog_response : {populate_blog_response} ---> "
            f"{populate_blog_end_time} seconds"
        )
        self.stdout.write(
            f"populate_reviews_response : {populate_reviews_response} ---> "
            f"{populate_reviews_end_time} seconds"
        )
        self.stdout.write(
            f"populate_countries_response : {populate_countries_response} ---> "
            f"{populate_countries_end_time} seconds"
        )
        self.stdout.write(
            f"populate_sliders_response : {populate_sliders_response} ---> "
            f"{populate_sliders_end_time} seconds"
        )
        self.stdout.write(
            f"populate_tips_response : {populate_tips_response} ---> "
            f"{populate_tips_end_time} seconds"
        )
        self.stdout.write(
            f"populate_orders_response : {populate_orders_response} ---> "
            f"{populate_orders_end_time} seconds"
        )

        self.stdout.write(f"Succeed in {round((time.time() - start_time), 2)} seconds")
