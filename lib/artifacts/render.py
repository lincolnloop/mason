"""Render artifacts as the markdown people read, with the artifacts embedded in it.

Everything a reader sees is derived from the artifacts: a review's disposition and counts, a
change's package and alert tables, a retrospective's figures are computed here, so they can't disagree with the data. Inputs are assumed valid; the
command line validates before it renders.
"""

from collections import Counter
from typing import Any, Literal

from . import deps
from .embed import embed

GITHUB_BODY_LIMIT = 65_536

SEVERITY_ORDER = ('critical', 'important', 'suggestion', 'nit')
SEVERITY_HEADINGS = {
    'critical': 'Critical',
    'important': 'Important',
    'suggestion': 'Suggestions',
    'nit': 'Nits',
}
OPEN = ('new', 'still_open')
BLOCKING = ('critical', 'important')

Inline = Literal['none', 'suggestions', 'all']


# --- Reviews -----------------------------------------------------------------------------------


def disposition(review: dict[str, Any]) -> str:
    open_findings = [f for f in review['findings'] if f['status'] in OPEN]
    if any(f['severity'] in BLOCKING for f in open_findings):
        return 'Changes requested'
    if open_findings:
        return 'No blocking findings'
    return 'No findings'


def counts(review: dict[str, Any]) -> str:
    tally = Counter(f['severity'] for f in review['findings'] if f['status'] in OPEN)
    parts = [f'{tally[s]} {_plural(s, tally[s])}' for s in SEVERITY_ORDER if tally[s]]
    return ', '.join(parts) if parts else 'none open'


def review_comment(review: dict[str, Any], run: dict[str, Any], inline: Inline = 'none') -> str:
    """The single consolidated comment: every finding, plus the embedded review."""
    findings = review['findings']
    open_findings = [f for f in findings if f['status'] in OPEN]
    lines = [
        '## Code review',
        '',
        f'**{disposition(review)}** ({counts(review)})',
        '',
        review['summary'].strip(),
    ]
    for severity in SEVERITY_ORDER:
        group = [f for f in open_findings if f['severity'] == severity]
        if not group:
            continue
        lines += ['', f'### {SEVERITY_HEADINGS[severity]}']
        for finding in group:
            lines += ['', *_finding_lines(finding, posted_inline=_is_inline(finding, inline))]

    earlier = [f for f in findings if f['status'] not in OPEN]
    if earlier:
        lines += ['', '### Since the last review', '']
        for finding in earlier:
            lines.append(
                f'- **{finding["id"]}. {finding["title"]}**: {finding["status"].replace("_", " ")}'
            )

    lines += [
        '',
        f'<sub>Reviewed {review["head_sha"][:10]} by {_run_line(run)}.</sub>',
        '',
        embed(review),
        '',
        embed({**run, 'outcome': 'review_posted'}, 'run.json'),
    ]
    return _within_limit('\n'.join(lines) + '\n', 'review comment')


def review_payload(
    review: dict[str, Any], run: dict[str, Any], inline: Inline = 'none'
) -> dict[str, Any]:
    """The body for POST /repos/{owner}/{repo}/pulls/{number}/reviews.

    Always event COMMENT: an agent's review neither approves nor blocks. Bound to head_sha, so
    GitHub refuses it if the PR has moved on.
    """
    comments = []
    for finding in review['findings']:
        if finding['status'] not in OPEN or not _is_inline(finding, inline):
            continue
        location = finding['location']
        comment: dict[str, Any] = {
            'path': location['path'],
            'line': location['line'],
            'side': location['side'],
            'body': _inline_body(finding),
        }
        if 'start_line' in location:
            comment['start_line'] = location['start_line']
            comment['start_side'] = location['start_side']
        comments.append(comment)
    return {
        'commit_id': review['head_sha'],
        'event': 'COMMENT',
        'body': review_comment(review, run, inline),
        'comments': comments,
    }


def _is_inline(finding: dict[str, Any], inline: Inline) -> bool:
    location = finding.get('location')
    if inline == 'none' or not location or not location['in_diff']:
        return False
    return inline == 'all' or finding['suggestion'] is not None


def _finding_lines(finding: dict[str, Any], posted_inline: bool) -> list[str]:
    title = f'**{finding["id"]}. {finding["title"]}**'
    location = finding.get('location')
    if location:
        span = location['line']
        if 'start_line' in location:
            span = f'{location["start_line"]}-{location["line"]}'
        side = ' (removed line)' if location['side'] == 'LEFT' else ''
        title += f' `{location["path"]}:{span}`{side}'
    notes = []
    if finding['status'] == 'still_open':
        notes.append('still open from the last review')
    if finding['evidence'] == 'inferred':
        notes.append('inferred, not verified')
    if finding['rule']:
        notes.append(f'rule `{finding["rule"]}`')
    if posted_inline:
        notes.append('suggestion posted inline')
    lines = [title + (f' ({"; ".join(notes)})' if notes else ''), '', finding['body'].strip()]
    if finding['suggestion'] is not None and not posted_inline:
        lines += ['', 'Suggested replacement:', '', '```', finding['suggestion'], '```']
    return lines


def _inline_body(finding: dict[str, Any]) -> str:
    severity = SEVERITY_HEADINGS[finding['severity']].rstrip('s')
    body = f'**{severity}: {finding["title"]}** ({finding["id"]})\n\n{finding["body"].strip()}'
    if finding['suggestion'] is not None:
        body += f'\n\n```suggestion\n{finding["suggestion"]}\n```'
    return body


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

    errors = [f for f in findings if f.get('impact')]
    if errors:
        lines += ['', *_errors_table(errors)]
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


def _errors_table(findings: list[dict[str, Any]]) -> list[str]:
    """How often the errors a change addresses happen, from the findings' impact."""
    lines = [
        '## Errors addressed',
        '',
        '| Error | Issues | Events | Users |',
        '|---|---|---:|---:|',
    ]
    for finding in findings:
        impact = finding['impact']
        lines.append(
            f'| {_cell(finding["title"])} | {", ".join(finding["source"]["refs"])} '
            f'| {impact.get("events", "–")} | {impact.get("users", "–")} |'
        )
    days = {f['impact']['period_days'] for f in findings}
    if len(days) == 1:
        lines += ['', f'Events and users over the last {days.pop()} days.']
    return lines


def _signal(project: str, signal: dict[str, Any]) -> list[str]:
    days = signal['check_after_days']
    when = 'at merge' if days == 0 else f'{days} day{"" if days == 1 else "s"} after merge'
    if signal['kind'] == 'sentry_quiet':
        return [
            '## How we will know it worked',
            '',
            f'{signal["description"].strip().rstrip(".")}. The Sentry retrospective checks this '
            f"{when}, from each issue's last event: {", ".join(signal["refs"])}.",
        ]
    # Nothing checks this signal automatically yet, so the body says so, and gives the command
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
        return sum(_measure(f)[0] for f in group)

    lines: list[str] = []
    order = [k for k in ('untracked', 'other') if k in groups]
    order += sorted(
        (k for k in groups if k not in ('untracked', 'other')), key=lambda k: -size(groups[k])
    )
    for key in order:
        group = sorted(groups[key], key=lambda f: (-_measure(f)[0], f['title']))
        noun = _measure(group[0])[1]
        heading = {
            'untracked': 'Not tracked anywhere yet',
            'other': 'Left out for the reason given',
        }.get(key) or f'Tracked in {_link(key)}'
        lines += [f'**{heading}** ({_count(size(group), noun)}):', '']
        for finding in group:
            dependency = finding.get('dependency') or {}
            what = dependency.get('classification', finding['kind']).replace('_', ' ')
            note = f'{_count(*_measure(finding))}, {what}'
            line = f'- **{finding["title"]}** ({note})'
            if key == 'other':
                line += f'. {finding["claim"].strip()}'
            lines.append(line)
        lines.append('')
    return lines


def _measure(finding: dict[str, Any]) -> tuple[int, str]:
    """How big a finding is: its Dependabot alerts, or for an error its events."""
    alerts = sum(1 for ref in finding['source']['refs'] if ref.startswith('dependabot#'))
    if alerts or not finding.get('impact'):
        return alerts, 'alert'
    return finding['impact'].get('events', 0), 'event'


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

    lines += _findings_section(findings)
    lines += ['', f'<sub>Reported by {_run_line(run)}.</sub>', '']
    # People's comments are on the PRs already; leaving them out keeps the embedded data small.
    compact = {**outcomes, 'prs': [{**pr, 'comments': len(pr['comments'])} for pr in prs]}
    for artifact in (compact, *findings):
        label = 'outcomes.json' if artifact is compact else f'finding {artifact["id"]}'
        lines += [embed(artifact, label), '']
    lines += [embed({**run, 'outcome': 'issue_opened'}, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'issue body')


def _findings_section(findings: list[dict[str, Any]]) -> list[str]:
    lines = ['', '## Findings', '']
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
    return lines


def review_retrospective_title(outcomes: dict[str, Any]) -> str:
    period = outcomes['period']
    return f'Code review retrospective: {period["since"][:10]} to {period["until"][:10]}'


def review_retrospective_body(
    outcomes: dict[str, Any], findings: list[dict[str, Any]], run: dict[str, Any]
) -> str:
    """The report issue for a code-review retrospective: what happened to the findings it
    posted, by category and severity, then the retrospective's own findings."""
    reviews = outcomes['reviews']
    posted = [f for r in reviews for f in r['findings']]
    lines = [
        f'**{review_retrospective_title(outcomes)}.**',
        '',
        f'{_count(len(reviews), "review")} on {_count(len({r["pr"] for r in reviews}), "PR")}, '
        f'with {_count(len(posted), "finding")}. A finding counts as acted on when its lines '
        'changed in a later commit, or a follow-up review found it addressed.',
    ]
    if posted:
        for heading, key in (('By category', 'category'), ('By severity', 'severity')):
            lines += [
                '',
                f'## {heading}',
                '',
                f'| {key.capitalize()} | Findings | Acted on | Declined | Withdrawn | No sign |',
                '|---|---:|---:|---:|---:|---:|',
            ]
            values = sorted(
                {f[key] for f in posted},
                key=lambda v: SEVERITY_ORDER.index(v) if key == 'severity' else v,
            )
            for value in values:
                group = [f for f in posted if f[key] == value]
                tally = Counter(_reaction(f) for f in group)
                lines.append(
                    f'| {_cell(value)} | {len(group)} | {tally["acted on"]} | {tally["declined"]} '
                    f'| {tally["withdrawn"]} | {tally["no sign"]} |'
                )
    lines += _findings_section(findings)
    lines += ['', f'<sub>Reported by {_run_line(run)}.</sub>', '']
    # People's words are on the PRs already; leaving them out keeps the embedded data small.
    compact = {
        **outcomes,
        'reviews': [{**r, 'said_after': len(r['said_after'])} for r in reviews],
    }
    for artifact in (compact, *findings):
        label = 'review-outcomes.json' if artifact is compact else f'finding {artifact["id"]}'
        lines += [embed(artifact, label), '']
    lines += [embed({**run, 'outcome': 'issue_opened'}, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'issue body')


TRIAGE_GROUPS = (
    ('data_fix', 'Data to fix'),
    ('investigate', 'To investigate'),
    ('archive', 'To archive or resolve in Sentry'),
    ('external', 'Waiting on others'),
    ('code_fix', 'Code fixes not made in this run'),
)


def triage_title(environment: str, run: dict[str, Any]) -> str:
    return f'Sentry triage ({environment}): {run["started_at"][:10]}'


def triage_body(environment: str, findings: list[dict[str, Any]], run: dict[str, Any]) -> str:
    """The issue a sentry-errors run opens when it has findings for a person but no fix to make."""
    lines = [
        f'**{triage_title(environment, run)}.**',
        '',
        f'{_count(len(findings), "finding")} that need a person, from the unresolved errors in '
        f'{environment}. Errors already in an open PR or triage issue are left out.',
    ]
    for kind, heading in TRIAGE_GROUPS:
        group = sorted((f for f in findings if f['kind'] == kind), key=lambda f: -_measure(f)[0])
        if not group:
            continue
        lines += ['', f'## {heading}', '']
        for finding in group:
            refs = ', '.join(finding['source']['refs'])
            impact = finding.get('impact') or {}
            weight = (
                f'{impact.get("events", 0)} events, {impact.get("users", 0)} users'
                if impact
                else finding['severity']
            )
            lines.append(f'- **{finding["title"]}** ({refs}; {weight}). {finding["claim"].strip()}')
    lines += ['', f'<sub>Opened by {_run_line(run)}.</sub>', '']
    for finding in findings:
        lines += [embed(finding, f'finding {finding["id"]}'), '']
    lines += [embed({**run, 'outcome': 'issue_opened'}, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'issue body')


def sentry_retrospective_title(outcomes: dict[str, Any]) -> str:
    period = outcomes['period']
    return f'Sentry retrospective: {period["since"][:10]} to {period["until"][:10]}'


def sentry_retrospective_body(
    outcomes: dict[str, Any], findings: list[dict[str, Any]], run: dict[str, Any]
) -> str:
    """The report issue for a Sentry retrospective: did the fixes work, what is still open."""
    prs, triage = outcomes['prs'], outcomes['triage_issues']
    signals = Counter(pr['signal']['status'] for pr in prs)
    lines = [
        f'**{sentry_retrospective_title(outcomes)}.**',
        '',
        f'{_count(len(prs), "fix PR")}: {signals["met"]} working (quiet for '
        f'{outcomes["policy"]["quiet_after_days"]} days after merge), {signals["not_met"]} not '
        f'working (errors after the fix was deployed), {signals["pending"]} still being watched, '
        f'{signals["not_applicable"]} not merged. {_count(len(triage), "triage issue")}, '
        f'{sum(1 for t in triage if t["state"] == "open")} still open.',
        '',
        '## Fix PRs',
        '',
    ]
    if prs:
        lines += [
            '| PR | Outcome | Issues | Signal | Last event after merge |',
            '|---|---|---|---|---|',
        ]
        for pr in prs:
            issues = pr['signal']['issues']
            late = [i['short_id'] for i in issues if i['after_fix']]
            lines.append(
                f'| #{pr["number"]} | {_pr_outcome(pr)} | {", ".join(i["short_id"] for i in issues) or "–"} '
                f'| {pr["signal"]["status"].replace("_", " ")} | {", ".join(late) or "–"} |'
            )
    else:
        lines.append('No fix PRs in this period.')
    lines += ['', '## Unresolved now', '', '| Environment | Unresolved issues |', '|---|---:|']
    lines += [f'| {env} | {count} |' for env, count in outcomes['unresolved_now'].items()]
    if triage:
        lines += ['', '## Triage issues', '']
        for issue in triage:
            lines.append(
                f'- #{issue["number"]} ({issue["state"]}, {_count(len(issue["findings"]), "finding")}, '
                f'opened {issue["created_at"][:10]})'
            )
    lines += _findings_section(findings)
    lines += ['', f'<sub>Reported by {_run_line(run)}.</sub>', '']
    compact = {**outcomes, 'prs': [{**pr, 'comments': len(pr['comments'])} for pr in prs]}
    for artifact in (compact, *findings):
        label = 'sentry-outcomes.json' if artifact is compact else f'finding {artifact["id"]}'
        lines += [embed(artifact, label), '']
    lines += [embed({**run, 'outcome': 'issue_opened'}, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'issue body')


def _reaction(finding: dict[str, Any]) -> str:
    if finding['later_status'] == 'addressed' or finding['lines_changed']:
        return 'acted on'
    if finding['later_status'] in ('declined', 'withdrawn'):
        return finding['later_status']
    return 'no sign'


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


def _plural(severity: str, number: int) -> str:
    word = {'suggestion': 'suggestion', 'nit': 'nit'}.get(severity, severity)
    return word + ('s' if number != 1 and severity in ('suggestion', 'nit') else '')


def _cell(text: str) -> str:
    return text.replace('|', '\\|').replace('\n', ' ')


def _within_limit(text: str, what: str) -> str:
    if len(text) > GITHUB_BODY_LIMIT:
        raise ValueError(
            f'the {what} is {len(text)} characters; GitHub allows {GITHUB_BODY_LIMIT}. '
            'Shorten the longest bodies or split the work.'
        )
    return text
