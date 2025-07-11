from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from django.test import TestCase

from core.utils.profiler import (
    ProfileLevel,
    FactoryExecutionMetrics,
    SessionMetrics,
    JSONMetricsExporter,
    QueryProfiler,
    MemoryProfiler,
    ProfilerConfig,
    FactoryProfiler,
    ReportBuilder,
    profile_factory_execution,
    benchmark_factories,
)


class TestProfileLevel(TestCase):
    def test_profile_levels(self):
        self.assertEqual(ProfileLevel.BASIC.value, "basic")
        self.assertEqual(ProfileLevel.DETAILED.value, "detailed")
        self.assertEqual(ProfileLevel.FULL.value, "full")


class TestFactoryExecutionMetrics(TestCase):
    def setUp(self):
        self.metrics = FactoryExecutionMetrics(
            factory_name="TestFactory",
            model_name="TestModel",
            count=100,
            start_time=1000.0,
            end_time=1002.0,
            memory_before=1024,
            memory_after=2048,
            query_count=50,
            query_time=0.5,
            success_count=95,
            error_count=5,
        )

    def test_initialization(self):
        self.assertEqual(self.metrics.factory_name, "TestFactory")
        self.assertEqual(self.metrics.model_name, "TestModel")
        self.assertEqual(self.metrics.count, 100)
        self.assertEqual(self.metrics.start_time, 1000.0)
        self.assertEqual(self.metrics.end_time, 1002.0)

    def test_post_init(self):
        self.assertEqual(self.metrics.memory_delta, 1024)

    def test_finalize_metrics(self):
        self.metrics.finalize_metrics()
        self.assertEqual(self.metrics.duration, 2.0)
        self.assertEqual(self.metrics.memory_delta, 1024)

    def test_success_rate(self):
        self.assertEqual(self.metrics.success_rate, 95.0)

        empty_metrics = FactoryExecutionMetrics(
            factory_name="Empty",
            model_name="Empty",
            count=0,
            start_time=0,
            end_time=0,
            success_count=0,
            error_count=0,
        )
        self.assertEqual(empty_metrics.success_rate, 0.0)

    def test_avg_time_per_record(self):
        self.metrics.duration = 2.0
        self.assertEqual(self.metrics.avg_time_per_record, 20.0)

        self.metrics.count = 0
        self.assertEqual(self.metrics.avg_time_per_record, 0.0)

    def test_avg_time_per_record_microseconds(self):
        self.metrics.duration = 2.0
        self.assertEqual(self.metrics.avg_time_per_record_microseconds, 20000.0)

    def test_memory_per_record(self):
        expected = 1024 / 1024 / 100
        self.assertEqual(self.metrics.memory_per_record, expected)

        self.metrics.memory_peak = 3000
        expected_peak = (3000 - 1024) / 1024 / 100
        self.assertEqual(self.metrics.memory_per_record, expected_peak)

    def test_queries_per_record(self):
        self.assertEqual(self.metrics.queries_per_record, 0.5)

    def test_avg_query_time(self):
        self.assertEqual(self.metrics.avg_query_time, 10.0)

        self.metrics.query_count = 0
        self.assertEqual(self.metrics.avg_query_time, 0.0)

    @patch("time.perf_counter")
    def test_add_memory_snapshot(self, mock_perf_counter):
        mock_perf_counter.return_value = 1001.5

        self.metrics.add_memory_snapshot(1500)

        self.assertEqual(len(self.metrics.memory_snapshots), 1)
        timestamp, memory = self.metrics.memory_snapshots[0]
        self.assertGreaterEqual(timestamp, 0)
        self.assertAlmostEqual(timestamp, 1.5, places=1)
        self.assertEqual(memory, 1500)

    @patch("time.perf_counter")
    def test_add_slow_query(self, mock_perf_counter):
        mock_perf_counter.return_value = 1001.2

        long_query = "SELECT * FROM test WHERE " + "x" * 300
        self.metrics.add_slow_query(long_query, 0.1)

        self.assertEqual(len(self.metrics.slow_queries), 1)
        slow_query = self.metrics.slow_queries[0]
        self.assertEqual(len(slow_query["query"]), 200)
        self.assertEqual(slow_query["duration"], 0.1)
        self.assertAlmostEqual(slow_query["timestamp"], 1.2, places=1)
        self.assertIn("timestamp", slow_query)


class TestSessionMetrics(TestCase):
    def setUp(self):
        self.session = SessionMetrics(
            start_time=1000.0,
            end_time=1010.0,
            total_factories=3,
            total_records=300,
            total_successes=285,
            total_errors=15,
            total_memory_delta=1024 * 1024,
            total_queries=150,
        )

    def test_total_duration(self):
        self.assertEqual(self.session.total_duration, 10.0)

    def test_overall_success_rate(self):
        self.assertEqual(self.session.overall_success_rate, 95.0)

    def test_avg_records_per_second(self):
        self.assertEqual(self.session.avg_records_per_second, 30.0)

    def test_to_dict(self):
        factory_metrics = FactoryExecutionMetrics(
            factory_name="TestFactory",
            model_name="TestModel",
            count=100,
            start_time=1000.0,
            end_time=1002.0,
        )
        self.session.factory_metrics = [factory_metrics]

        result = self.session.to_dict()

        self.assertIn("session", result)
        self.assertIn("factories", result)
        self.assertIn("analysis", result)

        session_data = result["session"]
        self.assertEqual(session_data["total_factories"], 3)
        self.assertEqual(session_data["total_records"], 300)
        self.assertEqual(session_data["overall_success_rate"], 95.0)

        factories_data = result["factories"]
        self.assertEqual(len(factories_data), 1)
        self.assertEqual(factories_data[0]["name"], "TestFactory")


class TestJSONMetricsExporter(TestCase):
    def setUp(self):
        self.exporter = JSONMetricsExporter()
        self.session = SessionMetrics(start_time=1000.0, end_time=1010.0)

    @patch("builtins.open")
    @patch("json.dump")
    def test_export(self, mock_json_dump, mock_open):
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        test_path = Path("/tmp/test_metrics.json")
        self.exporter.export(self.session, test_path)

        mock_open.assert_called_once_with(test_path, "w")
        mock_json_dump.assert_called_once()
        args, kwargs = mock_json_dump.call_args
        self.assertEqual(args[1], mock_file)
        self.assertEqual(kwargs["indent"], 2)


class TestQueryProfiler(TestCase):
    def setUp(self):
        self.profiler = QueryProfiler(
            track_slow_queries=True, slow_query_threshold=0.1
        )

    def test_initialization(self):
        self.assertTrue(self.profiler.track_slow_queries)
        self.assertEqual(self.profiler.slow_query_threshold, 0.1)
        self.assertEqual(self.profiler.initial_query_count, 0)
        self.assertEqual(len(self.profiler.slow_queries), 0)

    @patch("django.db.connection.queries_log")
    def test_start(self, mock_queries_log):
        mock_queries_log.clear = Mock()

        with patch("django.db.connection") as mock_connection:
            mock_connection.queries = []
            self.profiler.start()

        mock_queries_log.clear.assert_called_once()
        self.assertEqual(self.profiler.initial_query_count, 0)

    @patch("django.db.connection.queries_log")
    def test_stop(self, mock_queries_log):
        self.profiler.initial_query_count = 0

        mock_queries_log.__len__.return_value = 2
        mock_queries_log.__iter__.return_value = [
            {"sql": "SELECT 1", "time": "0.05"},
            {"sql": "SELECT * FROM large_table", "time": "0.15"},
        ]

        query_count, query_time, slow_queries = self.profiler.stop()

        self.assertEqual(query_count, 2)
        self.assertEqual(query_time, 0.2)
        self.assertEqual(len(slow_queries), 1)
        self.assertEqual(slow_queries[0]["query"], "SELECT * FROM large_table")

    def test_stop_without_start(self):
        result = self.profiler.stop()
        self.assertEqual(result, (0, 0.0, []))


class TestMemoryProfiler(TestCase):
    def setUp(self):
        self.profiler = MemoryProfiler(
            track_snapshots=True, snapshot_interval=1.0
        )

    def test_initialization(self):
        self.assertTrue(self.profiler.track_snapshots)
        self.assertEqual(self.profiler.snapshot_interval, 1.0)
        self.assertEqual(self.profiler.initial_memory, 0)
        self.assertEqual(len(self.profiler.snapshots), 0)

    @patch("tracemalloc.start")
    @patch("tracemalloc.get_traced_memory")
    def test_start(self, mock_get_memory, mock_start):
        mock_get_memory.return_value = (1024, 1024)

        self.profiler.start()

        mock_start.assert_called_once()
        self.assertEqual(self.profiler.initial_memory, 1024)

    @patch("psutil.Process")
    @patch("tracemalloc.is_tracing", return_value=True)
    @patch("tracemalloc.get_traced_memory")
    def test_get_memory_usage_with_psutil(
        self, mock_get_memory, mock_is_tracing, mock_process_class
    ):
        mock_process = Mock()
        mock_process.memory_info.return_value = Mock(rss=2048 * 1024)
        mock_process_class.return_value = mock_process
        mock_get_memory.return_value = (1024, 2048)

        result = self.profiler.update_peak()

        self.assertEqual(result, 1024)

    @patch("tracemalloc.is_tracing", return_value=True)
    @patch("tracemalloc.get_traced_memory")
    def test_get_memory_usage_fallback(self, mock_get_memory, mock_is_tracing):
        mock_get_memory.return_value = (1024, 2048)

        with patch("core.utils.profiler.psutil", None):
            result = self.profiler.update_peak()
            self.assertEqual(result, 1024)

    @patch("tracemalloc.is_tracing", return_value=True)
    @patch("tracemalloc.get_traced_memory")
    def test_update_peak(self, mock_get_memory, mock_is_tracing):
        mock_get_memory.return_value = (1024, 2048)

        self.profiler.peak_memory = 1024
        result = self.profiler.update_peak()

        self.assertEqual(result, 1024)
        self.assertEqual(self.profiler.peak_memory, 2048)

    @patch("tracemalloc.is_tracing", return_value=True)
    @patch("tracemalloc.get_traced_memory")
    @patch("tracemalloc.stop")
    def test_stop(self, mock_stop, mock_get_memory, mock_is_tracing):
        self.profiler._tracemalloc_started = True
        mock_get_memory.return_value = (1536, 2048)

        before, after, peak, snapshots = self.profiler.stop()

        self.assertIsInstance(before, int)
        self.assertIsInstance(after, int)
        self.assertIsInstance(peak, int)
        self.assertIsInstance(snapshots, list)

        mock_stop.assert_called_once()


class TestProfilerConfig(TestCase):
    def test_default_configuration(self):
        config = ProfilerConfig()

        self.assertEqual(config.profile_level, ProfileLevel.FULL)
        self.assertTrue(config.enable_query_profiling)
        self.assertTrue(config.enable_memory_profiling)
        self.assertTrue(config.track_slow_queries)
        self.assertEqual(config.slow_query_threshold, 0.1)
        self.assertFalse(config.track_memory_snapshots)
        self.assertEqual(config.memory_snapshot_interval, 1.0)
        self.assertIsNone(config.exporters)

    def test_custom_configuration(self):
        config = ProfilerConfig(
            profile_level=ProfileLevel.BASIC,
            enable_query_profiling=False,
            slow_query_threshold=0.2,
        )

        self.assertEqual(config.profile_level, ProfileLevel.BASIC)
        self.assertFalse(config.enable_query_profiling)
        self.assertEqual(config.slow_query_threshold, 0.2)


class TestFactoryProfiler(TestCase):
    def setUp(self):
        self.config = ProfilerConfig(profile_level=ProfileLevel.DETAILED)
        self.profiler = FactoryProfiler(self.config)

    def test_initialization(self):
        self.assertEqual(self.profiler.profile_level, ProfileLevel.DETAILED)
        self.assertEqual(self.profiler.session_start_time, 0.0)
        self.assertEqual(len(self.profiler.metrics), 0)

    def test_start_session(self):
        with patch("time.perf_counter", return_value=1000.0):
            self.profiler.start_session()

        self.assertEqual(self.profiler.session_start_time, 1000.0)

    @patch("time.perf_counter")
    @patch.object(QueryProfiler, "start")
    @patch.object(QueryProfiler, "stop")
    @patch.object(MemoryProfiler, "start")
    @patch.object(MemoryProfiler, "stop")
    def test_profile_factory_context_manager(
        self,
        mock_mem_stop,
        mock_mem_start,
        mock_query_stop,
        mock_query_start,
        mock_time,
    ):
        mock_time.side_effect = [1000.0, 1002.0]

        mock_query_stop.return_value = (10, 0.1, [])
        mock_mem_stop.return_value = (1024, 2048, 2048, [])

        mock_factory = Mock()
        mock_factory.__name__ = "TestFactory"
        mock_factory._meta.model.__name__ = "TestModel"

        with self.profiler.profile_factory(mock_factory, 100) as metrics:
            metrics.success_count = 95
            metrics.error_count = 5

        mock_query_start.assert_called_once()
        mock_query_stop.assert_called_once()
        mock_mem_start.assert_called_once()
        mock_mem_stop.assert_called_once()

        self.assertEqual(len(self.profiler.metrics), 1)
        recorded_metrics = self.profiler.metrics[0]
        self.assertEqual(recorded_metrics.factory_name, "TestFactory")
        self.assertEqual(recorded_metrics.success_count, 95)
        self.assertEqual(recorded_metrics.error_count, 5)

    def test_get_session_metrics(self):
        self.profiler.session_start_time = 1000.0

        factory_metrics = FactoryExecutionMetrics(
            factory_name="TestFactory",
            model_name="TestModel",
            count=100,
            start_time=1000.0,
            end_time=1002.0,
            success_count=95,
            error_count=5,
        )
        self.profiler.metrics.append(factory_metrics)

        with patch("time.perf_counter", return_value=1010.0):
            session = self.profiler.get_session_metrics()

        self.assertEqual(session.start_time, 1000.0)
        self.assertEqual(session.end_time, 1010.0)
        self.assertEqual(session.total_factories, 1)
        self.assertEqual(session.total_records, 100)
        self.assertEqual(session.total_successes, 95)
        self.assertEqual(session.total_errors, 5)

    @patch.object(ReportBuilder, "build")
    def test_generate_report(self, mock_build):
        mock_build.return_value = "Test Report"

        self.profiler.session_start_time = 1000.0

        factory_metrics = FactoryExecutionMetrics(
            factory_name="TestFactory",
            model_name="TestModel",
            count=100,
            start_time=1000.0,
            end_time=1002.0,
            success_count=95,
            error_count=5,
        )
        self.profiler.metrics.append(factory_metrics)

        with patch("time.perf_counter", return_value=1010.0):
            report = self.profiler.generate_report()

        self.assertEqual(report, "Test Report")
        mock_build.assert_called_once()

    @patch.object(JSONMetricsExporter, "export")
    def test_export_metrics(self, mock_export):
        self.profiler.session_start_time = 1000.0
        export_path = Path("/tmp/test_export.json")

        with patch("time.perf_counter", return_value=1010.0):
            self.profiler.export_metrics(export_path)

        mock_export.assert_called_once()
        args = mock_export.call_args[0]
        self.assertIsInstance(args[0], SessionMetrics)
        self.assertEqual(args[1], export_path)


class TestReportBuilder(TestCase):
    def setUp(self):
        self.session = SessionMetrics(
            start_time=1000.0,
            end_time=1010.0,
            total_factories=2,
            total_records=200,
            total_successes=190,
            total_errors=10,
        )

        factory_metrics = FactoryExecutionMetrics(
            factory_name="TestFactory",
            model_name="TestModel",
            count=100,
            start_time=1000.0,
            end_time=1002.0,
            success_count=95,
            error_count=5,
            query_count=50,
            query_time=0.5,
        )
        factory_metrics.duration = 2.0
        self.session.factory_metrics = [factory_metrics]

        self.builder = ReportBuilder(self.session, ProfileLevel.FULL)

    def test_initialization(self):
        self.assertEqual(self.builder.session, self.session)
        self.assertEqual(self.builder.profile_level, ProfileLevel.FULL)
        self.assertEqual(len(self.builder.sections), 0)

    def test_add_header(self):
        self.builder.add_header()

        header_content = "\n".join(self.builder.sections)
        self.assertIn("Factory Execution Profiling Report", header_content)
        self.assertIn("Profile Level: FULL", header_content)

    def test_add_session_summary(self):
        self.builder.add_session_summary()

        summary_content = "\n".join(self.builder.sections)
        self.assertIn("Session Summary", summary_content)
        self.assertIn("Total Duration: 10.00s", summary_content)
        self.assertIn("Total Factories: 2", summary_content)
        self.assertIn("Total Records: 200", summary_content)

    def test_add_factory_performance_table(self):
        self.builder.add_factory_performance_table()

        table_content = "\n".join(self.builder.sections)
        self.assertIn("Factory Performance", table_content)
        self.assertIn("TestFactory", table_content)

    def test_build_complete_report(self):
        self.builder.add_header()
        self.builder.add_session_summary()
        self.builder.add_factory_performance_table()

        report = self.builder.build()

        self.assertIn("Factory Execution Profiling Report", report)
        self.assertIn("Session Summary", report)
        self.assertIn("Factory Performance", report)


class TestModuleFunctions(TestCase):
    @patch("core.utils.profiler.FactoryProfiler")
    def test_profile_factory_execution(self, mock_profiler_class):
        mock_metrics = FactoryExecutionMetrics(
            factory_name="TestFactory",
            model_name="TestModel",
            count=10,
            start_time=1000.0,
            end_time=1001.0,
        )
        mock_metrics.success_count = 0
        mock_metrics.error_count = 0
        mock_metrics.errors = []

        mock_profiler = Mock()
        mock_profiler_class.return_value = mock_profiler

        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_metrics
        mock_context.__exit__.return_value = None
        mock_profiler.profile_factory.return_value = mock_context

        mock_factory = Mock()
        mock_factory.create.return_value = Mock()

        result = profile_factory_execution(mock_factory, 10)

        self.assertEqual(result, mock_metrics)
        mock_profiler.profile_factory.assert_called_once_with(mock_factory, 10)

    @patch("core.utils.profiler.FactoryProfiler")
    def test_benchmark_factories(self, mock_profiler_class):
        mock_profiler = Mock()
        mock_profiler_class.return_value = mock_profiler
        mock_profiler.generate_report.return_value = "Benchmark Report"

        mock_metrics = Mock()
        mock_metrics.success_count = 0
        mock_metrics.error_count = 0
        mock_metrics.errors = []

        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_metrics
        mock_context.__exit__.return_value = None
        mock_profiler.profile_factory.return_value = mock_context

        mock_factory_1 = Mock()
        mock_factory_1.create.return_value = Mock()
        mock_factory_2 = Mock()
        mock_factory_2.create.return_value = Mock()
        factory_classes = [mock_factory_1, mock_factory_2]

        result = benchmark_factories(factory_classes, count=5)

        self.assertEqual(result, "Benchmark Report")
        mock_profiler.start_session.assert_called_once()
        self.assertEqual(mock_profiler.profile_factory.call_count, 2)
        mock_profiler.generate_report.assert_called_once()


class TestErrorHandling(TestCase):
    def test_memory_profiler_without_tracemalloc(self):
        profiler = MemoryProfiler()

        with (
            patch(
                "tracemalloc.is_tracing",
                return_value=True,
            ),
            patch(
                "tracemalloc.get_traced_memory",
                side_effect=Exception("tracemalloc not available"),
            ),
        ):
            memory = profiler.update_peak()
            self.assertIsNone(memory)

    def test_query_profiler_with_invalid_time(self):
        profiler = QueryProfiler()

        query_count, query_time, slow_queries = profiler.stop()

        self.assertEqual(query_count, 0)
        self.assertEqual(query_time, 0.0)
        self.assertEqual(len(slow_queries), 0)

    def test_factory_profiler_without_session_start(self):
        profiler = FactoryProfiler()

        session = profiler.get_session_metrics()
        self.assertIsNotNone(session)
        self.assertEqual(session.total_factories, 0)
