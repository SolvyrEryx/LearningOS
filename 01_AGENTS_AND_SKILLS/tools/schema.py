"""A bundled JSON-Schema subset validator, standard library only.

Why this exists instead of a dependency on ``jsonschema``: `validate` is the health check for the
whole install, so it has to work on a bare Python 3.11. A learner who has just copied the
LearningOS folder onto a new machine must be able to find out whether their memory files are
intact without first installing anything.

Supported keyword subset — exactly what the 15 shipped schemas in
``01_AGENTS_AND_SKILLS/schemas/`` use, plus a little slack:

``$id``, ``$schema``, ``title``, ``description``, ``type`` (string or list), ``properties``,
``required``, ``additionalProperties`` (boolean or schema), ``items`` (schema or tuple),
``enum``, ``const``, ``pattern``, ``minimum``, ``maximum``, ``exclusiveMinimum``,
``exclusiveMaximum``, ``minLength``, ``maxLength``, ``minItems``, ``maxItems``, ``uniqueItems``,
``oneOf``, ``anyOf``, ``allOf``, ``$defs``/``definitions`` with local ``#/$defs/...`` refs, and
``format: "date-time"`` checked strictly as ``^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$``.

Anything outside that subset is ignored rather than guessed at, and
:meth:`SchemaRegistry.unsupported_keywords` reports what a schema uses that this validator does
not, so a schema can never silently stop being enforced.

When ``jsonschema`` happens to be installed and ``strict=True`` is requested, it is run as a
cross-check and any *extra* errors it finds are surfaced with the code ``schema_strict``. Its
absence never raises.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from common import (
    Finding,
    LearningOSDataError,
    escape_pointer_token,
    read_json,
)

DATE_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SUPPORTED_KEYWORDS = frozenset(
    {
        "$id",
        "$schema",
        "$ref",
        "$comment",
        "$defs",
        "definitions",
        "title",
        "description",
        "default",
        "examples",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "oneOf",
        "anyOf",
        "allOf",
    }
)

_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def type_name(value: Any) -> str:
    """The JSON type name of a Python value, for error messages."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


class _Checker:
    """One validation run. Collects ``(pointer, message)`` pairs; never raises on bad data."""

    def __init__(self, root_schema: dict[str, Any]) -> None:
        self.root = root_schema
        self.errors: list[tuple[str, str]] = []

    # -- entry point -------------------------------------------------------------------

    def check(self, schema: Any, instance: Any, pointer: str) -> None:
        if schema is True or schema == {}:
            return
        if schema is False:
            self._fail(pointer, "no value is allowed here")
            return
        if not isinstance(schema, dict):  # pragma: no cover - malformed schema
            self._fail(pointer, f"schema fragment is not an object: {schema!r}")
            return

        if "$ref" in schema:
            resolved = self._resolve(schema["$ref"], pointer)
            if resolved is not None:
                self.check(resolved, instance, pointer)
            return

        self._check_type(schema, instance, pointer)
        self._check_enum(schema, instance, pointer)
        self._check_string(schema, instance, pointer)
        self._check_number(schema, instance, pointer)
        self._check_array(schema, instance, pointer)
        self._check_object(schema, instance, pointer)
        self._check_combinators(schema, instance, pointer)

    # -- keyword groups ----------------------------------------------------------------

    def _check_type(self, schema: dict[str, Any], instance: Any, pointer: str) -> None:
        declared = schema.get("type")
        if declared is None:
            return
        allowed = [declared] if isinstance(declared, str) else list(declared)
        for candidate in allowed:
            check = _TYPE_CHECKS.get(candidate)
            if check is None:  # pragma: no cover - unknown type name in a schema
                return
            if check(instance):
                return
        self._fail(
            pointer,
            f"expected type {' or '.join(allowed)}, got {type_name(instance)}",
        )

    def _check_enum(self, schema: dict[str, Any], instance: Any, pointer: str) -> None:
        if "enum" in schema:
            options = schema["enum"]
            if not any(_json_equal(instance, option) for option in options):
                self._fail(
                    pointer,
                    f"value {_show(instance)} is not one of: "
                    + ", ".join(_show(option) for option in options),
                )
        if "const" in schema and not _json_equal(instance, schema["const"]):
            self._fail(pointer, f"value must be {_show(schema['const'])}, got {_show(instance)}")

    def _check_string(self, schema: dict[str, Any], instance: Any, pointer: str) -> None:
        if not isinstance(instance, str):
            return
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, instance) is None:
            self._fail(pointer, f"value {_show(instance)} does not match pattern {pattern}")
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            self._fail(
                pointer,
                f"string is shorter than the minimum of {min_length} character(s)"
                + (" (an empty string is not allowed here)" if min_length == 1 else ""),
            )
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(instance) > max_length:
            self._fail(pointer, f"string is longer than the maximum of {max_length} characters")
        fmt = schema.get("format")
        if fmt == "date-time" and DATE_TIME_RE.match(instance) is None:
            self._fail(
                pointer,
                "value is not an ISO-8601 UTC timestamp of the form 2026-02-11T09:14:00Z",
            )
        elif fmt == "date" and DATE_RE.match(instance) is None:
            self._fail(pointer, "value is not a calendar date of the form 2026-05-18")

    def _check_number(self, schema: dict[str, Any], instance: Any, pointer: str) -> None:
        if isinstance(instance, bool) or not isinstance(instance, (int, float)):
            return
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            self._fail(pointer, f"value {instance} is below the minimum of {minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and instance > maximum:
            self._fail(pointer, f"value {instance} is above the maximum of {maximum}")
        exclusive_min = schema.get("exclusiveMinimum")
        if isinstance(exclusive_min, (int, float)) and instance <= exclusive_min:
            self._fail(pointer, f"value {instance} must be greater than {exclusive_min}")
        exclusive_max = schema.get("exclusiveMaximum")
        if isinstance(exclusive_max, (int, float)) and instance >= exclusive_max:
            self._fail(pointer, f"value {instance} must be less than {exclusive_max}")

    def _check_array(self, schema: dict[str, Any], instance: Any, pointer: str) -> None:
        if not isinstance(instance, list):
            return
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            self._fail(pointer, f"array needs at least {min_items} item(s), got {len(instance)}")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(instance) > max_items:
            self._fail(pointer, f"array allows at most {max_items} item(s), got {len(instance)}")
        if schema.get("uniqueItems") is True:
            seen: list[Any] = []
            for item in instance:
                if any(_json_equal(item, other) for other in seen):
                    self._fail(pointer, f"array items must be unique; {_show(item)} is repeated")
                    break
                seen.append(item)
        items = schema.get("items")
        if items is None:
            return
        if isinstance(items, list):  # tuple validation
            for index, (sub_schema, value) in enumerate(zip(items, instance)):
                self.check(sub_schema, value, f"{pointer}/{index}")
        else:
            for index, value in enumerate(instance):
                self.check(items, value, f"{pointer}/{index}")

    def _check_object(self, schema: dict[str, Any], instance: Any, pointer: str) -> None:
        if not isinstance(instance, dict):
            return
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for name in schema.get("required", []) or []:
            if name not in instance:
                self._fail(
                    f"{pointer}/{escape_pointer_token(str(name))}",
                    f"required property {name!r} is missing",
                )
        for name, sub_schema in properties.items():
            if name in instance:
                self.check(sub_schema, instance[name], f"{pointer}/{escape_pointer_token(name)}")
        additional = schema.get("additionalProperties")
        if additional is None:
            return
        extras = [name for name in instance if name not in properties]
        if additional is False:
            for name in extras:
                self._fail(
                    f"{pointer}/{escape_pointer_token(str(name))}",
                    f"property {name!r} is not allowed here; permitted properties are: "
                    + (", ".join(sorted(properties)) or "(none)"),
                )
        elif isinstance(additional, dict):
            for name in extras:
                self.check(additional, instance[name], f"{pointer}/{escape_pointer_token(str(name))}")

    def _check_combinators(self, schema: dict[str, Any], instance: Any, pointer: str) -> None:
        for sub_schema in schema.get("allOf", []) or []:
            self.check(sub_schema, instance, pointer)
        if "oneOf" in schema:
            self._check_branches(schema["oneOf"], instance, pointer, exactly_one=True)
        if "anyOf" in schema:
            self._check_branches(schema["anyOf"], instance, pointer, exactly_one=False)

    def _check_branches(
        self,
        branches: Sequence[Any],
        instance: Any,
        pointer: str,
        *,
        exactly_one: bool,
    ) -> None:
        """Validate ``oneOf``/``anyOf`` and report the closest-matching branch when none match.

        The shipped ``session_event.schema.json`` keys its branches on ``type``, so when nothing
        matches, the branch whose discriminator *did* match is the one worth reporting — quoting
        "none of 18 branches matched" would be useless. Branches that failed on
        ``<pointer>/type`` are therefore ranked last, and among the rest the one with the fewest
        errors wins.
        """
        results: list[list[tuple[str, str]]] = []
        for branch in branches:
            checker = _Checker(self.root)
            checker.check(branch, instance, pointer)
            results.append(checker.errors)
        matched = [index for index, errors in enumerate(results) if not errors]
        if matched and (not exactly_one or len(matched) == 1):
            return
        if not matched:
            discriminator = f"{pointer}/type"
            candidates = [
                index
                for index, errors in enumerate(results)
                if not any(err_pointer == discriminator for err_pointer, _ in errors)
            ]
            ranked = candidates or list(range(len(results)))
            best = min(ranked, key=lambda index: len(results[index]))
            label = _branch_label(branches[best], best)
            detail = "; ".join(
                f"{err_pointer or '/'} {message}" for err_pointer, message in results[best][:4]
            )
            keyword = "oneOf" if exactly_one else "anyOf"
            self._fail(
                pointer,
                f"no {keyword} branch matched. Closest branch [{label}] failed: {detail}",
            )
            return
        labels = ", ".join(_branch_label(branches[index], index) for index in matched)
        self._fail(
            pointer,
            f"value matches {len(matched)} oneOf branches ({labels}); exactly one must match",
        )

    # -- helpers -----------------------------------------------------------------------

    def _resolve(self, ref: Any, pointer: str) -> Any | None:
        if not isinstance(ref, str) or not ref.startswith("#/"):
            self._fail(
                pointer,
                f"unsupported $ref {ref!r}: only local '#/$defs/...' references are supported",
            )
            return None
        node: Any = self.root
        for token in ref[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            else:
                self._fail(pointer, f"$ref {ref!r} does not resolve inside this schema")
                return None
        return node

    def _fail(self, pointer: str, message: str) -> None:
        self.errors.append((pointer, message))


def _branch_label(branch: Any, index: int) -> str:
    if isinstance(branch, dict):
        title = branch.get("title")
        if isinstance(title, str) and title:
            return title
        properties = branch.get("properties")
        if isinstance(properties, dict):
            discriminator = properties.get("type")
            if isinstance(discriminator, dict):
                if "const" in discriminator:
                    return str(discriminator["const"])
                if "enum" in discriminator:
                    return "/".join(str(value) for value in discriminator["enum"])
    return f"branch {index}"


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality: 1 and True are different, 1 and 1.0 are the same."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    return left == right


def _show(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False) if value is not None else "null"


class SchemaRegistry:
    """Loads the shipped schemas and validates instances against them.

    Schemas are addressable three ways: by bare name (``concept_record``), by filename
    (``concept_record.schema.json``) and by ``$id``
    (``https://learningos.local/schemas/concept_record.schema.json``).
    """

    def __init__(self, schema_dir: str | Path) -> None:
        self.schema_dir = Path(schema_dir)
        self._by_key: dict[str, dict[str, Any]] = {}
        self._names: list[str] = []
        self._loaded = False

    # -- loading -----------------------------------------------------------------------

    def load(self) -> "SchemaRegistry":
        if self._loaded:
            return self
        if not self.schema_dir.is_dir():
            raise LearningOSDataError(
                f"schema directory not found: {self.schema_dir}",
                path=str(self.schema_dir),
                remedy="Restore 01_AGENTS_AND_SKILLS/schemas/ from your copy of LearningOS; "
                "validation cannot run without it.",
                code="missing_schemas",
            )
        for path in sorted(self.schema_dir.glob("*.json")):
            schema = read_json(path, what=f"schema {path.name}")
            if not isinstance(schema, dict):
                raise LearningOSDataError(
                    f"schema {path.name} must contain a JSON object",
                    path=str(path),
                    remedy="Restore the schema file from your copy of LearningOS.",
                    code="bad_schema",
                )
            name = path.name[: -len(".schema.json")] if path.name.endswith(".schema.json") else path.stem
            self._names.append(name)
            self._by_key[name] = schema
            self._by_key[path.name] = schema
            schema_id = schema.get("$id")
            if isinstance(schema_id, str):
                self._by_key[schema_id] = schema
        self._loaded = True
        return self

    @property
    def names(self) -> list[str]:
        self.load()
        return list(self._names)

    def get(self, name: str) -> dict[str, Any]:
        self.load()
        try:
            return self._by_key[name]
        except KeyError as exc:
            raise LearningOSDataError(
                f"unknown schema {name!r}",
                remedy="Known schemas: " + ", ".join(self.names),
                code="unknown_schema",
            ) from exc

    def has(self, name: str) -> bool:
        self.load()
        return name in self._by_key

    # -- validation --------------------------------------------------------------------

    def validate(
        self,
        schema_name: str,
        instance: Any,
        *,
        path: str | None = None,
        pointer_prefix: str = "",
        strict: bool = False,
        code: str = "schema",
    ) -> list[Finding]:
        """Validate ``instance`` and return findings (empty list means valid).

        ``path`` is the workspace-relative file the instance came from, and ``pointer_prefix``
        lets a caller validate a fragment while keeping pointers meaningful — that is how each
        value of ``concept_mastery.json``'s ``concepts`` map is reported as
        ``/concepts/<concept_id>/...``.
        """
        schema = self.get(schema_name)
        checker = _Checker(schema)
        checker.check(schema, instance, pointer_prefix)
        findings = [
            Finding(
                severity="error",
                code=code,
                message=message,
                path=path,
                pointer=pointer or "/",
                remedy=f"Fix the value so it satisfies {schema_name}.schema.json, "
                "or restore the file from 03_MEMORY/backups/.",
            )
            for pointer, message in checker.errors
        ]
        if strict:
            findings.extend(
                self._strict_cross_check(
                    schema_name,
                    schema,
                    instance,
                    path=path,
                    pointer_prefix=pointer_prefix,
                    already={(finding.pointer, finding.message) for finding in findings},
                )
            )
        return findings

    def validate_or_raise(
        self,
        schema_name: str,
        instance: Any,
        *,
        path: str | None = None,
        pointer_prefix: str = "",
        strict: bool = False,
        remedy: str | None = None,
    ) -> None:
        """Validate and raise :class:`LearningOSDataError` on the first error.

        Writers call this *before* the atomic replace, so an invalid snapshot never reaches the
        canonical path.
        """
        findings = self.validate(
            schema_name,
            instance,
            path=path,
            pointer_prefix=pointer_prefix,
            strict=strict,
        )
        if not findings:
            return
        first = findings[0]
        extra = "" if len(findings) == 1 else f" (and {len(findings) - 1} more)"
        raise LearningOSDataError(
            f"{schema_name} validation failed: {first.message}{extra}",
            path=path or first.path,
            pointer=first.pointer,
            remedy=remedy or first.remedy,
            code="schema",
        )

    def unsupported_keywords(self, schema_name: str) -> set[str]:
        """Keywords used by a schema that this validator does not implement.

        A guard against the schemas quietly outgrowing the validator.
        """
        found: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("properties", "$defs", "definitions"):
                        if isinstance(value, dict):
                            for sub in value.values():
                                walk(sub)
                        continue
                    if key not in SUPPORTED_KEYWORDS:
                        # Record the keyword and stop: its value is not a schema this validator
                        # understands, so descending into it would invent keyword names.
                        found.add(key)
                        continue
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(self.get(schema_name))
        return found

    # -- optional cross-check ----------------------------------------------------------

    def _strict_cross_check(
        self,
        schema_name: str,
        schema: dict[str, Any],
        instance: Any,
        *,
        path: str | None,
        pointer_prefix: str,
        already: set[tuple[str | None, str]],
    ) -> list[Finding]:
        """Run ``jsonschema`` when it is installed and surface anything extra it finds.

        This is a belt-and-braces check for ``--strict`` only. A missing ``jsonschema`` is not an
        error, not a warning, and never an exception.
        """
        try:
            import jsonschema  # noqa: PLC0415 - optional, probed at call time
        except Exception:
            return []
        try:
            validator = jsonschema.Draft7Validator(schema)
            errors = sorted(validator.iter_errors(instance), key=lambda err: list(err.absolute_path))
        except Exception as exc:  # pragma: no cover - jsonschema internal failure
            return [
                Finding(
                    severity="warning",
                    code="schema_strict_unavailable",
                    message=f"jsonschema cross-check could not run: {exc}",
                    path=path,
                    remedy="The bundled validator already ran; this is informational only.",
                )
            ]
        findings: list[Finding] = []
        for error in errors:
            pointer = pointer_prefix + "".join(
                f"/{escape_pointer_token(str(token))}" for token in error.absolute_path
            )
            pointer = pointer or "/"
            if (pointer, error.message) in already:
                continue
            findings.append(
                Finding(
                    severity="error",
                    code="schema_strict",
                    message=f"{error.message} (reported by the jsonschema cross-check)",
                    path=path,
                    pointer=pointer,
                    remedy=f"Fix the value so it satisfies {schema_name}.schema.json.",
                )
            )
        return findings


_REGISTRY_CACHE: dict[Path, SchemaRegistry] = {}


def registry_for(schema_dir: str | Path) -> SchemaRegistry:
    """Return a cached :class:`SchemaRegistry` for a schema directory."""
    key = Path(schema_dir).resolve()
    cached = _REGISTRY_CACHE.get(key)
    if cached is None:
        cached = SchemaRegistry(key).load()
        _REGISTRY_CACHE[key] = cached
    return cached


def clear_registry_cache() -> None:
    """Drop cached registries. Tests that rewrite a schema on disk need this."""
    _REGISTRY_CACHE.clear()


def jsonschema_available() -> bool:
    """Whether the optional ``jsonschema`` cross-check can run."""
    try:
        import jsonschema  # noqa: F401
    except Exception:
        return False
    return True
