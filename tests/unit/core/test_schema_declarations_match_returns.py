"""A method field's declared type is what its method returns.

The storefront generates its Zod schemas from ``schema.yml`` and answers 422
to any body they reject. For a ``SerializerMethodField`` the schema says
whatever the method's ``@extend_schema_field`` (or, without one, its return
annotation) says, and nothing else ties that to the code: a reply to a blog
comment was saved but answered 422 because ``parentComment.user`` was
declared as the full account (``UserDetails``) while the method returned the
public author (``UserPublicSerializer``).

This walks the AST of every application module and fails for a method field
whose declaration disagrees with what the method visibly returns:

* a declared serializer (a class, ``lazy_serializer("...")`` or a ``$ref``)
  against the serializers whose ``.data`` the method returns;
* a declared object (``properties``) against the keys of the dict literals
  the method returns, both camelised as the renderer does, and the ``$ref``
  of a property against the serializer returned for that key;
* a declared or annotated primitive (``string``, ``integer``, ``boolean``,
  ``number``) against the types the return statements can have, including
  ``None`` against a type that is not nullable.

It is not complete, on purpose: a method that returns a variable, a call or
a model attribute is not judged (the type is not visible). What it cannot see
is held by validating real responses against the schema
(``tests/utils/openapi.assert_matches_response``).

A method field with neither a declaration nor a return annotation is typed
by nothing, so it must be listed in ``UNTYPED`` with the reason. An entry
that is no longer untyped fails, and so does a mismatch in ``KNOWN`` that has
been fixed: both tables only shrink.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.conf import settings

# A field known to mismatch, with the issue that tracks it, until it is
# fixed: ``"path:Class.method" -> "issue N, dated"``. Empty today.
KNOWN: dict[str, str] = {}

_SEARCH_HITS = (
    "builds a hit dict inside the search views (``ProductTranslationSerializer"
    "(...).data``); the endpoint's response is declared by its own "
    "``*MeiliSearchResponseSerializer``, so this method never reaches the "
    "schema"
)

# Method fields that are typed by nothing (no ``@extend_schema_field``, no
# return annotation): ``"path:Class.method" -> reason``.
UNTYPED: dict[str, str] = {
    f"src/search/serializers.py:{cls}.get_{name}": _SEARCH_HITS
    for cls, names in {
        "BlogPostTranslationSerializer": (
            "slug",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
            "content_type",
        ),
        "ProductTranslationSerializer": (
            "slug",
            "main_image_path",
            "matches_position",
            "ranking_score",
            "formatted",
            "content_type",
            "final_price",
            "price",
            "effective_price",
            "prior_price",
            "announced_discount_percent",
            "offer_kind",
            "stock",
            "likes_count",
            "view_count",
            "review_average",
            "vat_percent",
            "category_name",
            "brand_name",
            "review_count",
            "created_at",
            "low_stock_threshold",
        ),
    }.items()
    for name in names
}

_JSON = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}
_FIELDS = {
    "CharField": "string",
    "IntegerField": "integer",
    "BooleanField": "boolean",
    "DecimalField": "string",
    "FloatField": "number",
}
_OPEN_API_TYPES = {
    "STR": "string",
    "INT": "integer",
    "BOOL": "boolean",
    "DECIMAL": "string",
    "FLOAT": "number",
}
_SKIP_DIRS = {
    "tests",
    "tests_mt",
    "migrations",
    ".venv",
    "node_modules",
    ".claude",
}


@dataclass(frozen=True)
class Finding:
    kind: str  # "mismatch" | "untyped"
    key: str  # "path:Class.method"
    detail: str


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(word.title() for word in rest)


def _component(name: str) -> str:
    return name.removesuffix("Serializer")


def _types_of(node: ast.expr) -> set[str] | None:
    """The JSON types a returned expression can have; ``None`` if unknown."""
    if isinstance(node, ast.Constant):
        if node.value is None:
            return {"null"}
        return {_JSON.get(type(node.value).__name__, "?")}
    if isinstance(node, ast.JoinedStr):
        return {"string"}
    if isinstance(node, ast.Compare) or (
        isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
    ):
        return {"boolean"}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return {
            "str": {"string"},
            "int": {"integer"},
            "len": {"integer"},
            "bool": {"boolean"},
        }.get(node.func.id)
    if isinstance(node, ast.Dict):
        return {"object"}
    if isinstance(node, (ast.List, ast.ListComp)):
        return {"array"}
    if isinstance(node, ast.IfExp):
        body, orelse = _types_of(node.body), _types_of(node.orelse)
        return (
            body | orelse if body is not None and orelse is not None else None
        )
    return None


def _data_serializers(node: ast.AST) -> set[str]:
    """``X`` for every ``X(...).data`` inside ``node``, as a component name."""
    found = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and child.attr == "data"
            and isinstance(child.value, ast.Call)
        ):
            func = child.value.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else getattr(func, "attr", "")
            )
            if name.endswith("Serializer"):
                found.add(_component(name))
    return found


@dataclass
class _Declared:
    serializers: set[str]
    keys: set[str] | None
    refs: dict[str, str]
    jtype: str | None
    nullable: bool


def _declared(argument: ast.expr) -> _Declared:
    declared = _Declared(set(), None, {}, None, False)
    for node in ast.walk(argument):
        if isinstance(node, ast.Name) and node.id.endswith("Serializer"):
            declared.serializers.add(_component(node.id))
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "lazy_serializer"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            declared.serializers.add(
                _component(str(node.args[0].value).rsplit(".", 1)[-1])
            )
    if (
        isinstance(argument, ast.Call)
        and isinstance(argument.func, ast.Attribute)
        and argument.func.attr in _FIELDS
    ):
        declared.jtype = _FIELDS[argument.func.attr]
        declared.nullable = any(
            keyword.arg == "allow_null"
            and getattr(keyword.value, "value", False)
            for keyword in argument.keywords
        )
    if (
        isinstance(argument, ast.Attribute)
        and argument.attr in _OPEN_API_TYPES
        and "OpenApiTypes" in ast.unparse(argument)
    ):
        declared.jtype = _OPEN_API_TYPES[argument.attr]
    if isinstance(argument, ast.Dict):
        _declared_dict(argument, declared)
    return declared


def _declared_dict(argument: ast.Dict, declared: _Declared) -> None:
    properties = None
    for key, value in zip(argument.keys, argument.values, strict=True):
        if not isinstance(key, ast.Constant):
            continue
        if key.value == "type" and isinstance(value, ast.Constant):
            declared.jtype = value.value
        elif key.value == "nullable":
            declared.nullable = bool(getattr(value, "value", False))
        elif key.value == "properties" and isinstance(value, ast.Dict):
            properties = value
        elif key.value == "items" and isinstance(value, ast.Dict):
            for inner_key, inner in zip(value.keys, value.values, strict=True):
                if (
                    isinstance(inner_key, ast.Constant)
                    and inner_key.value == "properties"
                    and isinstance(inner, ast.Dict)
                ):
                    properties = inner
        elif key.value == "$ref":
            declared.serializers.add(
                str(getattr(value, "value", "")).rsplit("/", 1)[-1]
            )
    if properties is None:
        return
    declared.keys = set()
    for key, value in zip(properties.keys, properties.values, strict=True):
        if not isinstance(key, ast.Constant):
            continue
        declared.keys.add(_camel(key.value))
        text = ast.unparse(value)
        if "#/components/schemas/" in text:
            declared.refs[_camel(key.value)] = (
                text.split("#/components/schemas/")[1]
                .split("'")[0]
                .split('"')[0]
            )


@dataclass
class _Returned:
    serializers: set[str]
    keys: set[str]
    refs: dict[str, str]
    types: set[str]
    unknown: bool


def _returned(method: ast.FunctionDef) -> _Returned:
    returned = _Returned(set(), set(), {}, set(), False)
    for node in ast.walk(method):
        if not (isinstance(node, ast.Return) and node.value is not None):
            continue
        types = _types_of(node.value)
        if types is None:
            returned.unknown = True
        else:
            returned.types |= types
        returned.serializers |= _data_serializers(node.value)
        for literal in (
            n for n in ast.walk(node.value) if isinstance(n, ast.Dict)
        ):
            for key, value in zip(literal.keys, literal.values, strict=True):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    returned.keys.add(_camel(key.value))
                    nested = _data_serializers(value)
                    if nested:
                        returned.refs[_camel(key.value)] = next(iter(nested))
    return returned


def _hint(annotation: ast.expr | None) -> tuple[str | None, bool]:
    """The primitive a return annotation names, and whether it allows None."""
    if isinstance(annotation, ast.Name):
        return _JSON.get(annotation.id), False
    if isinstance(annotation, ast.BinOp) and isinstance(
        annotation.op, ast.BitOr
    ):
        sides = [annotation.left, annotation.right]
        nones = [
            s for s in sides if isinstance(s, ast.Constant) and s.value is None
        ]
        others = [s for s in sides if s not in nones]
        if len(nones) == 1 and len(others) == 1:
            jtype, _ = _hint(others[0])
            return jtype, True
    return None, False


def _problems(declared: _Declared, returned: _Returned) -> list[str]:
    problems = []
    if (
        declared.serializers
        and returned.serializers
        and not declared.serializers & returned.serializers
    ):
        problems.append(
            f"declares {sorted(declared.serializers)}, returns "
            f"{sorted(returned.serializers)}"
        )
    if declared.keys is not None and returned.keys:
        missing, extra = (
            declared.keys - returned.keys,
            returned.keys - declared.keys,
        )
        if missing:
            problems.append(f"declared but not returned: {sorted(missing)}")
        if extra:
            problems.append(f"returned but not declared: {sorted(extra)}")
        for key, component in declared.refs.items():
            got = returned.refs.get(key)
            if got and got != component:
                problems.append(f"{key} declares {component}, returns {got}")
    if (
        declared.jtype in {"string", "integer", "boolean", "number"}
        and returned.types
        and not returned.unknown
    ):
        got = returned.types - ({"null"} if declared.nullable else set())
        if "null" in got:
            problems.append("returns null but is not declared nullable")
        elif got and not got <= {declared.jtype}:
            problems.append(f"declares {declared.jtype}, returns {sorted(got)}")
    return problems


def _method_fields(cls: ast.ClassDef) -> dict[str, str]:
    """``method name -> field name`` for the class's ``SerializerMethodField``."""
    fields = {}
    for node in cls.body:
        if not (
            isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        ):
            continue
        func = node.value.func
        if (
            getattr(func, "attr", getattr(func, "id", ""))
            != "SerializerMethodField"
        ):
            continue
        field = (
            node.targets[0].id if isinstance(node.targets[0], ast.Name) else ""
        )
        method = next(
            (
                str(kw.value.value)
                for kw in node.value.keywords
                if kw.arg == "method_name"
                and isinstance(kw.value, ast.Constant)
            ),
            f"get_{field}",
        )
        fields[method] = field
    return fields


def _decorator_argument(method: ast.FunctionDef) -> ast.expr | None:
    for decorator in method.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if (
            getattr(func, "id", getattr(func, "attr", ""))
            == "extend_schema_field"
        ):
            return decorator.args[0] if decorator.args else None
    return None


def analyse(source: str, path: str) -> list[Finding]:
    """Every mismatch and every untyped method field in ``source``."""
    findings = []
    tree = ast.parse(source)
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        method_fields = _method_fields(cls)
        for method in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            key = f"{path}:{cls.name}.{method.name}"
            argument = _decorator_argument(method)
            if argument is not None:
                problems = _problems(_declared(argument), _returned(method))
            elif method.name in method_fields:
                jtype, nullable = _hint(method.returns)
                if method.returns is None:
                    findings.append(
                        Finding(
                            "untyped", key, "no declaration and no annotation"
                        )
                    )
                    continue
                problems = _problems(
                    _Declared(set(), None, {}, jtype, nullable),
                    _returned(method),
                )
            else:
                continue
            if problems:
                findings.append(Finding("mismatch", key, "; ".join(problems)))
    return findings


def _application_findings() -> list[Finding]:
    root = Path(settings.BASE_DIR)
    findings = []
    for path in sorted((root / "src").rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        findings += analyse(
            path.read_text(encoding="utf-8"), relative.as_posix()
        )
    return findings


def test_no_method_field_declares_a_type_it_does_not_return():
    unexpected = [
        f"{f.key}: {f.detail}"
        for f in _application_findings()
        if f.kind == "mismatch" and f.key not in KNOWN
    ]

    assert not unexpected, (
        "a method field's declared type differs from what its method returns; "
        "the storefront answers 422 for it:\n" + "\n".join(unexpected)
    )


def test_every_known_mismatch_still_mismatches():
    still = {f.key for f in _application_findings() if f.kind == "mismatch"}

    assert not (set(KNOWN) - still), (
        "fixed; delete these from KNOWN: "
        + ", ".join(sorted(set(KNOWN) - still))
    )


def test_every_untyped_method_field_is_listed_with_its_reason():
    untyped = {f.key for f in _application_findings() if f.kind == "untyped"}

    assert not (untyped - set(UNTYPED)), (
        "a method field with no @extend_schema_field and no return "
        "annotation is typed by nothing; declare it, or list it in UNTYPED "
        "with the reason: " + ", ".join(sorted(untyped - set(UNTYPED)))
    )
    assert not (set(UNTYPED) - untyped), (
        "no longer untyped; delete these from UNTYPED: "
        + ", ".join(sorted(set(UNTYPED) - untyped))
    )


# --- the analyser itself ----------------------------------------------------

PRE_FIX_COMMENT = """
from drf_spectacular.utils import extend_schema_field

class Detail:
    parent_comment = serializers.SerializerMethodField()

    @extend_schema_field(
        {
            "type": "object",
            "nullable": True,
            "properties": {
                "id": {"type": "integer"},
                "user": {"$ref": "#/components/schemas/UserDetails"},
            },
        }
    )
    def get_parent_comment(self, obj):
        return {"id": obj.id, "user": UserPublicSerializer(obj.user).data}
"""


def _kinds(source: str) -> list[tuple[str, str]]:
    return [(f.kind, f.detail) for f in analyse(source, "x.py")]


def test_a_declared_account_for_a_returned_public_user_is_caught():
    [(kind, detail)] = _kinds(PRE_FIX_COMMENT)

    assert kind == "mismatch"
    assert "user declares UserDetails, returns UserPublic" in detail


def test_the_declaration_that_matches_passes():
    assert _kinds(PRE_FIX_COMMENT.replace("UserDetails", "UserPublic")) == []


def test_snake_case_keys_match_a_camel_case_declaration():
    source = """
class S:
    x = serializers.SerializerMethodField()

    @extend_schema_field({"type": "object", "properties": {"groupName": {"type": "string"}}})
    def get_x(self, obj):
        return {"group_name": "a"}
"""

    assert _kinds(source) == []


def test_a_declared_serializer_against_another_returned_is_caught():
    source = """
class S:
    x = serializers.SerializerMethodField()

    @extend_schema_field(AuthorSerializer)
    def get_x(self, obj):
        return UserSerializer(obj).data
"""

    assert [k for k, _ in _kinds(source)] == ["mismatch"]


def test_a_return_annotation_is_held_to_what_is_returned():
    source = """
class S:
    x = serializers.SerializerMethodField()

    def get_x(self, obj) -> str:
        return 1
"""

    assert [k for k, _ in _kinds(source)] == ["mismatch"]


def test_none_against_a_type_that_is_not_nullable_is_caught():
    source = """
class S:
    x = serializers.SerializerMethodField()

    def get_x(self, obj) -> str:
        return None
"""

    assert [k for k, _ in _kinds(source)] == ["mismatch"]


def test_an_annotation_that_allows_none_accepts_it():
    source = """
class S:
    x = serializers.SerializerMethodField()

    def get_x(self, obj) -> str | None:
        return None if obj is None else "a"
"""

    assert _kinds(source) == []


def test_a_method_field_with_no_type_at_all_is_reported():
    source = """
class S:
    x = serializers.SerializerMethodField()

    def get_x(self, obj):
        return 1
"""

    assert [k for k, _ in _kinds(source)] == ["untyped"]


def test_a_return_that_cannot_be_judged_is_not_a_mismatch():
    source = """
class S:
    x = serializers.SerializerMethodField()

    @extend_schema_field(UserPublicSerializer)
    def get_x(self, obj):
        return helper(obj)
"""

    assert _kinds(source) == []


@pytest.mark.parametrize("table", [KNOWN, UNTYPED])
def test_every_table_entry_names_its_reason(table):
    assert all(reason.strip() for reason in table.values())
