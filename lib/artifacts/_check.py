"""A small field checker that collects every error instead of stopping at the first.

Artifacts are validated so that an agent can fix them, so each error names the exact field
(`$.dependency.fixed`) and says what was expected. Unknown fields are errors: a
misspelt key would otherwise be silently ignored.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

SHA_PATTERN = re.compile(r'^[0-9a-f]{40}$')
AUTOMATION_SHA_PATTERN = re.compile(r'^[0-9a-f]{40}(-dirty)?$')
SLUG_PATTERN = re.compile(r'^[a-z0-9][a-z0-9._/-]*$')
REPO_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self._missing: set[str] = set()

    def error(self, path: str, message: str) -> None:
        # A missing field is reported once, as "is required", not again by every later check
        # of its (absent) value or of fields inside it.
        if any(path == m or path.startswith((f'{m}.', f'{m}[')) for m in self._missing):
            return
        self.errors.append(f'{path}: {message}')

    def object(
        self,
        value: Any,
        path: str,
        required: Iterable[str],
        optional: Iterable[str] = (),
    ) -> bool:
        """Check that `value` is an object with the required keys and no unknown ones."""
        if not isinstance(value, Mapping):
            self.error(path, f'must be an object, got {_type_name(value)}')
            return False
        required = tuple(required)
        allowed = set(required) | set(optional)
        for key in required:
            if key not in value:
                self.error(f'{path}.{key}', 'is required')
                self._missing.add(f'{path}.{key}')
        for key in value:
            if key not in allowed:
                self.error(f'{path}.{key}', f'is not a known field (allowed: {_list(allowed)})')
        return True

    def string(
        self,
        value: Any,
        path: str,
        *,
        nullable: bool = False,
        max_length: int | None = None,
        pattern: re.Pattern[str] | None = None,
        pattern_hint: str = '',
    ) -> bool:
        if value is None and nullable:
            return True
        if not isinstance(value, str):
            self.error(path, f'must be a string, got {_type_name(value)}')
            return False
        if not value.strip():
            self.error(path, 'must not be empty')
            return False
        if max_length is not None and len(value) > max_length:
            self.error(path, f'must be at most {max_length} characters, got {len(value)}')
            return False
        if pattern is not None and not pattern.fullmatch(value):
            self.error(path, f'must match {pattern_hint or pattern.pattern}, got {value!r}')
            return False
        return True

    def integer(
        self,
        value: Any,
        path: str,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
        nullable: bool = False,
    ) -> bool:
        if value is None and nullable:
            return True
        # bool is a subclass of int; true/false is never a valid count or line number.
        if not isinstance(value, int) or isinstance(value, bool):
            self.error(path, f'must be an integer, got {_type_name(value)}')
            return False
        if minimum is not None and value < minimum:
            self.error(path, f'must be at least {minimum}, got {value}')
            return False
        if maximum is not None and value > maximum:
            self.error(path, f'must be at most {maximum}, got {value}')
            return False
        return True

    def boolean(self, value: Any, path: str) -> bool:
        if not isinstance(value, bool):
            self.error(path, f'must be true or false, got {_type_name(value)}')
            return False
        return True

    def choice(
        self, value: Any, path: str, choices: Iterable[str], *, nullable: bool = False
    ) -> bool:
        if value is None and nullable:
            return True
        choices = tuple(choices)
        if value not in choices:
            self.error(path, f'must be one of {_list(choices)}, got {value!r}')
            return False
        return True

    def timestamp(self, value: Any, path: str, *, nullable: bool = False) -> bool:
        if value is None and nullable:
            return True
        if not self.string(value, path):
            return False
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            self.error(path, f'must be an ISO 8601 timestamp, got {value!r}')
            return False
        if parsed.tzinfo is None:
            self.error(path, f'must include a timezone (e.g. a trailing Z), got {value!r}')
            return False
        return True

    def array(
        self,
        value: Any,
        path: str,
        item: Callable[[Any, str], object],
        *,
        min_items: int = 0,
        max_items: int | None = None,
    ) -> bool:
        if not isinstance(value, list):
            self.error(path, f'must be an array, got {_type_name(value)}')
            return False
        if len(value) < min_items:
            self.error(path, f'must have at least {min_items} item(s)')
        if max_items is not None and len(value) > max_items:
            self.error(path, f'must have at most {max_items} items, got {len(value)}')
        for index, element in enumerate(value):
            item(element, f'{path}[{index}]')
        return True

    def string_array(self, value: Any, path: str, *, min_items: int = 0) -> bool:
        return self.array(value, path, lambda v, p: self.string(v, p), min_items=min_items)

    def unique(self, values: Iterable[tuple[str, Any]], field: str) -> None:
        """Report duplicates among (path, value) pairs."""
        seen: dict[Any, str] = {}
        for path, value in values:
            if not isinstance(value, str | int):
                continue
            if value in seen:
                self.error(f'{path}.{field}', f'duplicates {seen[value]}.{field} ({value!r})')
            else:
                seen[value] = path


def check_envelope(checker: Checker, artifact: Mapping[str, Any], schema: str) -> None:
    """The fields every artifact carries: its schema, its project, and what produced it."""
    if artifact.get('schema') != schema:
        checker.error('$.schema', f'must be {schema!r}, got {artifact.get("schema")!r}')
    checker.string(
        artifact.get('project'), '$.project', pattern=REPO_PATTERN, pattern_hint='owner/repo'
    )
    produced_by = artifact.get('produced_by')
    if checker.object(produced_by, '$.produced_by', ('skill', 'automation_sha'), ('model',)):
        checker.string(produced_by.get('skill'), '$.produced_by.skill', pattern=SLUG_PATTERN)
        checker.string(
            produced_by.get('automation_sha'),
            '$.produced_by.automation_sha',
            pattern=AUTOMATION_SHA_PATTERN,
            pattern_hint='a 40-character commit SHA of this repo, optionally ending in -dirty',
        )
        if 'model' in produced_by:
            checker.string(produced_by['model'], '$.produced_by.model')


ENVELOPE_FIELDS = ('schema', 'project', 'produced_by')


def _type_name(value: Any) -> str:
    match value:
        case None:
            return 'null'
        case bool():
            return 'boolean'
        case int() | float():
            return 'number'
        case str():
            return 'string'
        case list():
            return 'array'
        case Mapping():
            return 'object'
    return type(value).__name__


def _list(values: Iterable[str]) -> str:
    return ', '.join(sorted(values))
