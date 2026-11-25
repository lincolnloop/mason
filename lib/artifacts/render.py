"""Render artifacts as the markdown people read, with the artifacts embedded in it.

Everything a reader sees is derived from the artifacts: tables, counts and figures are computed
here, so they can't disagree with the data. Inputs are assumed valid; the command line validates
before it renders.
"""

from collections import Counter
from typing import Any

from . import deps
from .embed import embed

GITHUB_BODY_LIMIT = 65_536

OPEN = ('new', 'still_open')


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


def _month_name(month: str) -> str:
    year, number = month.split('-')
    return f'{MONTH_NAMES[int(number) - 1]} {year}'


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


def _cell(text: str) -> str:
    return text.replace('|', '\\|').replace('\n', ' ')


def _within_limit(text: str, what: str) -> str:
    if len(text) > GITHUB_BODY_LIMIT:
        raise ValueError(
            f'the {what} is {len(text)} characters; GitHub allows {GITHUB_BODY_LIMIT}. '
            'Shorten the longest bodies or split the work.'
        )
    return text
