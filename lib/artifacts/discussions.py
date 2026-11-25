"""Publish to a repository's GitHub Discussions, through `gh api graphql`.

`gh` has no discussion commands, so this is the GraphQL the reports need: find a category by
name, check whether a discussion with the same title is already there, and create one.
Categories can only be created in GitHub's web interface, so a missing one is reported with how to
add it, never created.

A published report is not edited or replaced here: a correction is a visible comment, made by a
person. So posting a title that already exists in the category is refused.

Each function takes `gh`, a function that runs `gh` with arguments and returns its output, so
tests run without the network.
"""

import json
from collections.abc import Callable
from typing import Any

Gh = Callable[..., str]

CATEGORIES = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
    hasDiscussionsEnabled
    discussionCategories(first: 100) { nodes { id name } }
  }
}
"""

FIND = """
query($search: String!) {
  search(query: $search, type: DISCUSSION, first: 50) {
    nodes { ... on Discussion { title url category { name } } }
  }
}
"""

CREATE = """
mutation($repository: ID!, $category: ID!, $title: String!, $body: String!) {
  createDiscussion(input: {
    repositoryId: $repository, categoryId: $category, title: $title, body: $body
  }) {
    discussion { url }
  }
}
"""


class DiscussionError(Exception):
    pass


def graphql(gh: Gh, query: str, **variables: str) -> dict[str, Any]:
    args = ['api', 'graphql', '-f', f'query={query}']
    for key, value in variables.items():
        args += ['-f', f'{key}={value}']
    try:
        data = json.loads(gh(*args))
    except json.JSONDecodeError as exc:
        raise DiscussionError(f'gh api graphql returned something other than JSON: {exc}') from exc
    if not isinstance(data, dict):
        raise DiscussionError('gh api graphql returned an unexpected response')
    if data.get('errors'):
        messages = '; '.join(str(e.get('message', e)) for e in data['errors'])
        raise DiscussionError(f'GitHub refused the request: {messages}')
    return data.get('data') or {}


def category(gh: Gh, repo: str, name: str) -> tuple[str, str]:
    """(repository id, category id) for the category called `name`."""
    owner, _, repo_name = repo.partition('/')
    if not owner or not repo_name:
        raise DiscussionError(f'a repository is owner/name, got {repo!r}')
    found = graphql(gh, CATEGORIES, owner=owner, name=repo_name).get('repository')
    if not found:
        raise DiscussionError(f'{repo}: no such repository, or no access to it')
    if not found.get('hasDiscussionsEnabled'):
        raise DiscussionError(f'{repo} has Discussions turned off (Settings → General → Features)')
    nodes = (found.get('discussionCategories') or {}).get('nodes') or []
    for node in nodes:
        if node.get('name', '').casefold() == name.casefold():
            return found['id'], node['id']
    known = ', '.join(sorted(n.get('name', '') for n in nodes)) or 'none'
    raise DiscussionError(
        f'{repo} has no discussion category {name!r} (it has: {known}). Categories are created '
        "in the repository's Discussions tab, by someone with admin access."
    )


def existing(gh: Gh, repo: str, category_name: str, title: str) -> str | None:
    """The URL of a discussion in the category with exactly this title, if there is one."""
    escaped = title.replace('"', '\\"')
    data = graphql(gh, FIND, search=f'repo:{repo} in:title "{escaped}"')
    for node in (data.get('search') or {}).get('nodes') or []:
        if not node:
            continue
        in_category = ((node.get('category') or {}).get('name') or '').casefold()
        if node.get('title') == title and in_category == category_name.casefold():
            return node.get('url')
    return None


def post(gh: Gh, repo: str, category_name: str, title: str, body: str) -> str:
    """Create the discussion and return its URL. Refuses a title the category already has."""
    repository_id, category_id = category(gh, repo, category_name)
    already = existing(gh, repo, category_name, title)
    if already:
        raise DiscussionError(
            f'{already} is already "{title}". A published report is not replaced: post a '
            'correction as a comment on it, or delete it first if it should not have been posted.'
        )
    data = graphql(
        gh, CREATE, repository=repository_id, category=category_id, title=title, body=body
    )
    url = (((data.get('createDiscussion') or {}).get('discussion')) or {}).get('url')
    if not url:
        raise DiscussionError('GitHub created no discussion and gave no reason')
    return url
