"""Check the bootstrap set before a workflow runs: the tools, the GitHub login, the tokens the
workflow needs, and that this repository's own uv environment is the one running.

Every check is reported, not just the first failure, so one run of `artifact doctor` says
everything that needs fixing. Token values are never read beyond whether they are set.
"""

import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

OK, WARN, FAIL = 'ok', 'warn', 'fail'

Which = Callable[[str], str | None]
# Runs a command and returns (exit status, combined output).
Run = Callable[[Sequence[str]], tuple[int, str]]


@dataclass(frozen=True)
class Tool:
    name: str
    version_args: tuple[str, ...]
    why: str
    # Below this version the tool is reported; None means any version will do.
    minimum: tuple[int, ...] | None = None
    # A missing optional tool is a warning: only some projects or steps need it.
    required: bool = True


TOOLS = (
    Tool('git', ('--version',), 'worktrees, commits, reading pull request heads'),
    Tool('gh', ('--version',), 'everything on GitHub'),
    Tool('uv', ('--version',), "this repository's Python and its locked dependencies"),
    Tool('claude', ('--version',), 'running the workflows'),
    Tool(
        'node',
        ('--version',),
        'npx skills (installing rulesets), and projects with npm dependencies',
        minimum=(22, 20),
        required=False,
    ),
)
MIN_PYTHON = (3, 12)
# A scheduled run can't use an interactive sign-in, which expires (`claude setup-token`).
CLAUDE_TOKENS = ('CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_API_KEY')


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _run(command: Sequence[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            list(command), capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def parse_version(text: str) -> tuple[int, ...] | None:
    """The first dotted version number in a tool's `--version` output."""
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def _dotted(version: tuple[int, ...]) -> str:
    return '.'.join(str(part) for part in version)


def check_tool(tool: Tool, which: Which = shutil.which, run: Run = _run) -> Check:
    missing = FAIL if tool.required else WARN
    if which(tool.name) is None:
        return Check(tool.name, missing, f'not found on PATH; needed for {tool.why}')
    status, output = run([tool.name, *tool.version_args])
    first_line = output.splitlines()[0] if output else ''
    if status != 0:
        return Check(tool.name, missing, f'`{tool.name} {" ".join(tool.version_args)}` failed')
    version = parse_version(first_line)
    if tool.minimum is not None and (version is None or version < tool.minimum):
        found = _dotted(version) if version else 'an unknown version'
        return Check(
            tool.name,
            missing,
            f'{found} found; {_dotted(tool.minimum)} or later is needed for {tool.why}',
        )
    return Check(tool.name, OK, first_line)


def check_python(version: tuple[int, ...] | None = None) -> Check:
    version = version or tuple(sys.version_info[:3])
    if version < MIN_PYTHON:
        return Check('python', FAIL, f'{_dotted(version)}; {_dotted(MIN_PYTHON)} or later needed')
    return Check('python', OK, _dotted(version))


def check_environment(hub: Path, prefix: Path | None = None) -> Check:
    """That the running interpreter is this repository's uv environment, so what uv.lock pins
    is what is imported."""
    prefix = (prefix or Path(sys.prefix)).resolve()
    expected = (hub / '.venv').resolve()
    if prefix == expected:
        return Check('environment', OK, str(expected))
    return Check(
        'environment',
        WARN,
        f'running from {prefix}, not {expected}: run through '
        f'`uv run --project {hub} --locked python -I {hub / "bin" / "artifact"}`',
    )


def check_gh_auth(which: Which = shutil.which, run: Run = _run) -> Check:
    if which('gh') is None:
        return Check('gh auth', FAIL, 'gh is not installed')
    status, _ = run(['gh', 'auth', 'status'])
    if status != 0:
        return Check('gh auth', FAIL, 'not logged in: run `gh auth login`, or set GH_TOKEN')
    return Check('gh auth', OK, 'logged in')


def check_tokens(
    names: Sequence[str], env: dict[str, str], unattended: bool = False
) -> list[Check]:
    checks = [
        Check(name, OK, 'set') if env.get(name) else Check(name, FAIL, 'not set') for name in names
    ]
    if unattended:
        if any(env.get(name) for name in CLAUDE_TOKENS):
            checks.append(Check('claude token', OK, 'set'))
        else:
            checks.append(
                Check(
                    'claude token',
                    WARN,
                    f'none of {", ".join(CLAUDE_TOKENS)} is set: a scheduled run needs a '
                    'long-lived token (`claude setup-token`), since an interactive sign-in expires',
                )
            )
    return checks


def run_checks(
    hub: Path,
    tokens: Sequence[str],
    env: dict[str, str],
    unattended: bool = False,
    which: Which = shutil.which,
    run: Run = _run,
) -> list[Check]:
    return [
        *(check_tool(tool, which, run) for tool in TOOLS),
        check_python(),
        check_environment(hub),
        check_gh_auth(which, run),
        *check_tokens(tokens, env, unattended),
    ]


def render(checks: Sequence[Check]) -> str:
    width = max(len(check.name) for check in checks)
    lines = [f'{check.status:<4}  {check.name:<{width}}  {check.detail}' for check in checks]
    failed = sum(check.status == FAIL for check in checks)
    warned = sum(check.status == WARN for check in checks)
    lines.append(f'{failed} failed, {warned} warnings')
    return '\n'.join(lines) + '\n'


def failed(checks: Sequence[Check]) -> bool:
    return any(check.status == FAIL for check in checks)
