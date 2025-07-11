from unittest.mock import Mock, patch

import factory
from django.db import models
from django.db.models.fields.related import ForeignKey, OneToOneField
from django.test import TestCase

from core.utils.dependencies import (
    DependencyNode,
    CircularDependencyError,
    DependencyAnalyzer,
    FactoryOrchestrator,
    get_factory_execution_order,
    analyze_factory_dependencies,
)


class MockUser(models.Model):
    class Meta:
        app_label = "test"


class MockProfile(models.Model):
    user = models.OneToOneField(MockUser, on_delete=models.CASCADE)

    class Meta:
        app_label = "test"


class MockPost(models.Model):
    author = models.ForeignKey(MockUser, on_delete=models.CASCADE)

    class Meta:
        app_label = "test"


class MockComment(models.Model):
    post = models.ForeignKey(MockPost, on_delete=models.CASCADE)
    author = models.ForeignKey(MockUser, on_delete=models.CASCADE)

    class Meta:
        app_label = "test"


class MockUserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MockUser

    execution_priority = 0


class MockProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MockProfile

    user = factory.SubFactory(MockUserFactory)
    execution_priority = 1


class MockPostFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MockPost

    author = factory.SubFactory(MockUserFactory)
    execution_priority = 2


class MockCommentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MockComment

    post = factory.SubFactory(MockPostFactory)
    author = factory.SubFactory(MockUserFactory)
    execution_priority = 3


class MockConditionalFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MockUser

    conditional_depends_on = {
        "admin_required": ["MockUserFactory"],
        "has_inventory": ["MockProfileFactory"],
    }
    execution_priority = 5


class MockExplicitDepsFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MockUser

    depends_on = ["MockUserFactory", "MockProfileFactory"]
    business_logic_deps = ["MockPostFactory"]
    execution_priority = 4
    locale_aware = True
    business_rules_enabled = True


class TestDependencyNode(TestCase):
    def setUp(self):
        self.node = DependencyNode(MockUserFactory)

    def test_node_initialization(self):
        self.assertEqual(self.node.factory_class, MockUserFactory)
        self.assertEqual(self.node.model_class, MockUser)
        self.assertEqual(self.node.dependencies, set())
        self.assertEqual(self.node.dependents, set())
        self.assertEqual(self.node.level, 0)
        self.assertEqual(self.node.execution_priority, 0)

    def test_node_properties(self):
        self.assertEqual(self.node.name, "MockUserFactory")
        self.assertEqual(self.node.model_name, "MockUser")

    def test_add_dependency(self):
        profile_node = DependencyNode(MockProfileFactory)
        self.node.add_dependency(profile_node, "FK")

        self.assertIn(profile_node, self.node.dependencies)
        self.assertIn(self.node, profile_node.dependents)
        self.assertEqual(self.node.dependency_types["MockProfileFactory"], "FK")

    def test_conditional_dependencies_parsing(self):
        conditional_node = DependencyNode(MockConditionalFactory)

        self.assertEqual(
            conditional_node.conditional_dependencies,
            {
                "admin_required": ["MockUserFactory"],
                "has_inventory": ["MockProfileFactory"],
            },
        )

    def test_explicit_dependencies_parsing(self):
        explicit_node = DependencyNode(MockExplicitDepsFactory)

        self.assertEqual(
            explicit_node.explicit_dependencies,
            ["MockUserFactory", "MockProfileFactory"],
        )
        self.assertEqual(explicit_node.business_logic_deps, ["MockPostFactory"])
        self.assertEqual(explicit_node.execution_priority, 4)
        self.assertTrue(explicit_node.locale_aware)
        self.assertTrue(explicit_node.business_rules_enabled)

    def test_evaluate_condition(self):
        conditional_node = DependencyNode(MockConditionalFactory)

        context_admin = {"create_admin_users": True}
        self.assertTrue(
            conditional_node._evaluate_condition(
                "admin_required", context_admin
            )
        )

        context_no_admin = {"create_admin_users": False}
        self.assertFalse(
            conditional_node._evaluate_condition(
                "admin_required", context_no_admin
            )
        )

        context_inventory = {"create_inventory": True}
        self.assertTrue(
            conditional_node._evaluate_condition(
                "has_inventory", context_inventory
            )
        )

        context_no_inventory = {"create_inventory": False}
        self.assertFalse(
            conditional_node._evaluate_condition(
                "has_inventory", context_no_inventory
            )
        )

    def test_get_conditional_deps_for_context(self):
        conditional_node = DependencyNode(MockConditionalFactory)

        context = {"create_admin_users": True, "create_inventory": False}
        deps = conditional_node.get_conditional_deps_for_context(context)
        self.assertEqual(deps, ["MockUserFactory"])

        context = {"create_admin_users": False, "create_inventory": True}
        deps = conditional_node.get_conditional_deps_for_context(context)
        self.assertEqual(deps, ["MockProfileFactory"])

    def test_string_representations(self):
        self.assertEqual(
            str(self.node), "MockUserFactory (level 0, priority 0)"
        )
        self.assertEqual(
            repr(self.node),
            "DependencyNode(MockUserFactory, level=0, priority=0)",
        )


class TestCircularDependencyError(TestCase):
    def test_circular_dependency_error(self):
        node1 = DependencyNode(MockUserFactory)
        node2 = DependencyNode(MockProfileFactory)
        cycle = [node1, node2, node1]

        error = CircularDependencyError(cycle)
        self.assertEqual(error.cycle, cycle)
        expected_message = "Circular dependency detected: MockUserFactory -> MockProfileFactory -> MockUserFactory"
        self.assertEqual(str(error), expected_message)


class TestDependencyAnalyzer(TestCase):
    def setUp(self):
        self.analyzer = DependencyAnalyzer()

    def test_analyzer_initialization(self):
        self.assertEqual(self.analyzer.nodes, {})
        self.assertEqual(self.analyzer.model_to_factory, {})

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_create_nodes(self, mock_is_translation):
        mock_is_translation.return_value = False

        factories = [MockUserFactory, MockProfileFactory]
        self.analyzer._create_nodes(factories)

        self.assertIn("MockUserFactory", self.analyzer.nodes)
        self.assertIn("MockProfileFactory", self.analyzer.nodes)
        self.assertEqual(len(self.analyzer.nodes), 2)

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_create_nodes_skips_translation_factories(
        self, mock_is_translation
    ):
        def side_effect(factory_class):
            return factory_class.__name__ == "TranslationFactory"

        mock_is_translation.side_effect = side_effect

        class TranslationFactory(factory.django.DjangoModelFactory):
            class Meta:
                model = MockUser

        factories = [MockUserFactory, TranslationFactory]
        self.analyzer._create_nodes(factories)

        self.assertIn("MockUserFactory", self.analyzer.nodes)
        self.assertNotIn("TranslationFactory", self.analyzer.nodes)

    def test_should_skip_dependency(self):
        mock_field = Mock(spec=ForeignKey)
        mock_field.null = True
        mock_field.default = "some_default"

        self.assertTrue(
            self.analyzer._should_skip_dependency(mock_field, MockUser)
        )

        system_model = Mock()
        system_model.__name__ = "User"
        self.assertTrue(
            self.analyzer._should_skip_dependency(mock_field, system_model)
        )

        skip_model = Mock()
        skip_model.__name__ = "TestModel"
        skip_model._meta.app_label = "auth"
        self.assertTrue(
            self.analyzer._should_skip_dependency(mock_field, skip_model)
        )

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_analyze_model_dependencies(self, mock_is_translation):
        mock_is_translation.return_value = False

        self.analyzer._create_nodes([MockUserFactory, MockProfileFactory])

        profile_node = self.analyzer.nodes["MockProfileFactory"]

        user_field = Mock(spec=OneToOneField)
        user_field.related_model = MockUser
        user_field.name = "user"
        user_field.null = False
        user_field.default = models.NOT_PROVIDED

        with patch.object(
            MockProfile._meta, "get_fields", return_value=[user_field]
        ):
            self.analyzer._analyze_model_dependencies(profile_node)

        user_node = self.analyzer.nodes["MockUserFactory"]
        self.assertIn(user_node, profile_node.dependencies)

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_analyze_explicit_dependencies(self, mock_is_translation):
        mock_is_translation.return_value = False

        factories = [
            MockUserFactory,
            MockProfileFactory,
            MockExplicitDepsFactory,
        ]
        self.analyzer._create_nodes(factories)

        explicit_node = self.analyzer.nodes["MockExplicitDepsFactory"]
        context = {}

        self.analyzer._analyze_explicit_dependencies(explicit_node, context)

        user_node = self.analyzer.nodes["MockUserFactory"]
        profile_node = self.analyzer.nodes["MockProfileFactory"]

        self.assertIn(user_node, explicit_node.dependencies)
        self.assertIn(profile_node, explicit_node.dependencies)

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_detect_circular_dependencies(self, mock_is_translation):
        mock_is_translation.return_value = False

        self.analyzer._create_nodes([MockUserFactory, MockProfileFactory])

        user_node = self.analyzer.nodes["MockUserFactory"]
        profile_node = self.analyzer.nodes["MockProfileFactory"]

        user_node.add_dependency(profile_node)
        profile_node.add_dependency(user_node)

        with self.assertRaises(CircularDependencyError):
            self.analyzer._detect_circular_dependencies()

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_topological_sort(self, mock_is_translation):
        mock_is_translation.return_value = False

        self.analyzer._create_nodes([MockUserFactory, MockProfileFactory])

        user_node = self.analyzer.nodes["MockUserFactory"]
        profile_node = self.analyzer.nodes["MockProfileFactory"]
        profile_node.add_dependency(user_node)

        sorted_nodes = self.analyzer._topological_sort()

        user_index = next(
            i
            for i, node in enumerate(sorted_nodes)
            if node.name == "MockUserFactory"
        )
        profile_index = next(
            i
            for i, node in enumerate(sorted_nodes)
            if node.name == "MockProfileFactory"
        )

        self.assertLess(user_index, profile_index)

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_get_dependency_report(self, mock_is_translation):
        mock_is_translation.return_value = False

        self.analyzer._create_nodes([MockUserFactory, MockProfileFactory])

        report = self.analyzer.get_dependency_report()

        self.assertIn("Dependency Analysis Report", report)
        self.assertIn("MockUserFactory", report)
        self.assertIn("MockProfileFactory", report)
        self.assertIn("Statistics:", report)

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    @patch("core.utils.dependencies.logger")
    def test_analyze_factory_dependencies_full_flow(
        self, mock_logger, mock_is_translation
    ):
        mock_is_translation.return_value = False

        factories = [MockUserFactory, MockProfileFactory, MockPostFactory]

        with (
            patch.object(
                MockProfile._meta, "get_fields"
            ) as mock_profile_fields,
            patch.object(MockPost._meta, "get_fields") as mock_post_fields,
            patch.object(MockUser._meta, "get_fields", return_value=[]),
        ):
            user_field = Mock(spec=OneToOneField)
            user_field.related_model = MockUser
            user_field.name = "user"
            user_field.null = False
            user_field.default = models.NOT_PROVIDED
            mock_profile_fields.return_value = [user_field]

            author_field = Mock(spec=ForeignKey)
            author_field.related_model = MockUser
            author_field.name = "author"
            author_field.null = False
            author_field.default = models.NOT_PROVIDED
            mock_post_fields.return_value = [author_field]

            result = self.analyzer.analyze_factory_dependencies(factories)

            self.assertIsInstance(result, list)
            self.assertTrue(
                all(isinstance(node, DependencyNode) for node in result)
            )

            self.assertEqual(result[0].name, "MockUserFactory")


class TestFactoryOrchestrator(TestCase):
    def setUp(self):
        self.orchestrator = FactoryOrchestrator()

    def test_orchestrator_initialization(self):
        self.assertIsInstance(self.orchestrator.analyzer, DependencyAnalyzer)

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    @patch("core.utils.dependencies.logger")
    def test_get_execution_order(self, mock_logger, mock_is_translation):
        mock_is_translation.return_value = False

        factories = [
            MockProfileFactory,
            MockUserFactory,
        ]

        with (
            patch.object(MockProfile._meta, "get_fields") as mock_fields,
            patch.object(MockUser._meta, "get_fields", return_value=[]),
        ):
            user_field = Mock(spec=OneToOneField)
            user_field.related_model = MockUser
            user_field.name = "user"
            user_field.null = False
            user_field.default = models.NOT_PROVIDED
            mock_fields.return_value = [user_field]

            result = self.orchestrator.get_execution_order(factories)

            factory_names = [f.__name__ for f in result]
            user_index = factory_names.index("MockUserFactory")
            profile_index = factory_names.index("MockProfileFactory")
            self.assertLess(user_index, profile_index)

    @patch(
        "core.utils.dependencies.TranslationUtilities.is_translation_factory"
    )
    def test_get_dependency_report(self, mock_is_translation):
        mock_is_translation.return_value = False

        factories = [MockUserFactory, MockProfileFactory]
        self.orchestrator.get_execution_order(factories)

        report = self.orchestrator.get_dependency_report()
        self.assertIsInstance(report, str)
        self.assertIn("Dependency Analysis Report", report)


class TestModuleFunctions(TestCase):
    @patch("core.utils.dependencies.FactoryOrchestrator.get_execution_order")
    def test_get_factory_execution_order(self, mock_get_order):
        factory_classes = [MockUserFactory, MockProfileFactory]
        expected_order = [MockUserFactory, MockProfileFactory]
        mock_get_order.return_value = expected_order

        result = get_factory_execution_order(factory_classes)

        self.assertEqual(result, expected_order)
        mock_get_order.assert_called_once_with(factory_classes)

    @patch("core.utils.dependencies.FactoryOrchestrator.get_dependency_report")
    @patch("core.utils.dependencies.FactoryOrchestrator.get_execution_order")
    def test_analyze_factory_dependencies_function(
        self, mock_get_order, mock_get_report
    ):
        factory_classes = [MockUserFactory, MockProfileFactory]
        expected_report = "Test report"
        mock_get_report.return_value = expected_report

        result = analyze_factory_dependencies(factory_classes)

        self.assertEqual(result, expected_report)
        mock_get_order.assert_called_once_with(factory_classes)
        mock_get_report.assert_called_once()
