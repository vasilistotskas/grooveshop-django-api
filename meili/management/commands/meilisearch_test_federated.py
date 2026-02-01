"""
Management command to test federated search functionality.

This command allows testing federated search with sample queries to verify
that multi-index search with federation is working correctly.
"""

import json

from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from blog.models.post import BlogPostTranslation
from core.utils.greeklish import expand_greeklish_query
from meili._client import client as meili_client
from product.models.product import ProductTranslation


class Command(BaseCommand):
    help = _("Test federated search functionality with sample queries")

    def add_arguments(self, parser):
        parser.add_argument(
            "--query",
            type=str,
            help=_("Search query to test"),
            required=True,
        )
        parser.add_argument(
            "--language-code",
            type=str,
            help=_("Language code filter (e.g., en, el, de)"),
            required=False,
        )
        parser.add_argument(
            "--limit",
            type=int,
            help=_("Maximum number of results to return"),
            default=10,
        )

    def handle(self, *args, **options):
        query = options["query"]
        language_code = options.get("language_code")
        limit = options["limit"]

        # Display test parameters
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("FEDERATED SEARCH TEST")
        self.stdout.write("=" * 60)
        self.stdout.write(f"\nQuery: {query}")
        if language_code:
            self.stdout.write(f"Language: {language_code}")
        self.stdout.write(f"Limit: {limit}")

        # Apply Greeklish expansion if needed
        decoded_query = query
        if language_code == "el":
            decoded_query = expand_greeklish_query(query, max_variants=5)
            self.stdout.write(f"Greeklish expanded: {decoded_query}")

        # Calculate result allocation (70% products, 30% blog posts)
        product_limit = int(limit * 0.7)
        blog_limit = limit - product_limit

        self.stdout.write("\nResult allocation:")
        self.stdout.write(f"  - Products: {product_limit} (70%)")
        self.stdout.write(f"  - Blog posts: {blog_limit} (30%)")

        # Build filters
        product_filters = []
        blog_filters = []

        if language_code:
            product_filters.append(f"language_code = '{language_code}'")
            blog_filters.append(f"language_code = '{language_code}'")

        # Content filtering
        product_filters.append("active = true")
        product_filters.append("is_deleted = false")
        blog_filters.append("is_published = true")

        self.stdout.write("\nFilters:")
        self.stdout.write(f"  - Products: {' AND '.join(product_filters)}")
        self.stdout.write(f"  - Blog posts: {' AND '.join(blog_filters)}")

        # Build multi_search query
        multi_search_params = {
            "federation": {},
            "queries": [
                {
                    "indexUid": ProductTranslation._meilisearch["index_name"],
                    "q": decoded_query,
                    "filter": product_filters,
                    "limit": product_limit,
                    "showMatchesPosition": True,
                    "showRankingScore": True,
                    "federationOptions": {"weight": 1.0},
                },
                {
                    "indexUid": BlogPostTranslation._meilisearch["index_name"],
                    "q": decoded_query,
                    "filter": blog_filters,
                    "limit": blog_limit,
                    "showMatchesPosition": True,
                    "showRankingScore": True,
                    "federationOptions": {"weight": 0.7},
                },
            ],
        }

        self.stdout.write("\n" + "-" * 60)
        self.stdout.write("EXECUTING FEDERATED SEARCH...")
        self.stdout.write("-" * 60)

        try:
            # Execute multi_search
            results = meili_client.client.multi_search(multi_search_params)

            # Extract results
            hits = results.get("hits", [])
            estimated_total_hits = results.get("estimatedTotalHits", 0)
            processing_time_ms = results.get("processingTimeMs", 0)

            # Display summary
            self.stdout.write(
                self.style.SUCCESS("\n✓ Search completed successfully")
            )
            self.stdout.write("\nResults summary:")
            self.stdout.write(f"  - Total hits: {len(hits)}")
            self.stdout.write(f"  - Estimated total: {estimated_total_hits}")
            self.stdout.write(f"  - Processing time: {processing_time_ms}ms")

            # Display results by content type
            product_results = []
            blog_results = []

            for hit in hits:
                federation = hit.get("_federation", {})
                index_uid = federation.get("indexUid", "")

                if "ProductTranslation" in index_uid:
                    product_results.append(hit)
                elif "BlogPostTranslation" in index_uid:
                    blog_results.append(hit)

            self.stdout.write("\nResults by content type:")
            self.stdout.write(f"  - Products: {len(product_results)}")
            self.stdout.write(f"  - Blog posts: {len(blog_results)}")

            # Display detailed results
            if hits:
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write("DETAILED RESULTS")
                self.stdout.write("=" * 60)

                for i, hit in enumerate(hits, 1):
                    federation = hit.get("_federation", {})
                    index_uid = federation.get("indexUid", "")
                    ranking_score = hit.get("_rankingScore", 0)
                    weighted_score = federation.get("weightedRankingScore", 0)

                    content_type = (
                        "Product"
                        if "ProductTranslation" in index_uid
                        else "Blog Post"
                    )

                    self.stdout.write(f"\n{i}. {content_type}")
                    self.stdout.write(f"   ID: {hit.get('id')}")
                    self.stdout.write(
                        f"   Name/Title: {hit.get('name') or hit.get('title')}"
                    )
                    self.stdout.write(f"   Ranking Score: {ranking_score:.4f}")
                    self.stdout.write(
                        f"   Weighted Score: {weighted_score:.4f}"
                    )
                    self.stdout.write(f"   Index: {index_uid}")

            else:
                self.stdout.write(
                    self.style.WARNING("\nNo results found for this query.")
                )

            # Display raw JSON (optional, for debugging)
            if options.get("verbosity", 1) >= 2:
                self.stdout.write("\n" + "=" * 60)
                self.stdout.write("RAW JSON RESPONSE")
                self.stdout.write("=" * 60)
                self.stdout.write(json.dumps(results, indent=2))

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"\n✗ Federated search failed: {str(e)}")
            )
            import traceback

            if options.get("verbosity", 1) >= 2:
                self.stdout.write("\nTraceback:")
                self.stdout.write(traceback.format_exc())
