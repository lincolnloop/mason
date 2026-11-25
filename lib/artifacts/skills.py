"""The installed workflow skills, and whether each works with this mason (docs/packaging.md §4.2).

A workflow skill and the commands it calls change together, so each skill states the mason
versions it works with in its frontmatter:

    metadata:
      mason-requires: '>=0.2,<0.3'

(`metadata` is the open SKILL.md format's map of strings, so other agents ignore it.) mason
refuses to start a run whose skill states a range this mason is outside, with the command that
fixes it, rather than failing partway through the run.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import paths, runner, version

REQUIREMENT = re.compile(r'^(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)$')


class SkillError(Exception):
    pass


def directories(env: Mapping[str, str] | None = None) -> list[Path]:
    """Where skills are installed, mason's own place first (`mason skills install`), then the
    global places (`--global`)."""
    home = Path((env or {}).get('HOME') or Path.home())
    data = paths.data_home(env)
    return [
        data / '.claude' / 'skills',
        data / '.agents' / 'skills',
        home / '.claude' / 'skills',
        home / '.agents' / 'skills',
    ]


def frontmatter(text: str) -> dict[str, object]:
    """The YAML frontmatter's top-level keys, and the `metadata` map: enough for `name`,
    `version` and `metadata.mason-requires`. Values are plain or quoted scalars."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        raise SkillError('has no frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as exc:
        raise SkillError('frontmatter is not closed with ---') from exc
    data: dict[str, object] = {}
    section: dict[str, str] | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.strip().partition(':')
        if not sep:
            raise SkillError(f'cannot read frontmatter line {line!r}')
        if line.startswith((' ', '\t')):
            if section is None:
                raise SkillError(f'indented line outside a map: {line!r}')
            section[key] = _scalar(value)
        elif value.strip():
            data[key] = _scalar(value)
            section = None
        else:
            section = {}
            data[key] = section
    return data


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.match(r'^\d+(?:\.\d+)*', text.strip())
    if not numbers:
        raise SkillError(f'{text!r} is not a version')
    return tuple(int(part) for part in numbers.group().split('.'))


def satisfies(engine: str, requirement: str) -> bool:
    """Whether a version meets a range such as '>=0.2,<0.3'."""
    have = _version_tuple(engine)
    for clause in requirement.split(','):
        match = REQUIREMENT.match(clause.strip())
        if not match:
            raise SkillError(f'{clause.strip()!r} is not a version requirement (e.g. >=0.2,<0.3)')
        operator, wanted = match.groups()
        want = _version_tuple(wanted)
        width = max(len(have), len(want))
        a, b = have + (0,) * (width - len(have)), want + (0,) * (width - len(want))
        ok = {
            '>=': a >= b,
            '<=': a <= b,
            '==': a == b,
            '>': a > b,
            '<': a < b,
        }[operator]
        if not ok:
            return False
    return True


@dataclass
class Skill:
    name: str
    path: Path
    version: str | None
    requires: str | None

    def problem(self, engine: str) -> str | None:
        """Why this skill can't run with this mason, or None."""
        if self.requires is None:
            return 'states no mason-requires in its frontmatter'
        if engine == 'unknown':
            return None
        try:
            if satisfies(engine, self.requires):
                return None
        except SkillError as exc:
            return str(exc)
        return (
            f'needs mason {self.requires}, and this is mason {engine}: install mason and its '
            'skills from the same release tag (`uv tool install …@<tag>`, '
            '`mason skills install …@<tag>`)'
        )


def read(path: Path) -> Skill:
    try:
        data = frontmatter(path.read_text(encoding='utf-8'))
    except (OSError, SkillError) as exc:
        raise SkillError(f'{path}: {exc}') from exc
    metadata = data.get('metadata')
    requires = metadata.get('mason-requires') if isinstance(metadata, dict) else None
    skill_version = data.get('version')
    return Skill(
        name=str(data.get('name') or path.parent.name),
        path=path,
        version=skill_version if isinstance(skill_version, str) else None,
        requires=requires,
    )


def installed(env: Mapping[str, str] | None = None) -> list[Skill]:
    """Every installed copy of a workflow skill, in the order `directories` searches."""
    found = []
    for directory in directories(env):
        for name in sorted(runner.WORKFLOWS):
            path = directory / name / 'SKILL.md'
            if path.is_file():
                found.append(read(path))
    return found


def find(workflow: str, env: Mapping[str, str] | None = None) -> Skill | None:
    """The copy of a workflow's skill a session loads first, or None if none is installed."""
    return next((s for s in installed(env) if s.name == workflow), None)


def check(workflow: str, env: Mapping[str, str] | None = None) -> Skill | None:
    """The workflow's installed skill, refusing one that doesn't work with this mason."""
    skill = find(workflow, env)
    if skill is not None and (problem := skill.problem(version.package_version())):
        raise SkillError(f'{skill.path}: {problem}')
    return skill
