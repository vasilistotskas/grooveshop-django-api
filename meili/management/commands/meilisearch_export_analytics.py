"""
Management command to export search analytics data.

This command exports search analytics data (SearchQuery and SearchClick records)
to JSON format for analysis, reporting, or backup purposes.
"""

import json
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import gettext as _

from search.models import SearchClick, SearchQuery


class Command(BaseCommand):
    help = _("Export search analytics data to JSON format")

    def add_arguments(self, parser):
        parser.add_argument(
            "--start-date",
            type=str,
            help=_("Start date for export (ISO format: YYYY-MM-DD)"),
            required=False,
        )
        parser.add_argument(
            "--end-date",
            type=str,
            help=_("End date for export (ISO format: YYYY-MM-DD)"),
            required=False,
        )
        parser.add_argument(
            "--output",
            type=str,
            help=_("Output file path (default: analytics_export.json)"),
            default="analytics_export.json",
        )
        parser.add_argument(
            "--include-clicks",
            action="store_true",
            help=_("Include click data in export"),
        )

    def handle(self, *args, **options):
        start_date_str = options.get("start_date")
        end_date_str = options.get("end_date")
        output_file = options["output"]
        include_clicks = options.get("include_clicks", False)

        # Display export parameters
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("SEARCH ANALYTICS EXPORT")
        self.stdout.write("=" * 60)

        if start_date_str:
            self.stdout.write(f"Start date: {start_date_str}")
        else:
            self.stdout.write("Start date: (all historical data)")

        if end_date_str:
            self.stdout.write(f"End date: {end_date_str}")
        else:
            self.stdout.write("End date: (up to current date)")

        self.stdout.write(f"Output file: {output_file}")
        self.stdout.write(
            f"Include clicks: {'Yes' if include_clicks else 'No'}"
        )

        # Build queryset
        queries_qs = SearchQuery.objects.all()

        # Apply date filters
        if start_date_str:
            try:
                start_date = datetime.fromisoformat(start_date_str)
                # Make timezone-aware if naive
                if timezone.is_naive(start_date):
                    start_date = timezone.make_aware(start_date)
                queries_qs = queries_qs.filter(timestamp__gte=start_date)
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(
                        f"Invalid start_date format: {start_date_str}. "
                        "Use ISO format: YYYY-MM-DD"
                    )
                )
                return

        if end_date_str:
            try:
                end_date = datetime.fromisoformat(end_date_str)
                # Make timezone-aware if naive
                if timezone.is_naive(end_date):
                    end_date = timezone.make_aware(end_date)
                queries_qs = queries_qs.filter(timestamp__lte=end_date)
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(
                        f"Invalid end_date format: {end_date_str}. "
                        "Use ISO format: YYYY-MM-DD"
                    )
                )
                return

        # Count records
        total_queries = queries_qs.count()

        self.stdout.write(f"\nTotal queries to export: {total_queries}")

        if total_queries == 0:
            self.stdout.write(
                self.style.WARNING(
                    "\nNo queries found for the specified date range."
                )
            )
            return

        # Export data
        self.stdout.write("\nExporting data...")

        try:
            export_data = {
                "export_metadata": {
                    "export_date": datetime.now().isoformat(),
                    "start_date": start_date_str or "all",
                    "end_date": end_date_str or "now",
                    "total_queries": total_queries,
                    "include_clicks": include_clicks,
                },
                "queries": [],
            }

            # Export queries
            for query in queries_qs.iterator(chunk_size=1000):
                query_data = {
                    "id": query.id,
                    "query": query.query,
                    "language_code": query.language_code,
                    "content_type": query.content_type,
                    "results_count": query.results_count,
                    "estimated_total_hits": query.estimated_total_hits,
                    "processing_time_ms": query.processing_time_ms,
                    "user_id": query.user_id,
                    "session_key": query.session_key,
                    "ip_address": str(query.ip_address)
                    if query.ip_address
                    else None,
                    "user_agent": query.user_agent,
                    "timestamp": query.timestamp.isoformat(),
                }

                # Include clicks if requested
                if include_clicks:
                    clicks = SearchClick.objects.filter(search_query=query)
                    query_data["clicks"] = [
                        {
                            "id": click.id,
                            "result_id": click.result_id,
                            "result_type": click.result_type,
                            "position": click.position,
                            "timestamp": click.timestamp.isoformat(),
                        }
                        for click in clicks
                    ]
                    query_data["clicks_count"] = len(query_data["clicks"])

                export_data["queries"].append(query_data)

            # Write to file
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Successfully exported {total_queries} queries to {output_file}"
                )
            )

            # Display summary statistics
            self.stdout.write("\nExport summary:")
            self.stdout.write(f"  - Total queries: {total_queries}")

            if include_clicks:
                total_clicks = SearchClick.objects.filter(
                    search_query__in=queries_qs
                ).count()
                self.stdout.write(f"  - Total clicks: {total_clicks}")

                if total_queries > 0:
                    ctr = (total_clicks / total_queries) * 100
                    self.stdout.write(f"  - Click-through rate: {ctr:.2f}%")

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n✗ Export failed: {str(e)}"))
            import traceback

            if options.get("verbosity", 1) >= 2:
                self.stdout.write("\nTraceback:")
                self.stdout.write(traceback.format_exc())
