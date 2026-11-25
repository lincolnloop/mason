"""Render artifacts as the markdown people read, with the artifacts embedded in it.

Everything a reader sees is derived from the artifacts: tables, counts and figures are computed
here, so they can't disagree with the data. Inputs are assumed valid; the command line validates
before it renders.
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
            lines += ['', *_finding_lines(finding, posted_inline=is_inline(finding, inline))]

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
        # The setting it was posted with travels with it, for the retrospective to compare.
        embed({**review, 'inline': inline}),
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
        if finding['status'] not in OPEN or not is_inline(finding, inline):
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


def is_inline(finding: dict[str, Any], inline: Inline) -> bool:
    """Whether a finding is posted as an inline comment under the `inline` setting.

    Only `new` findings: an earlier finding carried into a follow-up already has its comment,
    which GitHub keeps on the latest diff while its lines are unchanged, and marks outdated once
    they change. Posting it again would put two comments on one line.
    """
    location = finding.get('location')
    if inline == 'none' or finding['status'] != 'new' or not location or not location['in_diff']:
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
            f"{when}, from each issue's last event: {', '.join(signal['refs'])}.",
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

    def totals(group: list[dict[str, Any]]) -> str:
        by_noun: dict[str, int] = {}
        for finding in group:
            count, noun = _measure(finding)
            by_noun[noun] = by_noun.get(noun, 0) + count
        return ', '.join(_count(count, noun) for noun, count in by_noun.items())

    lines: list[str] = []
    order = [k for k in ('untracked', 'other') if k in groups]
    order += sorted(
        (k for k in groups if k not in ('untracked', 'other')), key=lambda k: -size(groups[k])
    )
    for key in order:
        # Most severe first, then the biggest: a small important finding outranks a large nit.
        group = sorted(
            groups[key],
            key=lambda f: (SEVERITY_RANK.get(f['severity'], 3), -_measure(f)[0], f['title']),
        )

        heading = {
            'untracked': 'Not tracked anywhere yet',
            'other': 'Left out for the reason given',
        }.get(key) or f'Tracked in {_link(key)}'
        lines += [f'**{heading}** ({totals(group)}):', '']
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
    """How big a finding is: its Dependabot alerts; for an error, its events; otherwise how many
    things it cites."""
    refs = finding['source']['refs']
    alerts = sum(1 for ref in refs if ref.startswith('dependabot#'))
    if alerts:
        return alerts, 'alert'
    if finding.get('impact'):
        return finding['impact'].get('events', 0), 'event'
    return len(refs), 'issue'


SEVERITY_RANK = {'critical': 0, 'important': 1, 'suggestion': 2}


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
    fixed_advisories = sum(alerts['advisories']['fixed'].values())
    moved_total = sum(alerts['moved'].values())
    open_total = sum(alerts['open_at_end'].values())
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
        f'{pr_summary} {_count(fixed_total, "alert")} fixed '
        f'({fixed_advisories} {"advisory" if fixed_advisories == 1 else "advisories"}; '
        f'{len(alerts["fixed_by_upgrade_prs"])} by upgrade PRs), '
        f'{sum(alerts["opened"].values())} opened, {sum(alerts["dismissed"].values())} dismissed'
        + (
            f', {moved_total} closed on one manifest but still open on another'
            if moved_total
            else ''
        )
        + f'. {open_total} open at the end of the period, {len(overdue)} of them past their '
        'target.',
        '',
        '## Time to fix',
        '',
        '| Severity | Target | Fixed | Median days | Slowest | Open at end | Past target |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    late = Counter(a['severity'] for a in overdue)
    for severity in deps.SEVERITIES:
        timing = alerts['days_to_fix'][severity]
        lines.append(
            f'| {severity.capitalize()} | {policy["fix_within_days"][severity]} days '
            f'| {alerts["fixed"][severity]} | {_number(timing["median"])} '
            f'| {_number(timing["max"])} | {alerts["open_at_end"][severity]} | {late[severity]} |'
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
                f"{_count(stopped['alerts_not_covered'], 'alert')} it didn't cover."
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


# --- Dependency assessments ---------------------------------------------------------------------

# The order an assessment lists its findings in: what needs a decision first, what will be done
# next, then what is waiting on someone else.
ASSESSMENT_SECTIONS = (
    (
        'eol',
        'End-of-life lines',
        'No fix will come for the installed line: the dependency has to move to a supported one.',
    ),
    (
        'major',
        'Major upgrades',
        'Fixed only in a new major version: each needs its own PR and test pass.',
    ),
    (
        'blocked',
        "Blocked by a parent's range",
        'A fix exists, but another package holds this one back.',
    ),
    ('easy', 'Easy upgrades', 'The next dependency-upgrades run applies these.'),
    ('no_fix', 'No upstream fix', 'Nothing to install yet.'),
)


def assessment_title(assessment: dict[str, Any]) -> str:
    return f'Dependency assessment: {_month_name(assessment["assessed_at"][:7])}'


def assessment_body(
    assessment: dict[str, Any],
    findings: list[dict[str, Any]],
    run: dict[str, Any],
    previous: str | None = None,
) -> str:
    """The month's assessment issue: the current state of every open alert, replaced on each run.

    `previous` is the link to the previous month's issue, when this one starts a month."""
    alerts = assessment['alerts']
    by_finding: dict[str, list[dict[str, Any]]] = {}
    for row in alerts:
        if row['finding']:
            by_finding.setdefault(row['finding'], []).append(row)
    severities = Counter(row['severity'] for row in alerts)
    counts = ', '.join(f'{severities[s]} {s}' for s in deps.SEVERITIES if severities[s])
    lines = [
        f'**{_count(len(alerts), "open alert")}** ({counts or "none"}), assessed '
        f'{_day(assessment["assessed_at"])} against `{assessment["commit"][:10]}`. This issue '
        'always shows the latest assessment; each run adds a comment saying what changed.',
        '',
    ]
    if previous:
        lines += [f'Continues {previous}.', '']
    by_id = {f['id']: f for f in findings}
    for key, heading, explanation in ASSESSMENT_SECTIONS:
        group = [
            by_id[i]
            for i in assessment['findings']
            if i in by_id and by_id[i].get('dependency', {}).get('classification') == key
        ]
        if not group:
            continue
        lines += [f'## {heading}', '', explanation, '']
        lines += [
            '| Package | Installed | Fixed in | Alerts | Most severe | Tracked in |',
            '|---|---|---|---:|---|---|',
        ]
        for item in sorted(
            group, key=lambda f: (_worst(by_finding.get(f['id'], [])), f['dependency']['package'])
        ):
            dependency = item['dependency']
            rows = by_finding.get(item['id'], [])
            tracked = dependency.get('tracked_by', ...)
            where = '–' if tracked is ... else 'not tracked' if tracked is None else _link(tracked)
            lines.append(
                f'| `{dependency["package"]}` ({dependency["ecosystem"]}) | {dependency["installed"]} '
                f'| {dependency["fixed"] or "–"} | {len(rows)} '
                f'| {deps.SEVERITIES[_worst(rows)].capitalize() if rows else "–"} | {where} |'
            )
        lines.append('')
    fixed = [row for row in alerts if row['classification'] == 'already_fixed']
    if fixed:
        numbers = ', '.join(f'#{row["number"]}' for row in fixed)
        lines += [
            '## Already fixed',
            '',
            f'Installed versions already fix {numbers}; Dependabot closes them on its next scan.',
            '',
        ]
    lines += [
        f'<sub>Assessed by {_run_line(run)}.</sub>',
        '',
        embed(assessment, 'assessment.json'),
        '',
    ]
    for item in sorted(findings, key=lambda f: f['id']):
        lines += [embed(item, f'finding {item["id"]}'), '']
    lines += [embed({**run, 'outcome': 'assessed'}, 'run.json'), '']
    return _within_limit('\n'.join(lines), 'issue body')


def assessment_comment(
    changes: dict[str, list[Any]], assessment: dict[str, Any], run: dict[str, Any]
) -> str:
    """What changed since the previous assessment, as a comment on the issue."""
    new, closed, moved = changes['new'], changes['closed'], changes['reclassified']
    if not (new or closed or moved):
        summary = 'No change since the previous assessment.'
    else:
        summary = (
            '; '.join(
                part
                for part in (
                    f'{_count(len(new), "new alert")}' if new else '',
                    f'{len(closed)} closed' if closed else '',
                    f'{len(moved)} reclassified' if moved else '',
                )
                if part
            )
            + '.'
        )
    lines = [f'**Reassessed {_day(assessment["assessed_at"])}:** {summary}', '']

    def row(item: dict[str, Any], extra: str = '') -> str:
        return f'- #{item["number"]} `{item["package"]}` ({item["severity"]}): {item["classification"].replace("_", " ")}{extra}'

    if new:
        lines += ['New:', *(row(i) for i in new), '']
    if closed:
        lines += [
            'Closed:',
            *(f'- #{i["number"]} `{i["package"]}` ({i["severity"]})' for i in closed),
            '',
        ]
    if moved:
        lines += [
            'Reclassified:',
            *(row(i, f', was {i["was"].replace("_", " ")}') for i in moved),
            '',
        ]
    lines += [f'<sub>{_run_line(run)}.</sub>', '']
    return _within_limit('\n'.join(lines), 'comment')


def _worst(rows: list[dict[str, Any]]) -> int:
    """The index in deps.SEVERITIES of the most severe of `rows` (len(SEVERITIES) if empty)."""
    return min((deps.SEVERITIES.index(r['severity']) for r in rows), default=len(deps.SEVERITIES))


# --- Dependency reports ------------------------------------------------------------------------

MONTH_NAMES = (
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
)
GROUP_LABELS = {
    'production': "Production: the site's own code",
    'production_build': 'Production: build tools run on the servers',
    'development': 'Development tools only',
    'not_deployed': 'Not deployed',
    'unclassified': 'Not classified',
}
# Notes the skill writes go into a published page; they must not carry an embedded artifact.
NOTES_LIMIT = 8_000


def _month_name(month: str) -> str:
    year, number = month.split('-')
    return f'{MONTH_NAMES[int(number) - 1]} {year}'


def dependency_report_title(report: dict[str, Any]) -> str:
    return f'Dependency report: {_month_name(report["period"]["month"])}'


def dependency_report_body(report: dict[str, Any], run: dict[str, Any], notes: str = '') -> str:
    """The published report: a headline any reader can stop after, then the detail and data.

    Every figure is from the data. `notes` is the skill's commentary on what is still open and
    the plan for it, placed under its own heading.
    """
    if '<!-- automation:artifact' in notes:
        raise ValueError('the notes must not contain an embedded artifact')
    if len(notes) > NOTES_LIMIT:
        raise ValueError(f'the notes are {len(notes)} characters; keep them under {NOTES_LIMIT}')
    period, month, ytd = report['period'], report['month'], report['year_to_date']
    policy = report['policy']['fix_within_days']
    name = _month_name(period['month'])
    to_date = '' if period['complete'] else f' (to {_day(period["until"])})'
    fixed_vulns = sum(month['advisories']['fixed'].values())
    fixed_alerts = sum(month['fixed'].values())
    critical_fixed = month['advisories']['fixed']['critical']
    open_vulns = sum(month['advisories']['open_at_end'].values())
    open_alerts = sum(month['open_at_end'].values())
    open_start = sum(month['open_at_start'].values())
    critical_open = month['advisories']['open_at_end']['critical']
    within = sum(month['fixed_within_target'].values())
    headline = (
        f'**{name}{to_date}: {_count(fixed_vulns, "vulnerability fixed", "vulnerabilities fixed")} '
        f'({_count(fixed_alerts, "alert")})'
        + (f', {critical_fixed} of them critical' if critical_fixed else '')
        + f'. {critical_open} critical {"vulnerability" if critical_open == 1 else "vulnerabilities"} '
        f'open at the end of the month, and {open_vulns} in all ({_count(open_alerts, "alert")}, '
        f'against {open_start} at the start of the month).'
        + _production_sentence(report['groups'], open_vulns)
        + (f' {within} of {fixed_alerts} fixes met their target time.' if fixed_alerts else '')
        + '**'
    )
    ytd_vulns = sum(ytd['advisories']['fixed'].values())
    lines = [
        headline,
        '',
        f'Year to date: {_count(ytd_vulns, "vulnerability", "vulnerabilities")} fixed '
        f'({_count(sum(ytd["fixed"].values()), "alert")}) since {_day(ytd["since"])}.',
        '',
    ]
    lines += ['## This month', '']
    lines += _severity_table(month, policy)
    not_fixes = []
    if sum(month['moved'].values()):
        not_fixes.append(
            f'{_count(sum(month["moved"].values()), "alert")} closed on one file while still open '
            'on another'
        )
    if sum(month['dismissed'].values()):
        not_fixes.append(f'{sum(month["dismissed"].values())} dismissed')
    if not_fixes:
        lines += ['', f'Not counted as fixes: {"; ".join(not_fixes)}.']
    lines += ['', '## Where they are', '']
    lines += _group_table(report['groups'])
    lines += [
        '',
        f'## Year to date, since {_day(ytd["since"])}',
        '',
        '| Month | Vulnerabilities fixed | Alerts fixed | Alerts open at end | Critical open at end |',
        '|---|---:|---:|---:|---:|',
    ]
    for row in report['months']:
        lines.append(
            f'| {_month_name(row["month"])} | {row["advisories_fixed"]} | {row["fixed"]} '
            f'| {row["open_at_end"]} | {row["critical_open_at_end"]} |'
        )
    lines.append(
        f'| **Year to date** | **{sum(ytd["advisories"]["fixed"].values())}** '
        f'| **{sum(ytd["fixed"].values())}** | | |'
    )
    lines += [
        '',
        'A vulnerability fixed in two months is counted in each month but once in the year to '
        'date, so the months can add up to more than the total.',
        '',
        "## What's still open",
        '',
    ]
    if notes.strip():
        lines += [notes.strip(), '']
    rows = report['open']
    if rows:
        lines += [
            '<details>',
            f'<summary>Open at the end of the month, by package ({_count(len(rows), "package")})</summary>',
            '',
            '| Package | Most severe | Vulnerabilities | Alerts | Oldest | Group | Files |',
            '|---|---|---:|---:|---:|---|---|',
        ]
        lines += [
            f'| `{r["package"]}` ({r["ecosystem"]}) | {r["severity"].capitalize()} '
            f'| {r["advisories"]} | {r["alerts"]} | {_days(r["oldest_days"])} '
            f'| {_cell("; ".join(GROUP_LABELS[g] for g in r["groups"]))} '
            f'| {_cell(", ".join(f"`{m}`" for m in r["manifests"]))} |'
            for r in rows
        ]
        lines += ['', '</details>']
    else:
        lines.append('Nothing was open at the end of the month.')
    lines += [
        '',
        '<details>',
        '<summary>How these figures are counted</summary>',
        '',
        "- The source is the repository's Dependabot alert history. Periods are calendar months "
        'in UTC, by the date an alert was closed.',
        '- A **vulnerability** is one published advisory (a GHSA id). Dependabot raises an '
        '**alert** for each file the vulnerable package appears in, so one vulnerability can '
        'be several alerts. Both are given.',
        '- An alert counts as **fixed** only if Dependabot closed it as fixed and the same '
        "vulnerability isn't still open on another file of the same project. Dismissed alerts "
        'are never counted as fixes.',
        '- **Where they are** comes from the file each alert is on. '
        + (
            'The project lists which of its files are deployed, and how.'
            if report['groups']['source'] == 'configured'
            else "The project hasn't listed which of its files are deployed, so Dependabot's own "
            'runtime/development label is used, which can be wrong.'
        ),
        '- **Time to fix** runs from when Dependabot raised the alert to when it closed. The '
        'targets are ' + ', '.join(f'{s} {policy[s]} days' for s in deps.SEVERITIES) + '.',
        '- The figures are as they stood at the end of the month, whenever the report is '
        'produced, and the data is embedded below.',
        '',
        '</details>',
        '',
        f'<sub>Reported by {_run_line(run)}.</sub>',
        '',
        embed(report, 'dependency-report.json'),
        '',
        embed({**run, 'outcome': 'report_published'}, 'run.json'),
        '',
    ]
    return _within_limit('\n'.join(lines), 'report body')


def _production_sentence(groups: dict[str, Any], open_vulns: int) -> str:
    """How many of the open vulnerabilities are on production machines, if anything is open."""
    if not open_vulns:
        return ''
    counts = groups['month']
    code = counts['production']['advisories_open_at_end']
    build = counts['production_build']['advisories_open_at_end']
    if groups['source'] != 'configured':
        return f' Dependabot marks {code} of them as in code that runs in production.'
    return (
        f" Of those, {code} {'is' if code == 1 else 'are'} in the site's own code and {build} in "
        'build tools run on the production servers; the rest are in tools that are not deployed.'
    )


def _group_table(groups: dict[str, Any]) -> list[str]:
    month, ytd = groups['month'], groups['year_to_date']
    shown = [g for g in GROUP_LABELS if any(month[g].values()) or ytd[g]['fixed']]
    if not shown:
        return ['Nothing was fixed or open this month.']
    lines = [
        '| | Vulnerabilities fixed this month | Vulnerabilities open at end | Alerts open at end '
        '| Critical alerts open | Vulnerabilities fixed this year |',
        '|---|---:|---:|---:|---:|---:|',
    ]
    lines += [
        f'| {GROUP_LABELS[g]} | {month[g]["advisories_fixed"]} '
        f'| {month[g]["advisories_open_at_end"]} | {month[g]["open_at_end"]} '
        f'| {month[g]["critical_open_at_end"]} | {ytd[g]["advisories_fixed"]} |'
        for g in shown
    ]
    lines += [
        '',
        'A vulnerability found in files of two groups is counted in each.',
    ]
    return lines


def _severity_table(summary: dict[str, Any], policy: dict[str, int]) -> list[str]:
    severities = deps.SEVERITIES

    def row(label: str, values: dict[str, Any], total: Any = None) -> str:
        cells = ' | '.join(str(values[s]) for s in severities)
        total = sum(values.values()) if total is None else total
        return f'| {label} | {cells} | {total} |'

    medians = {s: _days(summary['days_to_fix'][s]['median'], unit=False) for s in severities}
    targets = {s: f'{policy[s]} days' for s in severities}
    met = {
        s: f'{summary["fixed_within_target"][s]} of {summary["fixed"][s]}'
        if summary['fixed'][s]
        else '–'
        for s in severities
    }
    return [
        '| | ' + ' | '.join(s.capitalize() for s in severities) + ' | Total |',
        '|---|' + '---:|' * (len(severities) + 1),
        row('Vulnerabilities fixed', summary['advisories']['fixed']),
        row('Alerts fixed', summary['fixed']),
        row('Target time to fix', targets, ''),
        row('Median days to fix', medians, ''),
        row(
            'Fixes within target',
            met,
            f'{sum(summary["fixed_within_target"].values())} of {sum(summary["fixed"].values())}',
        ),
        row('Vulnerabilities open at end', summary['advisories']['open_at_end']),
        row('Alerts open at end', summary['open_at_end']),
        row('Alerts open at start', summary['open_at_start']),
    ]


def _days(value: float | None, unit: bool = True) -> str:
    """Whole days for a reader: fractions of a day say nothing about how fast a fix came."""
    if value is None:
        return '–'
    days = round(value)
    return f'{days} {"day" if days == 1 else "days"}' if unit else str(days)


def _day(stamp: str) -> str:
    year, month, day = stamp[:10].split('-')
    return f'{int(day)} {MONTH_NAMES[int(month) - 1]} {year}'


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
        # The inline setting each finding's review was posted with, for comparing settings.
        settings = {id(f): r['inline'] or 'unrecorded' for r in reviews for f in r['findings']}
        tables = (
            ('By category', 'Category', lambda f: f['category']),
            ('By severity', 'Severity', lambda f: f['severity']),
            ('By inline setting', 'Setting', lambda f: settings[id(f)]),
        )
        for heading, label, key in tables:
            lines += [
                '',
                f'## {heading}',
                '',
                f'| {label} | Findings | Acted on | Declined | Withdrawn | No sign | Thread resolved |',
                '|---|---:|---:|---:|---:|---:|---:|',
            ]
            values = sorted(
                {key(f) for f in posted},
                key=lambda v: SEVERITY_ORDER.index(v) if label == 'Severity' else v,
            )
            for value in values:
                group = [f for f in posted if key(f) == value]
                tally = Counter(_reaction(f) for f in group)
                resolved = sum(1 for f in group if (f.get('thread') or {}).get('resolved'))
                lines.append(
                    f'| {_cell(value)} | {len(group)} | {tally["acted on"]} | {tally["declined"]} '
                    f'| {tally["withdrawn"]} | {tally["no sign"]} | {resolved} |'
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


def _count(number: int, noun: str, plural: str | None = None) -> str:
    return f'{number} {noun if number == 1 else plural or noun + "s"}'


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
