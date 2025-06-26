"""
Dependency management utilities for factory seeding.
Analyzes model relationships and explicit dependencies to determine optimal execution order.
"""

import logging
from collections import defaultdict, deque
from typing import (
    Any,
    Literal,
    TypeVar,
)

import factory
from django.conf import settings
from django.db import models
from django.db.models.fields.related import (
    ForeignKey,
    OneToOneField,
)

from core.factories import TranslationUtilities

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=factory.django.DjangoModelFactory)


class DependencyNode:
    """Represents a factory and its dependencies in the dependency graph"""

    def __init__(self, factory_class: type[F]):
        self.factory_class = factory_class
        self.model_class = factory_class._meta.model
        self.dependencies: set[DependencyNode] = set()
        self.dependents: set[DependencyNode] = set()
        self.level = 0  # Topological level (0 = no dependencies)

        self.explicit_dependencies: list[str] = []
        self.conditional_dependencies: dict[str, list[str]] = {}
        self.execution_priority: int = 0  # Higher = execute later
        self.business_logic_deps: list[str] = []

        self._parse_factory_metadata()

    @property
    def name(self):
        return self.factory_class.__name__

    @property
    def model_name(self):
        return self.model_class.__name__

    def add_dependency(
        self, dependency_node: "DependencyNode", dependency_type: str = "FK"
    ):
        """Add a dependency (this factory depends on dependency_node)"""
        self.dependencies.add(dependency_node)
        dependency_node.dependents.add(self)

        if not hasattr(self, "dependency_types"):
            self.dependency_types = {}
        self.dependency_types[dependency_node.name] = dependency_type

    def _parse_factory_metadata(self):
        """Parse enhanced dependency metadata from factory class"""
        factory = self.factory_class

        self.explicit_dependencies = getattr(factory, "depends_on", [])

        self.conditional_dependencies = getattr(
            factory, "conditional_depends_on", {}
        )

        self.execution_priority = getattr(factory, "execution_priority", 0)

        self.business_logic_deps = getattr(factory, "business_logic_deps", [])

        self.locale_aware = getattr(factory, "locale_aware", False)
        self.business_rules_enabled = getattr(
            factory, "business_rules_enabled", False
        )

    def get_conditional_deps_for_context(
        self, context: dict[str, Any]
    ) -> list[str]:
        """Get conditional dependencies that apply to current context"""
        applicable_deps = []

        for condition, deps in self.conditional_dependencies.items():
            if self._evaluate_condition(condition, context):
                applicable_deps.extend(deps)

        return applicable_deps

    def _evaluate_condition(
        self, condition: str, context: dict[str, Any]
    ) -> bool:
        """Evaluate if a conditional dependency applies"""
        if condition == "admin_required":
            return context.get("create_admin_users", False)
        elif condition == "has_inventory":
            return context.get("create_inventory", True)

        return getattr(settings, condition.upper(), False)

    def __str__(self):
        return f"{self.name} (level {self.level}, priority {self.execution_priority})"

    def __repr__(self):
        return f"DependencyNode({self.name}, level={self.level}, priority={self.execution_priority})"


class CircularDependencyError(Exception):
    """Raised when circular dependencies are detected"""

    def __init__(self, cycle: list[DependencyNode]):
        self.cycle = cycle
        cycle_names = " -> ".join([node.name for node in cycle])
        super().__init__(f"Circular dependency detected: {cycle_names}")


class DependencyAnalyzer:
    """Analyzes model relationships and creates dependency graphs for factories"""

    def __init__(self):
        self.nodes: dict[str, DependencyNode] = {}
        self.model_to_factory: dict[type[models.Model], type[F]] = {}

    def analyze_factory_dependencies(
        self,
        factory_classes: list[type[F]],
        context: dict[str, Any] | None = None,
    ) -> list[DependencyNode]:
        """
        Analyze dependencies between factories and return them in topological order.

        Args:
            factory_classes: List of factory classes to analyze
            context: Execution context for conditional dependencies

        Returns:
            List of DependencyNode objects in topological order (dependencies first)

        Raises:
            CircularDependencyError: If circular dependencies are detected
        """
        if context is None:
            context = {}

        logger.info(
            f"Analyzing dependencies for {len(factory_classes)} factories"
        )

        self._create_nodes(factory_classes)

        self._build_dependency_graph(context)

        self._detect_circular_dependencies()

        sorted_nodes = self._topological_sort_with_priority()

        logger.info(
            f"Dependency analysis complete. Execution order determined for {len(sorted_nodes)} factories"
        )
        return sorted_nodes

    def _create_nodes(self, factory_classes: list[type[F]]):
        """Create dependency nodes for all factories"""
        for factory_class in factory_classes:
            if TranslationUtilities.is_translation_factory(factory_class):
                continue

            node = DependencyNode(factory_class)
            self.nodes[factory_class.__name__] = node
            self.model_to_factory[factory_class._meta.model] = factory_class

    def _build_dependency_graph(self, context: dict[str, Any]):
        """Build the dependency graph by analyzing model relationships and explicit dependencies"""
        for node in self.nodes.values():
            self._analyze_model_dependencies(node)
            self._analyze_explicit_dependencies(node, context)

    def _analyze_model_dependencies(self, node: DependencyNode):
        """Analyze dependencies for a specific model"""
        model_class = node.model_class

        for field in model_class._meta.get_fields():
            if isinstance(field, ForeignKey | OneToOneField):
                related_model = field.related_model

                if related_model == model_class:
                    continue

                if self._should_skip_dependency(field, related_model):
                    continue

                related_factory = self.model_to_factory.get(related_model)
                if related_factory and related_factory.__name__ in self.nodes:
                    dependency_node = self.nodes[related_factory.__name__]
                    node.add_dependency(dependency_node, "FK")
                    logger.debug(
                        f"{node.name} depends on {dependency_node.name} via {field.name} (FK)"
                    )

    def _should_skip_dependency(
        self,
        field: ForeignKey,
        related_model: type[models.Model] | Literal["self"],
    ) -> bool:
        """Determine if a dependency should be skipped"""

        if field.null and field.default is not models.NOT_PROVIDED:
            return True

        system_models = {
            "ContentType",
            "Permission",
            "Group",
            "User",
            "Site",
        }
        if related_model.__name__ in system_models:
            return True

        skip_apps = {"auth", "contenttypes", "sessions", "admin"}
        return related_model._meta.app_label in skip_apps

    def _analyze_explicit_dependencies(
        self, node: DependencyNode, context: dict[str, Any]
    ):
        """Analyze explicit dependencies declared in factory metadata"""

        for dep_name in node.explicit_dependencies:
            if dep_name in self.nodes:
                dependency_node = self.nodes[dep_name]
                node.add_dependency(dependency_node, "EXPLICIT")
                logger.debug(
                    f"{node.name} explicitly depends on {dependency_node.name}"
                )

        conditional_deps = node.get_conditional_deps_for_context(context)
        for dep_name in conditional_deps:
            if dep_name in self.nodes:
                dependency_node = self.nodes[dep_name]
                node.add_dependency(dependency_node, "CONDITIONAL")
                logger.debug(
                    f"{node.name} conditionally depends on {dependency_node.name}"
                )

        for dep_name in node.business_logic_deps:
            if dep_name in self.nodes:
                dependency_node = self.nodes[dep_name]
                node.add_dependency(dependency_node, "BUSINESS_LOGIC")
                logger.debug(
                    f"{node.name} has business logic dependency on {dependency_node.name}"
                )

    def _detect_circular_dependencies(self):
        """Detect circular dependencies using DFS"""
        visited = set()
        rec_stack = set()

        def dfs(
            node: DependencyNode, path: list[DependencyNode]
        ) -> list[DependencyNode] | None:
            if node in rec_stack:
                cycle_start = path.index(node)
                return [*path[cycle_start:], node]

            if node in visited:
                return None

            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for dependency in node.dependencies:
                cycle = dfs(dependency, path.copy())
                if cycle:
                    return cycle

            rec_stack.remove(node)
            return None

        for node in self.nodes.values():
            if node not in visited:
                cycle = dfs(node, [])
                if cycle:
                    raise CircularDependencyError(cycle)

    def _topological_sort(self) -> list[DependencyNode]:
        """Perform topological sort using Kahn's algorithm"""
        in_degree = {
            node: len(node.dependencies) for node in self.nodes.values()
        }

        queue = deque(
            [node for node in self.nodes.values() if in_degree[node] == 0]
        )
        result = []
        level = 0

        while queue:
            level_size = len(queue)

            for _ in range(level_size):
                node = queue.popleft()
                node.level = level
                result.append(node)

                for dependent in node.dependents:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

            level += 1

        if len(result) != len(self.nodes):
            remaining = [
                node.name for node in self.nodes.values() if node not in result
            ]
            raise CircularDependencyError(
                f"Could not resolve dependencies for: {remaining}"
            )

        return result

    def _topological_sort_with_priority(self) -> list[DependencyNode]:
        """Perform topological sort with execution priority consideration"""
        in_degree = {
            node: len(node.dependencies) for node in self.nodes.values()
        }

        available_nodes = [
            node for node in self.nodes.values() if in_degree[node] == 0
        ]
        available_nodes.sort(key=lambda n: n.execution_priority)

        queue = deque(available_nodes)
        result = []
        level = 0

        while queue:
            level_size = len(queue)
            current_level_nodes = []

            for _ in range(level_size):
                node = queue.popleft()
                node.level = level
                current_level_nodes.append(node)

                for dependent in node.dependents:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

            current_level_nodes.sort(key=lambda n: n.execution_priority)
            result.extend(current_level_nodes)

            queue = deque(sorted(queue, key=lambda n: n.execution_priority))

            level += 1

        if len(result) != len(self.nodes):
            remaining = [
                node.name for node in self.nodes.values() if node not in result
            ]
            raise CircularDependencyError(
                f"Could not resolve dependencies for: {remaining}"
            )

        logger.info(
            f"Priority-aware topological sort completed with {len(result)} factories"
        )
        return result

    def get_dependency_report(self) -> str:
        """Generate a detailed dependency report"""
        if not self.nodes:
            return "No factories analyzed"

        sorted_nodes = self._topological_sort()

        report = ["Dependency Analysis Report", "=" * 50, ""]

        levels: dict[int, list[DependencyNode]] = defaultdict(list)
        for node in sorted_nodes:
            levels[node.level].append(node)

        for level in sorted(levels.keys()):
            nodes = levels[level]
            report.append(f"Level {level} ({len(nodes)} factories):")
            for node in sorted(nodes, key=lambda n: n.execution_priority):
                deps_info = []
                if hasattr(node, "dependency_types"):
                    for dep_node in node.dependencies:
                        dep_type = node.dependency_types.get(
                            dep_node.name, "FK"
                        )
                        deps_info.append(f"{dep_node.name}({dep_type})")
                else:
                    deps_info = [dep.name for dep in node.dependencies]

                dep_str = (
                    f" (depends on: {', '.join(deps_info)})"
                    if deps_info
                    else " (no dependencies)"
                )
                priority_str = (
                    f" [priority: {node.execution_priority}]"
                    if node.execution_priority != 0
                    else ""
                )

                meta_info = []
                if node.locale_aware:
                    meta_info.append("locale-aware")
                if node.business_rules_enabled:
                    meta_info.append("business-rules")

                meta_str = f" {{{', '.join(meta_info)}}}" if meta_info else ""

                report.append(
                    f"  - {node.name}{dep_str}{priority_str}{meta_str}"
                )
            report.append("")

        report.extend(
            [
                "Statistics:",
                f"  Total factories: {len(self.nodes)}",
                f"  Dependency levels: {max(levels.keys()) + 1 if levels else 0}",
                f"  Total dependencies: {sum(len(node.dependencies) for node in self.nodes.values())}",
            ]
        )

        return "\n".join(report)


class FactoryOrchestrator:
    """Orchestrates factory execution based on dependency analysis"""

    def __init__(self):
        self.analyzer = DependencyAnalyzer()

    def get_execution_order(
        self,
        factory_classes: list[type[F]],
        context: dict[str, Any] | None = None,
    ) -> list[type[F]]:
        """
        Get factories in the correct execution order based on dependencies.

        Args:
            factory_classes: List of factory classes to order

        Returns:
            List of factory classes in dependency order
        """
        try:
            main_factories = [
                f
                for f in factory_classes
                if not TranslationUtilities.is_translation_factory(f)
            ]

            if not main_factories:
                return factory_classes

            sorted_nodes = self.analyzer.analyze_factory_dependencies(
                main_factories, context
            )

            ordered_factories = [node.factory_class for node in sorted_nodes]

            logger.info(
                f"Factory execution order determined: {[f.__name__ for f in ordered_factories]}"
            )
            return ordered_factories

        except CircularDependencyError as e:
            logger.warning(
                f"Circular dependency detected: {e}. Using original order."
            )
            return factory_classes
        except Exception as e:
            logger.error(
                f"Dependency analysis failed: {e}. Using original order."
            )
            return factory_classes

    def get_dependency_report(self) -> str:
        """Get a detailed dependency analysis report"""
        return self.analyzer.get_dependency_report()


def get_factory_execution_order[F: factory.django.DjangoModelFactory](
    factory_classes: list[type[F]],
) -> list[type[F]]:
    """Get factories in dependency order"""
    orchestrator = FactoryOrchestrator()
    return orchestrator.get_execution_order(factory_classes)


def analyze_factory_dependencies[F: factory.django.DjangoModelFactory](
    factory_classes: list[type[F]],
) -> str:
    """Analyze and report on factory dependencies"""
    orchestrator = FactoryOrchestrator()
    orchestrator.get_execution_order(factory_classes)
    return orchestrator.get_dependency_report()
