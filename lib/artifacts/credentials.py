"""Which identity a project's work runs as: its tokens, and who its commits are from.

A developer working for several organisations shouldn't run every project with one token that
reaches all of them. A project's settings, or the settings for every project of its owner
(`~/.config/mason/owners/<owner>.toml`), say where its credentials come from:

    [credentials]
    env_file = '~/.config/mason/env.acme'   # KEY=value lines: this project's tokens
    github = 'gh:acme-bot'                  # GH_TOKEN from a gh account, or 'env:NAME'
    git_name = 'mason (acme)'               # who mason's commits are from
    git_email = 'mason@acme.example'
    host = 'github.acme.example'            # GitHub Enterprise; github.com when unset

The settings never hold a secret, only where one is. The environment a project's work runs in
(a `mason run` session, and mason's own GitHub calls for it) gets that project's tokens, and
loses every other project's: every variable set in any project's or owner's env file, and in the
shared `~/.config/mason/env`, that this project doesn't set itself.
"""

import json
import os
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import paths

KEYS = ('env_file', 'github', 'git_name', 'git_email', 'host')
# Tokens GitHub's CLI reads, in its order of preference; a project's own replaces them all.
GITHUB_VARIABLES = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')


class CredentialError(Exception):
    pass


def owner_settings(owner: str, env: Mapping[str, str] | None = None) -> Path:
    return paths.config_home(env) / 'owners' / f'{owner}.toml'


def check(section: object) -> list[str]:
    """Problems with a [credentials] table, for the settings checker."""
    if not isinstance(section, dict):
        return ['must be a table']
    problems = [f'{key}: is not a known field' for key in section if key not in KEYS]
    for key, value in section.items():
        if key in KEYS and (not isinstance(value, str) or not value.strip()):
            problems.append(f'{key}: must be a string')
    source = section.get('github')
    if isinstance(source, str) and not source.startswith(('env:', 'gh:')):
        problems.append("github: is 'env:<VARIABLE>' or 'gh:<account>'")
    return problems


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines (an optional `export`, comments and blank lines allowed)."""
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError as exc:
        raise CredentialError(f'{path}: no such env file') from exc
    values = {}
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, sep, value = line.removeprefix('export ').partition('=')
        if not sep or not name.strip().isidentifier():
            raise CredentialError(f'{path}:{number}: not a KEY=value line')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _settings_table(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except tomllib.TOMLDecodeError as exc:
        raise CredentialError(f'{path}: is not valid TOML: {exc}') from exc
    table = data.get('credentials', {})
    if problems := check(table):
        raise CredentialError(f'{path}: [credentials] ' + '; '.join(problems))
    return table


@dataclass
class Credentials:
    repo: str
    # The settings in effect, key -> (value, the file it came from).
    settings: dict[str, tuple[str, Path]] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        found = self.settings.get(key)
        return found[0] if found else None

    def describe(self) -> str:
        if not self.settings:
            return f'{self.repo}: the shared credentials (no [credentials] set)'
        lines = [f'{self.repo}:']
        for key in KEYS:
            if key in self.settings:
                value, source = self.settings[key]
                lines.append(f'  {key} = {value}  ({source})')
        return '\n'.join(lines)


def load(repo: str, env: Mapping[str, str] | None = None) -> Credentials:
    """The project's own [credentials], key by key over its owner's."""
    owner = repo.split('/')[0]
    found = Credentials(repo)
    for path in (owner_settings(owner, env), paths.settings(repo, env)):
        for key, value in _settings_table(path).items():
            found.settings[key] = (value, path)
    return found


def every_env_file(env: Mapping[str, str] | None = None) -> list[Path]:
    """Every env file any project or owner names, and the shared one."""
    files = {paths.config_home(env) / 'env'}
    root = paths.config_home(env)
    for path in [*root.glob('owners/*.toml'), *root.glob('projects/*/*.toml')]:
        try:
            value = _settings_table(path).get('env_file')
        except CredentialError:
            continue
        if value:
            files.add(Path(value).expanduser())
    return sorted(files)


def _gh_token(account: str, host: str | None) -> str:
    command = ['gh', 'auth', 'token', '--user', account]
    if host:
        command += ['--hostname', host]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialError(f'gh auth token failed: {exc}') from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise CredentialError(
            f'gh has no login for {account}{" on " + host if host else ""}: '
            f'`gh auth login{" --hostname " + host if host else ""}` as that account first'
        )
    return result.stdout.strip()


def environment(
    repo: str,
    base: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    gh_token=_gh_token,
) -> dict[str, str]:
    """`base` (default: this process's environment) as the project's work should see it."""
    base = dict(os.environ if base is None else base)
    creds = load(repo, env)
    if not creds.settings:
        return base
    own: dict[str, str] = {}
    if env_file := creds.get('env_file'):
        own.update(read_env_file(Path(env_file).expanduser()))
    # Every other project's secrets out: what any env file sets, unless this project sets it.
    for path in every_env_file(env):
        try:
            others = read_env_file(path)
        except CredentialError:
            continue
        for name in others:
            if name not in own:
                base.pop(name, None)
    host = creds.get('host')
    source = creds.get('github')
    if source:
        kind, _, name = source.partition(':')
        if kind == 'env':
            token = own.get(name) or base.get(name)
            if not token:
                raise CredentialError(f'{repo}: github = {source!r}, but {name} is not set')
        else:
            token = gh_token(name, host)
        for variable in GITHUB_VARIABLES:
            base.pop(variable, None)
        own['GH_TOKEN'] = token
    if host:
        own['GH_HOST'] = host
        if 'GH_TOKEN' in own:
            own['GH_ENTERPRISE_TOKEN'] = own['GH_TOKEN']
    for key, variables in (
        ('git_name', ('GIT_AUTHOR_NAME', 'GIT_COMMITTER_NAME')),
        ('git_email', ('GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_EMAIL')),
    ):
        if value := creds.get(key):
            own.update(dict.fromkeys(variables, value))
    return base | own


def activate(repo: str, env: Mapping[str, str] | None = None) -> None:
    """Run the rest of this command as the project's identity: for a command that works on one
    project and calls gh or git itself."""
    updated = environment(repo, env=env)
    for name in set(os.environ) - set(updated):
        del os.environ[name]
    os.environ.update(updated)


def token_checks(environ: Mapping[str, str], run=subprocess.run) -> list[tuple[str, str]]:
    """(status, detail) for the GitHub token a project runs with: whose it is, its scopes when
    it is a classic token, and when it expires, from GitHub's response headers."""
    try:
        result = run(
            ['gh', 'api', '--include', 'user'],
            capture_output=True, text=True, timeout=30, env=dict(environ),
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [('fail', f'gh api user: {exc}')]
    if result.returncode != 0:
        return [('fail', f'gh api user: {(result.stderr or result.stdout).strip()[:200]}')]
    head, _, body = result.stdout.partition('\r\n\r\n')
    if not body:
        head, _, body = result.stdout.partition('\n\n')
    headers = {}
    for line in head.splitlines()[1:]:
        name, _, value = line.partition(':')
        headers[name.strip().lower()] = value.strip()
    try:
        login = json.loads(body).get('login', '?')
    except json.JSONDecodeError:
        login = '?'
    found = [('ok', f'acts as {login}')]
    scopes = headers.get('x-oauth-scopes')
    if scopes is not None:
        granted = {s.strip() for s in scopes.split(',') if s.strip()}
        if 'repo' not in granted and 'public_repo' not in granted:
            found.append(
                ('fail', f'classic token without the repo scope (has: {scopes or "none"})')
            )
        else:
            found.append(('ok', f'classic token, scopes: {scopes}'))
    else:
        found.append(
            (
                'ok',
                'fine-grained or app token: its repository permissions are checked by `mason project add`',
            )
        )
    if expires := headers.get('github-authentication-token-expiration'):
        try:
            when = datetime.strptime(expires.replace(' UTC', ' +0000'), '%Y-%m-%d %H:%M:%S %z')
        except ValueError:
            found.append(('warn', f'expires {expires}'))
        else:
            days = (when - datetime.now(UTC)).days
            status = 'fail' if days < 0 else 'warn' if days < 14 else 'ok'
            found.append((status, f'expires {when:%Y-%m-%d} ({days} days)'))
    return found
