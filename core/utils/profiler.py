import json
import logging
import time
import tracemalloc
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from django.db import connection

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger(__name__)


class ProfileLevel(Enum):
    """Profiling detail levels"""

    BASIC = "basic"
    DETAILED = "detailed"
    FULL = "full"


class MetricsExporter(Protocol):
    """Protocol for metrics exporters"""

    def export(self, metrics: "SessionMetrics", path: Path) -> None:
        """Export metrics to a file"""
        ...


@dataclass
class FactoryExecutionMetrics:
    """Metrics for a single factory execution"""

    factory_name: str
    model_name: str
    count: int
    start_time: float
    end_time: float
    duration: float = field(init=False, default=0.0)
    memory_before: int = 0
    memory_after: int = 0
    memory_peak: int = 0
    memory_delta: int = field(init=False, default=0)
    query_count: int = 0
    query_time: float = 0.0
    success_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)
    slow_queries: list[dict[str, Any]] = field(default_factory=list)
    memory_snapshots: list[tuple[float, int]] = field(default_factory=list)

    def __post_init__(self):
        self.memory_delta = self.memory_after - self.memory_before

    def finalize_metrics(self):
        """Calculate final metrics after end_time is set"""
        self.duration = self.end_time - self.start_time
        self.memory_delta = self.memory_after - self.memory_before

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage"""
        total = self.success_count + self.error_count
        return (self.success_count / total * 100) if total > 0 else 0.0

    @property
    def avg_time_per_record(self) -> float:
        """Average time per record in milliseconds"""
        return (self.duration * 1000 / self.count) if self.count > 0 else 0.0

    @property
    def avg_time_per_record_microseconds(self) -> float:
        """Average time per record in microseconds for high precision"""
        return (self.duration * 1000000 / self.count) if self.count > 0 else 0.0

    @property
    def memory_per_record(self) -> float:
        """Memory usage per record in KB"""
        memory_to_use = max(
            abs(self.memory_delta),
            self.memory_peak - self.memory_before
            if self.memory_peak > self.memory_before
            else 0,
        )
        return (memory_to_use / 1024 / self.count) if self.count > 0 else 0.0

    @property
    def queries_per_record(self) -> float:
        """Average queries per record"""
        return (self.query_count / self.count) if self.count > 0 else 0.0

    @property
    def avg_query_time(self) -> float:
        """Average query time in milliseconds"""
        return (
            (self.query_time * 1000 / self.query_count)
            if self.query_count > 0
            else 0.0
        )

    def add_memory_snapshot(self, memory_usage: int) -> None:
        """Add a memory usage snapshot"""
        timestamp = time.perf_counter() - self.start_time
        self.memory_snapshots.append((timestamp, memory_usage))

    def add_slow_query(self, query: str, duration: float) -> None:
        """Add a slow query to tracking"""
        self.slow_queries.append(
            {
                "query": query[:200],
                "duration": duration,
                "timestamp": time.perf_counter() - self.start_time,
            }
        )


@dataclass
class SessionMetrics:
    """Overall session metrics"""

    start_time: float
    end_time: float
    total_factories: int = 0
    total_records: int = 0
    total_successes: int = 0
    total_errors: int = 0
    total_memory_delta: int = 0
    total_queries: int = 0
    factory_metrics: list[FactoryExecutionMetrics] = field(default_factory=list)
    profile_level: ProfileLevel = ProfileLevel.FULL
    slowest_factories: list[str] = field(default_factory=list)
    memory_intensive_factories: list[str] = field(default_factory=list)

    @property
    def total_duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def overall_success_rate(self) -> float:
        total = self.total_successes + self.total_errors
        return (self.total_successes / total * 100) if total > 0 else 0.0

    @property
    def avg_records_per_second(self) -> float:
        return (
            (self.total_records / self.total_duration)
            if self.total_duration > 0
            else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary for export"""
        return {
            "session": {
                "start_time": self.start_time,
                "end_time": self.end_time,
                "duration": self.total_duration,
                "profile_level": self.profile_level.value,
                "total_factories": self.total_factories,
                "total_records": self.total_records,
                "total_successes": self.total_successes,
                "total_errors": self.total_errors,
                "overall_success_rate": self.overall_success_rate,
                "avg_records_per_second": self.avg_records_per_second,
                "total_memory_delta_mb": self.total_memory_delta / 1024 / 1024,
                "total_queries": self.total_queries,
            },
            "factories": [
                {
                    "name": m.factory_name,
                    "model": m.model_name,
                    "count": m.count,
                    "duration": m.duration,
                    "success_rate": m.success_rate,
                    "avg_time_per_record": m.avg_time_per_record,
                    "memory_per_record_kb": m.memory_per_record,
                    "queries_per_record": m.queries_per_record,
                    "errors": len(m.errors),
                }
                for m in self.factory_metrics
            ],
            "analysis": {
                "slowest_factories": self.slowest_factories,
                "memory_intensive_factories": self.memory_intensive_factories,
            },
        }


class JSONMetricsExporter:
    """Export metrics to JSON format"""

    def export(self, metrics: SessionMetrics, path: Path) -> None:
        """Export metrics to JSON file"""
        with open(path, "w") as f:
            json.dump(metrics.to_dict(), f, indent=2)


class QueryProfiler:
    """Profiles database queries during factory execution"""

    def __init__(
        self, track_slow_queries: bool = True, slow_query_threshold: float = 0.1
    ):
        self.track_slow_queries = track_slow_queries
        self.slow_query_threshold = slow_query_threshold
        self.initial_query_count = 0
        self.initial_query_time = 0.0
        self.slow_queries: list[dict[str, Any]] = []

    def start(self) -> None:
        """Start query profiling"""
        connection.queries_log.clear()
        self.initial_query_count = len(connection.queries)
        self.initial_query_time = 0.0

    def stop(self) -> tuple[int, float, list[dict[str, Any]]]:
        """Stop profiling and return metrics"""
        current_queries = connection.queries_log
        query_count = len(current_queries)

        total_query_time = sum(
            float(query.get("time", 0)) for query in current_queries
        )

        slow_queries = []
        if self.track_slow_queries:
            for query in current_queries:
                duration = float(query.get("time", 0))
                if duration > self.slow_query_threshold:
                    slow_queries.append(
                        {
                            "query": query["sql"][:200],
                            "duration": duration,
                            "timestamp": time.perf_counter(),
                        }
                    )

        return query_count, total_query_time, slow_queries


class MemoryProfiler:
    """Profiles memory usage during factory execution"""

    def __init__(
        self, track_snapshots: bool = False, snapshot_interval: float = 1.0
    ):
        self.track_snapshots = track_snapshots
        self.snapshot_interval = snapshot_interval
        self.initial_memory = 0
        self.current_memory = 0
        self.peak_memory = 0
        self.snapshots: list[tuple[float, int]] = []
        self._tracemalloc_started = False

        self.initial_process_memory = 0
        self.current_process_memory = 0
        self.peak_process_memory = 0

    def start(self) -> None:
        """Start memory profiling"""
        try:
            if psutil and not tracemalloc.is_tracing():
                try:
                    process = psutil.Process()
                    self.initial_memory = process.memory_info().rss
                    self.current_memory = self.initial_memory
                    self.peak_memory = self.initial_memory
                except Exception:
                    pass

            if not tracemalloc.is_tracing():
                tracemalloc.start()
                self._tracemalloc_started = True

                current, peak = tracemalloc.get_traced_memory()
                if current > 0:
                    self.initial_memory = current
                    self.current_memory = current
                    self.peak_memory = peak

        except Exception as e:
            logger.debug(f"Memory profiling setup: {e}")
            if not self.initial_memory:
                self.initial_memory = 0
                self.current_memory = 0
                self.peak_memory = 0

    def update_peak(self) -> int | None:
        """Update peak memory usage"""
        try:
            if tracemalloc.is_tracing():
                current, peak = tracemalloc.get_traced_memory()
                self.current_memory = current
                self.peak_memory = max(self.peak_memory, peak)

                if self.track_snapshots:
                    timestamp = time.perf_counter()
                    self.snapshots.append((timestamp, current))

            if psutil:
                try:
                    process = psutil.Process()
                    process_memory = process.memory_info().rss
                    self.current_process_memory = process_memory
                    self.peak_process_memory = max(
                        self.peak_process_memory, process_memory
                    )
                except Exception:
                    pass

            return self.current_memory
        except Exception as e:
            logger.warning(f"Failed to update memory: {e}")

        return None

    def stop(self) -> tuple[int, int, int, list[tuple[float, int]]]:
        """Stop profiling and return metrics"""
        try:
            if tracemalloc.is_tracing():
                current, peak = tracemalloc.get_traced_memory()
                self.current_memory = current
                self.peak_memory = max(self.peak_memory, peak)

                if self._tracemalloc_started:
                    tracemalloc.stop()

            if psutil:
                try:
                    process = psutil.Process()
                    process_memory = process.memory_info().rss
                    self.current_process_memory = process_memory
                    self.peak_process_memory = max(
                        self.peak_process_memory, process_memory
                    )
                except Exception:
                    pass

            memory_delta_process = abs(
                self.peak_process_memory - self.initial_process_memory
            )
            memory_delta_tracemalloc = abs(
                self.peak_memory - self.initial_memory
            )

            if memory_delta_process > memory_delta_tracemalloc and psutil:
                return (
                    self.initial_process_memory,
                    self.current_process_memory,
                    self.peak_process_memory,
                    self.snapshots.copy(),
                )
            else:
                return (
                    self.initial_memory,
                    self.current_memory,
                    self.peak_memory,
                    self.snapshots.copy(),
                )
        except Exception as e:
            logger.warning(f"Failed to stop memory profiling: {e}")
            return (0, 0, 0, [])


@dataclass
class ProfilerConfig:
    profile_level: ProfileLevel = ProfileLevel.FULL
    enable_query_profiling: bool = True
    enable_memory_profiling: bool = True
    track_slow_queries: bool = True
    slow_query_threshold: float = 0.1
    track_memory_snapshots: bool = False
    memory_snapshot_interval: float = 1.0
    exporters: list[MetricsExporter] | None = None


class FactoryProfiler:
    """Main profiler for factory execution analysis"""

    def __init__(
        self,
        config: ProfilerConfig = None,
    ):
        if config is None:
            config = ProfilerConfig()
        self.profile_level = config.profile_level
        self.enable_query_profiling = config.enable_query_profiling
        self.enable_memory_profiling = config.enable_memory_profiling
        self.query_profiler = (
            QueryProfiler(
                config.track_slow_queries, config.slow_query_threshold
            )
            if config.enable_query_profiling
            else None
        )
        self.memory_profiler = (
            MemoryProfiler(
                config.track_memory_snapshots, config.memory_snapshot_interval
            )
            if config.enable_memory_profiling
            else None
        )
        self.session_start_time = 0.0
        self.metrics: list[FactoryExecutionMetrics] = []
        self.exporters = config.exporters or [JSONMetricsExporter()]

    def start_session(self) -> None:
        """Start profiling session"""
        self.session_start_time = time.perf_counter()
        self.metrics.clear()

    @contextmanager
    def profile_factory(self, factory_class: type[Any], count: int):
        """Context manager for profiling a single factory execution"""
        factory_name = factory_class.__name__
        model_name = factory_class._meta.model.__name__

        start_time = time.perf_counter()

        metrics = FactoryExecutionMetrics(
            factory_name=factory_name,
            model_name=model_name,
            count=count,
            start_time=start_time,
            end_time=start_time,
        )

        if self.query_profiler:
            self.query_profiler.start()

        if self.memory_profiler:
            self.memory_profiler.start()
            metrics.memory_before = self.memory_profiler.initial_memory

        try:
            yield metrics

        finally:
            metrics.end_time = time.perf_counter()

            if self.query_profiler:
                metrics.query_count, metrics.query_time, slow_queries = (
                    self.query_profiler.stop()
                )
                if self.profile_level in (
                    ProfileLevel.DETAILED,
                    ProfileLevel.FULL,
                ):
                    metrics.slow_queries = slow_queries

            if self.memory_profiler:
                (
                    _,
                    metrics.memory_after,
                    metrics.memory_peak,
                    memory_snapshots,
                ) = self.memory_profiler.stop()

                if self.profile_level == ProfileLevel.FULL:
                    metrics.memory_snapshots = memory_snapshots

            metrics.finalize_metrics()

            self.metrics.append(metrics)

            if metrics.avg_time_per_record > 100:  # > 100ms per record
                logger.warning(
                    f"Slow factory detected: {factory_name} "
                    f"({metrics.avg_time_per_record:.1f}ms per record)"
                )

            if metrics.memory_per_record > 100:  # > 100KB per record
                logger.warning(
                    f"Memory-intensive factory detected: {factory_name} "
                    f"({metrics.memory_per_record:.1f}KB per record)"
                )

    def get_session_metrics(self) -> SessionMetrics:
        """Get comprehensive session metrics"""
        if not self.metrics:
            return SessionMetrics(
                start_time=self.session_start_time,
                end_time=time.perf_counter(),
                profile_level=self.profile_level,
            )

        session = SessionMetrics(
            start_time=self.session_start_time,
            end_time=time.perf_counter(),
            total_factories=len(self.metrics),
            factory_metrics=self.metrics.copy(),
            profile_level=self.profile_level,
        )

        for metric in self.metrics:
            session.total_records += metric.count
            session.total_successes += metric.success_count
            session.total_errors += metric.error_count
            memory_usage = max(
                abs(metric.memory_delta),
                metric.memory_peak - metric.memory_before
                if metric.memory_peak > metric.memory_before
                else 0,
            )
            session.total_memory_delta += memory_usage
            session.total_queries += metric.query_count

        if self.metrics:
            sorted_by_time = sorted(
                self.metrics, key=lambda m: m.avg_time_per_record, reverse=True
            )
            session.slowest_factories = [
                f"{m.factory_name} ({m.avg_time_per_record:.1f}ms/rec)"
                for m in sorted_by_time[:5]
            ]

            sorted_by_memory = sorted(
                self.metrics, key=lambda m: m.memory_per_record, reverse=True
            )
            session.memory_intensive_factories = [
                f"{m.factory_name} ({m.memory_per_record:.1f}KB/rec)"
                for m in sorted_by_memory[:5]
            ]

        return session

    def generate_report(
        self, show_details: bool = True, show_recommendations: bool = True
    ) -> str:
        """Generate a detailed profiling report"""
        session = self.get_session_metrics()

        if not self.metrics:
            return "No profiling data available"

        report_builder = ReportBuilder(session, self.profile_level)

        report_builder.add_header()
        report_builder.add_session_summary()

        if self.enable_memory_profiling:
            report_builder.add_memory_usage()

        if self.enable_query_profiling:
            report_builder.add_database_queries()

        if show_details:
            report_builder.add_factory_performance_table()

            if self.profile_level in (ProfileLevel.DETAILED, ProfileLevel.FULL):
                report_builder.add_slow_queries()

        report_builder.add_performance_analysis()

        if show_recommendations:
            report_builder.add_recommendations()

        return report_builder.build()

    def export_metrics(self, export_path: Path) -> None:
        """Export metrics using configured exporters"""
        session = self.get_session_metrics()

        for exporter in self.exporters:
            try:
                exporter.export(session, export_path)
                logger.info(
                    f"Exported metrics using {exporter.__class__.__name__}"
                )
            except Exception as e:
                logger.error(f"Failed to export metrics: {e}")


class ReportBuilder:
    """Builds formatted profiling reports"""

    def __init__(self, session: SessionMetrics, profile_level: ProfileLevel):
        self.session = session
        self.profile_level = profile_level
        self.sections: list[str] = []

    def add_header(self) -> None:
        """Add report header"""
        self.sections.extend(
            [
                "Factory Execution Profiling Report",
                "=" * 50,
                f"Profile Level: {self.profile_level.value.upper()}",
                "",
            ]
        )

    def add_session_summary(self) -> None:
        """Add session summary section"""
        self.sections.extend(
            [
                "📊 Session Summary:",
                f"  Total Duration: {self.session.total_duration:.2f}s",
                f"  Total Factories: {self.session.total_factories}",
                f"  Total Records: {self.session.total_records:,}",
                f"  Success Rate: {self.session.overall_success_rate:.1f}%",
                f"  Records/sec: {self.session.avg_records_per_second:.1f}",
                "",
            ]
        )

    def add_memory_usage(self) -> None:
        """Add memory usage section"""
        self.sections.extend(
            [
                "💾 Memory Usage:",
                f"  Total Delta: {self.session.total_memory_delta / 1024 / 1024:.1f} MB",
                f"  Avg per Record: {self.session.total_memory_delta / 1024 / self.session.total_records:.1f} KB"
                if self.session.total_records > 0
                else "  Avg per Record: 0.0 KB",
                "",
            ]
        )

    def add_database_queries(self) -> None:
        """Add database queries section"""
        avg_queries_per_record = (
            self.session.total_queries / self.session.total_records
            if self.session.total_records > 0
            else 0
        )

        self.sections.extend(
            [
                "🗄️ Database Queries:",
                f"  Total Queries: {self.session.total_queries:,}",
                f"  Avg per Record: {avg_queries_per_record:.1f}",
                "",
            ]
        )

    def add_factory_performance_table(self) -> None:
        """Add factory performance table"""
        use_microseconds = all(
            m.avg_time_per_record < 1.0 for m in self.session.factory_metrics
        )

        time_unit = "μs/rec" if use_microseconds else "ms/rec"

        header = f"{'Factory':<30} {'Records':<8} {'Time':<8} {time_unit:<8} {'Mem/KB':<10} {'Q/rec':<6} {'Success%':<8}"
        separator = "-" * len(header)

        if self.profile_level == ProfileLevel.FULL:
            header += f" {'Errors':<6}"
            separator = "-" * len(header)

        self.sections.extend(
            [
                "🏭 Individual Factory Performance:",
                header,
                separator,
            ]
        )

        sorted_metrics = sorted(
            self.session.factory_metrics,
            key=lambda m: m.avg_time_per_record,
            reverse=True,
        )

        for metric in sorted_metrics:
            time_per_record = (
                metric.avg_time_per_record_microseconds
                if use_microseconds
                else metric.avg_time_per_record
            )

            duration_str = (
                f"{metric.duration:.3f}"
                if metric.duration < 1.0
                else f"{metric.duration:.2f}"
            )
            time_per_record_str = (
                f"{time_per_record:.1f}"
                if time_per_record >= 1.0
                else f"{time_per_record:.2f}"
            )
            memory_str = (
                f"{metric.memory_per_record:.1f}"
                if metric.memory_per_record >= 1.0
                else f"{metric.memory_per_record:.2f}"
            )

            if metric.duration == 0:
                duration_str = "0.000"
            if time_per_record == 0:
                time_per_record_str = "0.00"

            row_parts = [
                f"{metric.factory_name:<30}",
                f"{metric.count:<8}",
                f"{duration_str:<8}",
                f"{time_per_record_str:<8}",
                f"{memory_str:<10}",
                f"{metric.queries_per_record:<6.1f}",
                f"{metric.success_rate:<8.1f}",
            ]

            if self.profile_level == ProfileLevel.FULL:
                row_parts.append(f"{metric.error_count:<6}")

            self.sections.append("".join(row_parts))

            if (
                self.profile_level in (ProfileLevel.DETAILED, ProfileLevel.FULL)
                and metric.errors
            ):
                for error in metric.errors[:3]:
                    self.sections.append(f"    ❌ {error}")
                if len(metric.errors) > 3:
                    self.sections.append(
                        f"    ... and {len(metric.errors) - 3} more errors"
                    )

        self.sections.append("")

    def add_slow_queries(self) -> None:
        """Add slow queries section"""
        all_slow_queries = []

        for metric in self.session.factory_metrics:
            for query in metric.slow_queries:
                all_slow_queries.append(
                    {
                        "factory": metric.factory_name,
                        "duration": query["duration"],
                        "query": query["query"],
                    }
                )

        if all_slow_queries:
            self.sections.extend(
                [
                    "🐌 Slow Queries Detected:",
                    f"  Total: {len(all_slow_queries)}",
                    "",
                ]
            )

            sorted_queries = sorted(
                all_slow_queries, key=lambda q: q["duration"], reverse=True
            )[:5]

            for i, query in enumerate(sorted_queries, 1):
                self.sections.extend(
                    [
                        f"  {i}. {query['factory']} - {query['duration']:.3f}s",
                        f"     {query['query'][:100]}...",
                    ]
                )

            self.sections.append("")

    def add_performance_analysis(self) -> None:
        """Add performance analysis section"""
        if len(self.session.factory_metrics) <= 1:
            return

        metrics = self.session.factory_metrics
        slowest = max(metrics, key=lambda m: m.avg_time_per_record)
        fastest = min(metrics, key=lambda m: m.avg_time_per_record)

        use_microseconds = all(m.avg_time_per_record < 1.0 for m in metrics)

        if use_microseconds:
            slowest_time = slowest.avg_time_per_record_microseconds
            fastest_time = fastest.avg_time_per_record_microseconds
            unit = "μs/record"
        else:
            slowest_time = slowest.avg_time_per_record
            fastest_time = fastest.avg_time_per_record
            unit = "ms/record"

        speed_ratio_str = (
            f"  Speed Ratio: {slowest_time / fastest_time:.1f}x"
            if fastest_time > 0
            else "  Speed Ratio: N/A (insufficient timing precision)"
        )

        self.sections.extend(
            [
                "⚡ Performance Analysis:",
                f"  Slowest: {slowest.factory_name} ({slowest_time:.2f} {unit})",
                f"  Fastest: {fastest.factory_name} ({fastest_time:.2f} {unit})",
                speed_ratio_str,
                "",
            ]
        )

    def add_recommendations(self) -> None:
        """Add recommendations section"""
        recommendations = []

        high_query_factories = [
            m for m in self.session.factory_metrics if m.queries_per_record > 10
        ]
        if high_query_factories:
            recommendations.append(
                "  - Consider optimizing queries for: "
                + ", ".join(f.factory_name for f in high_query_factories[:3])
            )

        slow_factories = [
            m
            for m in self.session.factory_metrics
            if m.avg_time_per_record > 100
        ]
        if slow_factories:
            recommendations.append(
                "  - Consider optimizing factories: "
                + ", ".join(f.factory_name for f in slow_factories[:3])
            )

        high_memory_factories = [
            m for m in self.session.factory_metrics if m.memory_per_record > 50
        ]
        if high_memory_factories:
            recommendations.append(
                "  - Consider memory optimization for: "
                + ", ".join(f.factory_name for f in high_memory_factories[:3])
            )

        if any(m.count > 1000 for m in self.session.factory_metrics):
            recommendations.append(
                "  - Consider increasing batch size for large datasets"
            )

        if all(
            m.avg_time_per_record < 1.0 for m in self.session.factory_metrics
        ):
            recommendations.append(
                "  - Factory execution is very fast (< 1ms/record). Consider using detailed profiling for microsecond analysis"
            )

        if recommendations:
            self.sections.extend(["💡 Recommendations:", *recommendations])

    def build(self) -> str:
        """Build the final report"""
        return "\n".join(self.sections)


def profile_factory_execution(
    factory_class: type[Any],
    count: int,
    profile_level: ProfileLevel = ProfileLevel.FULL,
    enable_query_profiling: bool = True,
    enable_memory_profiling: bool = True,
) -> FactoryExecutionMetrics:
    """Profile a single factory execution"""
    profiler = FactoryProfiler(
        profile_level=profile_level,
        enable_query_profiling=enable_query_profiling,
        enable_memory_profiling=enable_memory_profiling,
    )
    profiler.start_session()

    with profiler.profile_factory(factory_class, count) as metrics:
        for _ in range(count):
            try:
                factory_class.create()
                metrics.success_count += 1
            except Exception as e:
                metrics.error_count += 1
                metrics.errors.append(str(e))

    return metrics


def benchmark_factories(
    factory_classes: list[type[Any]],
    count: int = 10,
    profile_level: ProfileLevel = ProfileLevel.DETAILED,
) -> str:
    """Benchmark multiple factories and generate comparison report"""
    profiler = FactoryProfiler(profile_level=profile_level)
    profiler.start_session()

    for factory_class in factory_classes:
        with profiler.profile_factory(factory_class, count) as metrics:
            for _ in range(count):
                try:
                    factory_class.create()
                    metrics.success_count += 1
                except Exception as e:
                    metrics.error_count += 1
                    metrics.errors.append(str(e))

    return profiler.generate_report()
