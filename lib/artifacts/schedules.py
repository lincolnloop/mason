"""Schedules and calendar periods (docs/workflows.md §9).

A schedule says how often a workflow runs on a project, and when in each interval:

    off
    hourly [:MM]
    daily [HH:MM]
    weekly [mon|tue|wed|thu|fri|sat|sun] [HH:MM]
    monthly [day N] [HH:MM]
    quarterly [day N] [HH:MM]
    yearly [day N] [HH:MM]

Each interval maps to calendar periods with stable keys: `2026-11-25T09` (an hour),
`2026-11-25` (a day), `2026-W48` (an ISO week), `2026-11` (a month), `2026-Q4`, `2026`. A
workflow's run for a period is a slot, with a key and a time it is due:

- a **report** covers a period that has ended: the slot for October is due when November's
  anchor comes round (`monthly day 2` → 2 November);
- a **check** runs once in each period: the slot for November is due at November's anchor.

Times are the machine's local time, without a zone: a schedule says "at 06:00", wherever it runs.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

INTERVALS = ('hourly', 'daily', 'weekly', 'monthly', 'quarterly', 'yearly')
KINDS = ('report', 'check')
WEEKDAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
TIME = re.compile(r'^(\d{1,2}):(\d{2})$')
MINUTE = re.compile(r'^:(\d{2})$')
DURATION = re.compile(r'^(\d+)([hd])$')


class ScheduleError(Exception):
    pass


@dataclass(frozen=True)
class Schedule:
    interval: str | None  # None: off
    weekday: int = 0  # weekly: 0 is Monday
    day: int = 1  # monthly, quarterly, yearly: the day of the month (of the first month)
    hour: int = 0
    minute: int = 0

    def __str__(self) -> str:
        if self.interval is None:
            return 'off'
        if self.interval == 'hourly':
            return f'hourly :{self.minute:02}'
        parts = [self.interval]
        if self.interval == 'weekly':
            parts.append(WEEKDAYS[self.weekday])
        elif self.interval in ('monthly', 'quarterly', 'yearly'):
            parts.append(f'day {self.day}')
        parts.append(f'{self.hour:02}:{self.minute:02}')
        return ' '.join(parts)


def parse(text: str) -> Schedule:
    words = text.strip().lower().split()
    if not words:
        raise ScheduleError('a schedule is empty')
    interval, rest = words[0], words[1:]
    if interval == 'off' and not rest:
        return Schedule(None)
    if interval not in INTERVALS:
        raise ScheduleError(f'{text!r}: starts with off or one of {", ".join(INTERVALS)}')
    found: dict[str, int] = {}
    while rest:
        word = rest.pop(0)
        if interval == 'hourly' and (m := MINUTE.match(word)):
            found['minute'] = int(m.group(1))
        elif interval != 'hourly' and (m := TIME.match(word)):
            found['hour'], found['minute'] = int(m.group(1)), int(m.group(2))
        elif interval == 'weekly' and word in WEEKDAYS:
            found['weekday'] = WEEKDAYS.index(word)
        elif interval in ('monthly', 'quarterly', 'yearly') and word == 'day' and rest:
            day = rest.pop(0)
            if not day.isdigit() or not 1 <= int(day) <= 28:
                raise ScheduleError(f'{text!r}: day is 1 to 28, so every month has it')
            found['day'] = int(day)
        else:
            raise ScheduleError(f'{text!r}: {word!r} is not understood (see `mason schedule`)')
    if found.get('hour', 0) > 23 or found.get('minute', 0) > 59:
        raise ScheduleError(f'{text!r}: not a time of day')
    return Schedule(interval, **found)


def duration(text: str) -> timedelta:
    """`12h` or `2d`."""
    match = DURATION.match(text.strip())
    if not match:
        raise ScheduleError(f'{text!r}: a duration is hours or days, e.g. 12h or 2d')
    number, unit = int(match.group(1)), match.group(2)
    return timedelta(hours=number) if unit == 'h' else timedelta(days=number)


# --- Periods -------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Period:
    start: datetime
    interval: str

    @property
    def key(self) -> str:
        s = self.start
        if self.interval == 'hourly':
            return f'{s:%Y-%m-%dT%H}'
        if self.interval == 'daily':
            return f'{s:%Y-%m-%d}'
        if self.interval == 'weekly':
            year, week, _ = s.isocalendar()
            return f'{year}-W{week:02}'
        if self.interval == 'monthly':
            return f'{s:%Y-%m}'
        if self.interval == 'quarterly':
            return f'{s.year}-Q{(s.month - 1) // 3 + 1}'
        return f'{s.year}'

    def next(self) -> 'Period':
        return Period(_add(self.start, self.interval, 1), self.interval)

    def previous(self) -> 'Period':
        return Period(_add(self.start, self.interval, -1), self.interval)

    @property
    def end(self) -> datetime:
        return self.next().start


def _add(start: datetime, interval: str, n: int) -> datetime:
    if interval == 'hourly':
        return start + timedelta(hours=n)
    if interval == 'daily':
        return start + timedelta(days=n)
    if interval == 'weekly':
        return start + timedelta(weeks=n)
    months = {'monthly': 1, 'quarterly': 3, 'yearly': 12}[interval] * n
    index = start.year * 12 + start.month - 1 + months
    return start.replace(year=index // 12, month=index % 12 + 1)


def containing(moment: datetime, interval: str) -> Period:
    if interval == 'hourly':
        start = moment.replace(minute=0, second=0, microsecond=0)
    else:
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        if interval == 'weekly':
            start -= timedelta(days=start.weekday())
        elif interval == 'monthly':
            start = start.replace(day=1)
        elif interval == 'quarterly':
            start = start.replace(day=1, month=(start.month - 1) // 3 * 3 + 1)
        elif interval == 'yearly':
            start = start.replace(day=1, month=1)
    return Period(start, interval)


def from_key(key: str) -> Period:
    """The period a key names."""
    patterns = (
        (r'^(\d{4})-(\d{2})-(\d{2})T(\d{2})$', 'hourly'),
        (r'^(\d{4})-(\d{2})-(\d{2})$', 'daily'),
        (r'^(\d{4})-W(\d{2})$', 'weekly'),
        (r'^(\d{4})-(\d{2})$', 'monthly'),
        (r'^(\d{4})-Q([1-4])$', 'quarterly'),
        (r'^(\d{4})$', 'yearly'),
    )
    for pattern, interval in patterns:
        if m := re.match(pattern, key):
            numbers = [int(g) for g in m.groups()]
            try:
                if interval == 'hourly':
                    start = datetime(*numbers[:3], numbers[3])
                elif interval == 'daily':
                    start = datetime(*numbers)
                elif interval == 'weekly':
                    start = datetime.combine(
                        date.fromisocalendar(numbers[0], numbers[1], 1), datetime.min.time()
                    )
                elif interval == 'monthly':
                    start = datetime(numbers[0], numbers[1], 1)
                elif interval == 'quarterly':
                    start = datetime(numbers[0], (numbers[1] - 1) * 3 + 1, 1)
                else:
                    start = datetime(numbers[0], 1, 1)
            except ValueError as exc:
                raise ScheduleError(f'{key!r}: not a real period ({exc})') from exc
            return Period(start, interval)
    raise ScheduleError(f'{key!r}: not a period key (e.g. 2026-11-25, 2026-W48, 2026-11, 2026-Q4)')


def within(outer: str, interval: str) -> list[str]:
    """The keys of the `interval` periods inside the period `outer`. A week belongs to the month
    (quarter, year) that holds its Thursday, as ISO weeks belong to years."""
    big = from_key(outer)
    found = []
    period = containing(big.start, interval)
    while period.start < big.end + timedelta(days=7):
        anchor = period.start + timedelta(days=3) if interval == 'weekly' else period.start
        if big.start <= anchor < big.end:
            found.append(period.key)
        period = period.next()
    return found


# --- Slots: when each period's run is due ------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    key: str
    due: datetime


def anchor(schedule: Schedule, period: Period) -> datetime:
    """When in `period` the schedule's time comes round."""
    start = period.start
    if schedule.interval == 'hourly':
        return start.replace(minute=schedule.minute)
    if schedule.interval == 'weekly':
        start += timedelta(days=schedule.weekday)
    elif schedule.interval in ('monthly', 'quarterly', 'yearly'):
        days = calendar.monthrange(start.year, start.month)[1]
        start = start.replace(day=min(schedule.day, days))
    return start.replace(hour=schedule.hour, minute=schedule.minute)


def slot(schedule: Schedule, kind: str, period: Period) -> Slot:
    """A report on `period` is due at the next period's anchor; a check at this one's."""
    due = anchor(schedule, period.next() if kind == 'report' else period)
    return Slot(period.key, due)


def slots(schedule: Schedule, kind: str, start: datetime, end: datetime) -> list[Slot]:
    """Every slot due in [start, end), oldest first."""
    if schedule.interval is None:
        return []
    period = containing(start, schedule.interval)
    if kind == 'report':
        period = period.previous()
    found = []
    while True:
        s = slot(schedule, kind, period)
        if s.due >= end:
            return found
        if s.due >= start:
            found.append(s)
        period = period.next()


def next_slot(schedule: Schedule, kind: str, now: datetime) -> Slot | None:
    if schedule.interval is None:
        return None
    period = containing(now, schedule.interval)
    if kind == 'report':
        period = period.previous()
    while (s := slot(schedule, kind, period)).due <= now:
        period = period.next()
    return s


def current_key(interval: str, kind: str, now: datetime) -> str:
    """The period a run started now is for: a check, the period it is in; a report, the period
    that has just ended."""
    period = containing(now, interval)
    return (period.previous() if kind == 'report' else period).key
