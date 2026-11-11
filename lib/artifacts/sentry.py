"""Read a project's errors from Sentry's REST API, keeping what a fix needs and no personal data.

Authentication is a Sentry token in `SENTRY_AUTH_TOKEN`, so unattended runs work without an
interactive login. Every endpoint used here accepts `event:read`, and nothing more is needed:
a personal auth token with that scope, or an internal integration with read access to issues
and events. Organization auth tokens are for CI, and Sentry doesn't document them as reaching
these endpoints. Everything is read; nothing is changed in Sentry.

**Personal data stays in Sentry.** What these functions return goes to an agent and from there,
possibly, into a pull request. So an event is cut down to its exception, its in-app stack frames
and a few tags (release, environment, transaction), and the text kept is scrubbed of email
addresses, IP addresses and long numbers. User, request, cookie, header and context data is never
kept. That is a best effort, not a guarantee: the skill still must not copy event text into
GitHub beyond what explains the bug.
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

USER_AGENT = 'mason-sentry'
TOKEN_VARIABLE = 'SENTRY_AUTH_TOKEN'
TEXT_LIMIT = 500
KEPT_TAGS = ('release', 'environment', 'transaction', 'server_name', 'level', 'handled')

EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
IPV4 = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
LONG_NUMBER = re.compile(r'\b\d{6,}\b')
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next";\s*results="true"')

# (url) -> (parsed JSON, Link header)
Fetch = Callable[[str], tuple[Any, str]]


class SentryError(Exception):
    pass


def scrub(text: str | None, limit: int = TEXT_LIMIT) -> str | None:
    if text is None:
        return None
    text = EMAIL.sub('<email>', text)
    text = IPV4.sub('<ip>', text)
    text = LONG_NUMBER.sub('<number>', text)
    return text[:limit]


class Client:
    def __init__(self, base_url: str, org: str, project: str, fetch: Fetch | None = None):
        self.base = base_url.rstrip('/') + '/api/0'
        self.org, self.project = org, project
        self._fetch = fetch or _http_fetch

    def _get(self, path: str, **params: str) -> tuple[Any, str]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return self._fetch(f'{self.base}{path}' + (f'?{query}' if query else ''))

    def unresolved(self, environment: str, period: str, limit: int = 300) -> list[dict[str, Any]]:
        """Unresolved issues seen in `environment` within `period` (e.g. '14d'), most frequent
        first, as summaries."""
        found: list[dict[str, Any]] = []
        url_path = f'/projects/{self.org}/{self.project}/issues/'
        data, link = self._get(
            url_path,
            query='is:unresolved',
            environment=environment,
            statsPeriod=period,
            sort='freq',
            limit='100',
        )
        while True:
            found.extend(issue_summary(issue) for issue in data)
            following = NEXT_LINK.search(link or '')
            if not following or len(found) >= limit:
                return found[:limit]
            data, link = self._fetch(following.group(1))

    def issue(self, issue_id: str, environment: str | None = None) -> dict[str, Any]:
        data, _ = self._get(
            f'/organizations/{self.org}/issues/{issue_id}/', environment=environment
        )
        return issue_detail(data)

    def by_short_id(self, short_id: str) -> str:
        """The numeric issue id for a short id such as 'EXAMPLE-1A2'."""
        data, _ = self._get(f'/organizations/{self.org}/shortids/{short_id}/')
        return str(data['groupId'])

    def latest_event(self, issue_id: str, environment: str | None = None) -> dict[str, Any]:
        data, _ = self._get(
            f'/organizations/{self.org}/issues/{issue_id}/events/latest/', environment=environment
        )
        return event_summary(data)


def issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    metadata = issue.get('metadata') or {}
    return {
        'id': str(issue['id']),
        'short_id': issue['shortId'],
        'title': scrub(issue.get('title')),
        'culprit': scrub(issue.get('culprit')),
        'level': issue.get('level'),
        'category': issue.get('issueCategory') or 'error',
        'type': issue.get('issueType'),
        'exception_type': metadata.get('type'),
        'events': int(issue.get('count') or 0),
        'users': int(issue.get('userCount') or 0),
        'first_seen': issue.get('firstSeen'),
        'last_seen': issue.get('lastSeen'),
        'permalink': issue.get('permalink'),
    }


def issue_detail(issue: dict[str, Any]) -> dict[str, Any]:
    first, last = issue.get('firstRelease') or {}, issue.get('lastRelease') or {}
    return {
        **issue_summary(issue),
        'status': issue.get('status'),
        'substatus': issue.get('substatus'),
        'first_release': first.get('version'),
        'last_release': last.get('version'),
    }


def event_summary(event: dict[str, Any]) -> dict[str, Any]:
    """An event cut down to what explains the error: no user, request or context data."""
    tags = {t['key']: t['value'] for t in event.get('tags') or [] if t.get('key') in KEPT_TAGS}
    exceptions = []
    for entry in event.get('entries') or []:
        if entry.get('type') != 'exception':
            continue
        for value in (entry.get('data') or {}).get('values') or []:
            frames = ((value.get('stacktrace') or {}).get('frames')) or []
            exceptions.append(
                {
                    'type': value.get('type'),
                    'value': scrub(value.get('value')),
                    'frames': [
                        {
                            'file': frame.get('filename') or frame.get('absPath'),
                            'function': frame.get('function'),
                            'line': frame.get('lineNo'),
                            'code': scrub(_context_line(frame), 200),
                        }
                        for frame in frames
                        if frame.get('inApp')
                    ][-15:],
                }
            )
    return {
        'event_id': event.get('eventID') or event.get('id'),
        'date': event.get('dateCreated'),
        'release': (event.get('release') or {}).get('version') or tags.get('release'),
        'tags': tags,
        'message': scrub(event.get('message') or None),
        'exceptions': exceptions,
    }


def _context_line(frame: dict[str, Any]) -> str | None:
    for number, code in frame.get('context') or []:
        if number == frame.get('lineNo'):
            return code
    return None


def client_from_config(settings: dict[str, Any]) -> Client:
    return Client(settings['sentry_url'], settings['sentry_org'], settings['sentry_project'])


def _http_fetch(url: str) -> tuple[Any, str]:
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        raise SentryError(
            f'{TOKEN_VARIABLE} is not set: create a Sentry personal auth token with the '
            'event:read scope'
        )
    request = urllib.request.Request(
        url,
        headers={
            'Authorization': f'Bearer {token}',
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response), response.headers.get('Link', '')
    except urllib.error.HTTPError as exc:
        raise SentryError(f'Sentry answered {exc.code} for {url.split("?")[0]}') from exc
    except urllib.error.URLError as exc:
        raise SentryError(f'could not reach Sentry: {exc.reason}') from exc
