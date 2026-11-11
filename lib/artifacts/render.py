"""Render a change as the pull request body people read, with the artifacts embedded in it.

Everything a reader sees is derived from the artifacts: the package and alert tables are
computed here, so they can't disagree with the data. Inputs are assumed valid; the
command line validates before it renders.
"""

from collections import Counter
from typing import Any

from . import deps
from .embed import embed

GITHUB_BODY_LIMIT = 65_536

# --- Changes -----------------------------------------------------------------------------------


def change_body(
    change: dict[str, Any],
    findings: list[dict[str, Any]],
    run: dict[str, Any],
    not_in_this_pr: list[dict[str, Any]] = (),  # type: ignore[assignment]
) -> str:
    """The pull request body for a change, with the change, its findings and its run embedded.

    `findings` are the ones this change addresses. `not_in_this_pr` are the pass's other
    findings, listed so the PR says what it deliberately leaves out. `run` is the run that
    opened the PR.
    """
    lines = [change['effect'].strip(), '']
    lines += ['## Findings addressed', '']
    for finding in findings:
        refs = ', '.join(finding['source']['refs'])
        lines.append(f'- **{finding["title"]}** ({finding["severity"]}; {refs})')

    if change.get('dependencies'):
        lines += ['', *_dependency_table(change['dependencies'])]
    if change.get('alerts'):
        lines += ['', *_alert_table(change['alerts'])]

    lines += ['', '## What it touches', '']
    lines += [f'- {item}' for item in change['blast_radius']]

    lines += ['', '## Verification', '']
    if change['verified']:
        lines += ['| Check | Result | Note |', '|---|---|---|']
        for check in change['verified']:
            note = check.get('note', '')
            result = 'runs in CI on this PR' if check['result'] == 'ci' else check['result']
            lines.append(f'| `{_cell(check["check"])}` | {result} | {_cell(note)} |')
    if change['not_verified']:
        lines += ['', '**Not verified:**', '']
        lines += [f'- {item}' for item in change['not_verified']]

    lines += ['', *_signal(change['project'], change['success_signal'])]

    if not_in_this_pr:
        lines += ['', '## Not in this PR', '', *_left_out(not_in_this_pr)]

    lines += ['', '## Revert', '', change['revert'].strip(), '']
    lines += [f'<sub>Opened by {_run_line(run)}.</sub>', '']
    for artifact in (change, *findings, *not_in_this_pr):
        label = 'change.json' if artifact is change else f'finding {artifact["id"]}'
        lines += [embed(artifact, label), '']
    lines += [embed({**run, 'outcome': 'pr_opened'}, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'pull request body')


def _dependency_table(dependencies: list[dict[str, Any]]) -> list[str]:
    lines = [
        '## Package upgrades',
        '',
        '| Package | Before | After | Manifests |',
        '|---|---|---|---|',
    ]
    for dep in dependencies:
        name = f'`{dep["package"]}`'
        notes = []
        if dep['copy']:
            notes.append(f'nested copy: {dep["copy"]}')
        elif not dep['direct']:
            notes.append(f'transitive, via {", ".join(dep["via"])}' if dep['via'] else 'transitive')
        if dep['before'] is None:
            notes.append('new')
        if dep['after'] is None:
            notes.append('removed')
        if notes:
            name += f' *({"; ".join(notes)})*'
        after = dep['after'] or '–'
        if deps.is_major(dep['before'], dep['after']):
            after += ' **(major)**'
        manifests = ', '.join(f'`{m}`' for m in dep['manifests'])
        lines.append(f'| {name} | {dep["before"] or "–"} | {after} | {manifests} |')
    return lines


def _alert_table(alerts: dict[str, Any]) -> list[str]:
    open_before = alerts['open_before']
    closed = Counter(a['severity'] for a in alerts['closed'])
    total_open = sum(open_before.values())
    lines = [
        '## Dependabot alerts addressed',
        '',
        f'{len(alerts["closed"])} of the {total_open} open alerts close when this merges.',
        '',
        '| Severity | Open now | Closed by this PR | Still open |',
        '|---|---:|---:|---:|',
    ]
    for severity in deps.SEVERITIES:
        before = open_before[severity]
        lines.append(
            f'| {severity.capitalize()} | {before} | {closed[severity]} | {before - closed[severity]} |'
        )
    lines.append(
        f'| **Total** | **{total_open}** | **{len(alerts["closed"])}** '
        f'| **{total_open - len(alerts["closed"])}** |'
    )
    if alerts['already_fixed']:
        numbers = ', '.join(f'#{a["number"]} ({a["package"]})' for a in alerts['already_fixed'])
        lines += ['', f'Already fixed before this change, so they should close anyway: {numbers}.']
    if alerts['undecided']:
        numbers = ', '.join(f'#{a["number"]} ({a["package"]})' for a in alerts['undecided'])
        lines += ['', f'Could not decide from version ranges, so check by hand: {numbers}.']
    return lines


def _signal(project: str, signal: dict[str, Any]) -> list[str]:
    days = signal['check_after_days']
    when = 'at merge' if days == 0 else f'{days} day{"" if days == 1 else "s"} after merge'
    # Nothing checks the signal automatically yet, so the body says so, and gives the command
    # where there is one.
    lines = [
        '## How we will know it worked',
        '',
        f'{signal["description"].strip().rstrip(".")}. Check by hand {when}; nothing checks '
        'this automatically yet.',
    ]
    numbers = [ref.removeprefix('dependabot#') for ref in signal['refs']]
    if signal['kind'] == 'alerts_closed' and all(n.isdigit() for n in numbers):
        lines += [
            '',
            '```sh',
            f'for n in {" ".join(numbers)}; do',
            f'  gh api repos/{project}/dependabot/alerts/$n --jq \'"\\(.number) \\(.state)"\'',
            'done',
            '```',
        ]
    return lines


def _left_out(findings: list[dict[str, Any]]) -> list[str]:
    """The findings a PR leaves out, grouped by what happens to them, needing a person first.

    A finding with tracked work is grouped under it, so one plan covering many packages reads
    as one line of work rather than a repeated link. Each line says how many alerts it holds.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        dependency = finding.get('dependency') or {}
        tracked = dependency.get('tracked_by', ...)
        key = 'untracked' if tracked is None else 'other' if tracked is ... else tracked
        groups.setdefault(key, []).append(finding)

    def size(group: list[dict[str, Any]]) -> int:
        return sum(_alert_count(f) for f in group)

    lines: list[str] = []
    order = [k for k in ('untracked', 'other') if k in groups]
    order += sorted(
        (k for k in groups if k not in ('untracked', 'other')), key=lambda k: -size(groups[k])
    )
    for key in order:
        group = sorted(groups[key], key=lambda f: (-_alert_count(f), f['title']))
        heading = {
            'untracked': 'Not tracked anywhere yet',
            'other': 'Left out for the reason given',
        }.get(key) or f'Tracked in {_link(key)}'
        lines += [f'**{heading}** ({_count(size(group), "alert")}):', '']
        for finding in group:
            dependency = finding.get('dependency') or {}
            what = dependency.get('classification', finding['kind']).replace('_', ' ')
            note = f'{_count(_alert_count(finding), "alert")}, {what}'
            line = f'- **{finding["title"]}** ({note})'
            if key == 'other':
                line += f'. {finding["claim"].strip()}'
            lines.append(line)
        lines.append('')
    return lines


def _alert_count(finding: dict[str, Any]) -> int:
    return sum(1 for ref in finding['source']['refs'] if ref.startswith('dependabot#'))


def _link(url: str) -> str:
    """A tracking URL as a reader would name it: #N for an issue or PR, the branch for a tree."""
    parts = url.rstrip('/').split('/')
    if len(parts) >= 2 and parts[-2] in ('issues', 'pull') and parts[-1].isdigit():
        return f'#{parts[-1]}'
    if 'tree' in parts:
        branch = '/'.join(parts[parts.index('tree') + 1 :])
        return f'branch [`{branch}`]({url})'
    return f'<{url}>'


# --- Retrospectives ---------------------------------------------------------------------------

FINDING_GROUPS = (
    ('skill_change', 'Proposed changes to the workflow'),
    ('investigate', 'For the project'),
    ('external', 'Waiting on others'),
)


def retrospective_title(outcomes: dict[str, Any]) -> str:
    period = outcomes['period']
    return f'Dependency retrospective: {period["since"][:10]} to {period["until"][:10]}'


def retrospective_body(
    outcomes: dict[str, Any], findings: list[dict[str, Any]], run: dict[str, Any]
) -> str:
    """The report issue: derived tables from the outcomes, then the retrospective's findings."""
    prs, alerts, policy = outcomes['prs'], outcomes['alerts'], outcomes['policy']
    states = Counter(pr['state'] for pr in prs)
    edited = sum(1 for pr in prs if pr['state'] == 'merged' and pr['edited'])
    stale = sum(1 for pr in prs if pr['stale'])
    fixed_total = sum(alerts['fixed'].values())
    open_total = sum(alerts['open_now'].values())
    overdue = alerts['open_past_target']
    pr_summary = (
        f'{_count(len(prs), "upgrade PR")}: {states["merged"]} merged ({edited} after edits), '
        f'{states["closed"]} closed without merging, {states["open"]} open ({stale} stale).'
        if prs
        else 'No upgrade PRs in this period.'
    )
    lines = [
        f'**{retrospective_title(outcomes)}.**',
        '',
        f'{pr_summary} {_count(fixed_total, "alert")} fixed ({len(alerts["fixed_by_upgrade_prs"])} by upgrade '
        f'PRs), {sum(alerts["opened"].values())} opened, {sum(alerts["dismissed"].values())} '
        f'dismissed. {open_total} open now, {len(overdue)} of them past their target.',
        '',
        '## Time to fix',
        '',
        '| Severity | Target | Fixed | Median days | Slowest | Open now | Past target |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    late = Counter(a['severity'] for a in overdue)
    for severity in deps.SEVERITIES:
        timing = alerts['days_to_fix'][severity]
        lines.append(
            f'| {severity.capitalize()} | {policy["fix_within_days"][severity]} days '
            f'| {alerts["fixed"][severity]} | {_number(timing["median"])} '
            f'| {_number(timing["max"])} | {alerts["open_now"][severity]} | {late[severity]} |'
        )
    if overdue:
        lines += [
            '',
            '<details>',
            f'<summary>The {_count(len(overdue), "alert")} past target</summary>',
            '',
            '| Severity | Alert | Package | Open for |',
            '|---|---:|---|---:|',
        ]
        lines += [
            f'| {a["severity"].capitalize()} | #{a["number"]} | `{a["package"]}` '
            f'| {_number(a["age_days"])} days |'
            for a in overdue
        ]
        lines += ['', '</details>']

    lines += ['', '## Upgrade PRs', '']
    if prs:
        lines += [
            '| PR | Started by | Outcome | Days open | CI | Alerts closed |',
            '|---|---|---|---:|---|---:|',
        ]
        for pr in prs:
            started = f'{pr["run"]["started_by"]} ({pr["run"]["trigger"]})' if pr['run'] else '–'
            lines.append(
                f'| #{pr["number"]} | {_cell(started)} | {_pr_outcome(pr)} '
                f'| {_number(pr["days_open"])} | {_cell(_ci_text(pr["ci"]))} '
                f'| {len(pr["alerts_closed"])} |'
            )
    else:
        lines.append('No upgrade PRs were open in this period.')

    if outcomes['stopped_runs']:
        lines += ['', '## Runs that stopped for an open PR', '']
        for stopped in outcomes['stopped_runs']:
            lines.append(
                f'- `{stopped["id"]}`, started by {stopped["started_by"]} ({stopped["trigger"]}), '
                f'stopped for #{stopped["pr"]} with '
                f"{_count(stopped["alerts_not_covered"], "alert")} it didn't cover."
            )
    if outcomes['reverts']:
        lines += ['', '## Reverted upgrades', '']
        for revert in outcomes['reverts']:
            lines.append(
                f'- {revert["sha"][:10]} {_cell(revert["subject"])} ({revert["date"][:10]})'
            )

    flagged = [p for p in outcomes['packages'] if p['flags']]
    unchecked = [p for p in outcomes['packages'] if p['error']]
    lines += ['', '## Package health', '']
    if flagged:
        lines += [
            f'Stale means no release for {policy["stale_after_months"]} months.',
            '',
            '| Package | Flags | Latest release | Open alerts | Note |',
            '|---|---|---|---:|---|',
        ]
        for package in flagged:
            released = (package['latest_released_at'] or '')[:10] or '–'
            lines.append(
                f'| `{package["package"]}` ({package["ecosystem"]}) | {", ".join(package["flags"])} '
                f'| {package["latest"] or "–"}, {released} | {package["open_alerts"]} '
                f'| {_cell(package["deprecated"] or "")} |'
            )
    else:
        lines.append(
            f'None of the {_count(len(outcomes["packages"]), "package")} checked is stale, deprecated or archived.'
        )
    if unchecked:
        names = ', '.join(f'`{p["package"]}`' for p in unchecked)
        lines += ['', f'Could not check: {names}.']

    lines += ['', '## Findings', '']
    if not findings:
        lines.append('No findings this period.')
    for kind, heading in FINDING_GROUPS:
        group = [f for f in findings if f['kind'] == kind]
        if not group:
            continue
        lines += [f'### {heading}', '']
        for finding in group:
            refs = ', '.join(e['ref'] for e in finding['evidence'])
            lines += [
                f'- **{finding["title"]}** ({finding["severity"]}). {finding["claim"].strip()} '
                f'Evidence: {refs}.'
            ]
        lines.append('')

    lines += ['', f'<sub>Reported by {_run_line(run)}.</sub>', '']
    # People's comments are on the PRs already; leaving them out keeps the embedded data small.
    compact = {**outcomes, 'prs': [{**pr, 'comments': len(pr['comments'])} for pr in prs]}
    for artifact in (compact, *findings):
        label = 'outcomes.json' if artifact is compact else f'finding {artifact["id"]}'
        lines += [embed(artifact, label), '']
    lines += [embed({**run, 'outcome': 'issue_opened'}, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'issue body')


def _pr_outcome(pr: dict[str, Any]) -> str:
    if pr['state'] == 'merged':
        return 'merged after edits' if pr['edited'] else 'merged'
    if pr['state'] == 'closed':
        return 'closed'
    notes = [n for n, on in (('draft', pr['draft']), ('stale', pr['stale'])) if on]
    return 'open' + (f' ({", ".join(notes)})' if notes else '')


def _ci_text(ci: dict[str, list[str]]) -> str:
    if ci['failed']:
        return 'failed: ' + ', '.join(ci['failed'])
    if ci['pending']:
        return 'pending'
    return 'passed' if ci['passed'] else 'none'


def _count(number: int, noun: str) -> str:
    return f'{number} {noun}{"" if number == 1 else "s"}'


def _number(value: float | None) -> str:
    if value is None:
        return '–'
    return f'{value:g}'


# --- Runs ------------------------------------------------------------------------------------


def stopped_comment(run: dict[str, Any], pr: int, missing: list[dict[str, Any]]) -> str:
    """The comment a run leaves on the open upgrade PR it stopped for.

    The PR itself is not changed: a reviewer part-way through it would not notice a new
    commit, so the run says what it saw instead, where the reviewer will see it.
    """
    lines = [
        f'**Run `{run["id"]}` stopped without changing this PR.**',
        '',
        f'Started by {_run_details(run)}. One upgrade PR is open at a time, so it opened no new '
        'PR and left this one as it is.',
        '',
    ]
    if missing:
        noun = 'alert is' if len(missing) == 1 else 'alerts are'
        tally = Counter(a['severity'] for a in missing)
        counts = ', '.join(f'{tally[s]} {s}' for s in deps.SEVERITIES if tally[s])
        lines += [
            f'{len(missing)} open {noun} not covered by this PR ({counts}). The first run after '
            'this PR is merged or closed picks them up.',
            '',
            '<details>',
            f'<summary>The {len(missing)} {"alert" if len(missing) == 1 else "alerts"}</summary>',
            '',
            '| Severity | Alert | Package |',
            '|---|---:|---|',
        ]
        lines += [
            f'| {a["severity"].capitalize()} | #{a["number"]} | `{a["package"]}` |' for a in missing
        ]
        lines += ['', '</details>']
    else:
        lines.append('Every open alert is covered by this PR, or listed in it as left out.')
    record = {
        **run,
        'outcome': 'stopped_pr_open',
        'pr': pr,
        'alerts_not_covered': [a['number'] for a in missing],
    }
    lines += ['', embed(record, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'comment')


def _run_line(run: dict[str, Any]) -> str:
    return f'run `{run["id"]}`, started by {_run_details(run)}'


def _run_details(run: dict[str, Any]) -> str:
    sha = run['produced_by']['automation_sha']
    sha = sha[:10] + (' with uncommitted changes' if sha.endswith('-dirty') else '')
    return (
        f'{run["started_by"]} ({run["trigger"]}) on {run["host"]} at {run["started_at"]}, '
        f'mason {sha}'
    )


# --- Helpers -----------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace('|', '\\|').replace('\n', ' ')


def _within_limit(text: str, what: str) -> str:
    if len(text) > GITHUB_BODY_LIMIT:
        raise ValueError(
            f'the {what} is {len(text)} characters; GitHub allows {GITHUB_BODY_LIMIT}. '
            'Shorten the longest bodies or split the work.'
        )
    return text
