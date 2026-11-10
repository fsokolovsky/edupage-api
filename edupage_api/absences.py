"""Absences of a student and the excuse notes (ospravedlnenky) a parent sends
for them, as EduPage reports them on the timeline.

EduPage posts three kinds of timeline items:

* ``student_absent`` - a teacher recorded the student as absent in some
  lessons of a day (`Absence`);
* ``ospravedlnenka`` - an excuse note: sent by a parent and accepted or
  rejected by the class teacher, or entered by the teacher directly
  (`AbsenceExcuse`; when EduPage posts a note more than once the latest item
  wins);
* ``ospravedlnenka_reminder`` - EduPage reminds the parent that some days are
  still not excused (`AbsenceReminder`).

Read only: this module does not send or accept excuse notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from edupage_api.exceptions import MissingDataException
from edupage_api.module import Module, ModuleHelper
from edupage_api.people import EduAccount
from edupage_api.timeline import EventType, TimelineEvent, TimelineEvents


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _name(account) -> str | None:
    if isinstance(account, EduAccount):
        return account.name
    if isinstance(account, str) and account:
        return account
    return None


@dataclass
class Absence:
    """One day on which a teacher recorded the student as absent."""

    event_id: int
    date: date
    # lesson period -> EduPage absence code, e.g. {"1": "A", "2": "A-L"};
    # "A" is absent, suffixes such as "-L" or "-P" are EduPage's own
    # qualifiers (late arrival etc.) and are passed through unchanged
    periods: dict[str, str]
    is_excused: bool
    recorded_by: str | None  # name of the teacher who recorded it
    recorded_at: datetime
    raw: dict = field(repr=False)

    @staticmethod
    def from_event(event: TimelineEvent) -> Absence | None:
        data = event.additional_data if isinstance(event.additional_data, dict) else {}
        day = _parse_date(data.get("cas_udalosti")) or (
            event.event_time.date() if event.event_time else None
        )
        if day is None:
            return None
        periods = {
            key[2:]: str(value)
            for key, value in data.items()
            if key.startswith("p_") and key != "p_" and value not in (None, "")
        }
        return Absence(
            event.event_id,
            day,
            periods,
            bool(data.get("isExcused")),
            _name(event.author),
            event.timestamp,
            data,
        )


class ExcuseStatus(str, Enum):
    PENDING = "pending"  # sent by a parent, the class teacher has not decided yet
    ACCEPTED = "accepted"  # sent by a parent and accepted by the class teacher
    REJECTED = "rejected"
    # Entered by the class teacher directly (e.g. from a paper note), so
    # there is nothing to accept; EduPage sends these without the
    # "sa_parent" type and without a decision.
    RECORDED = "recorded"


@dataclass
class AbsenceExcuse:
    """An excuse note for an absence (ospravedlnenka)."""

    excuse_id: str  # EduPage's id of the note (hex string)
    student_id: int | None
    date_from: date | None
    date_to: date | None
    note: str | None  # the reason the parent wrote
    status: ExcuseStatus
    sent_by_parent: bool  # EduPage type "sa_parent"
    reason_type_id: str | None  # EduPage absence reason id (``studentabsent_typeid``)
    teacher: str | None  # the class teacher the note went to
    created_at: datetime  # when the note was first posted
    updated_at: datetime  # when its latest state was posted
    event_ids: list[int]  # all timeline items about this note, oldest first
    raw: dict = field(repr=False)  # data of the latest item

    @staticmethod
    def from_events(events: list[TimelineEvent]) -> AbsenceExcuse | None:
        """Build the note from all its timeline items (any order)."""
        events = sorted(events, key=lambda e: (e.timestamp, e.event_id))
        if not events:
            return None
        latest = events[-1]
        data = latest.additional_data if isinstance(latest.additional_data, dict) else {}
        excuse_id = data.get("id") or latest.other_id
        if excuse_id is None:
            return None

        sent_by_parent = any(
            isinstance(e.additional_data, dict) and e.additional_data.get("type") == "sa_parent"
            for e in events
        )
        if data.get("zamietnute") in (True, 1, "1"):
            status = ExcuseStatus.REJECTED
        elif data.get("akceptovane") in (True, 1, "1"):
            status = ExcuseStatus.ACCEPTED
        elif sent_by_parent:
            status = ExcuseStatus.PENDING
        else:
            status = ExcuseStatus.RECORDED
        note = next(
            (
                e.additional_data.get("note")
                for e in reversed(events)
                if isinstance(e.additional_data, dict) and e.additional_data.get("note")
            ),
            None,
        )
        teacher = next((_name(e.author) for e in reversed(events) if _name(e.author)), None)

        return AbsenceExcuse(
            str(excuse_id),
            ModuleHelper.parse_int(data.get("studentid")),
            _parse_date(data.get("datefrom")),
            _parse_date(data.get("dateto")),
            note,
            status,
            sent_by_parent,
            None if data.get("studentabsent_typeid") is None else str(data.get("studentabsent_typeid")),
            teacher,
            events[0].timestamp,
            latest.timestamp,
            [e.event_id for e in events],
            data,
        )

    @property
    def is_resolved(self) -> bool:
        """Accepted, or entered by the teacher; nothing left for the parent."""
        return self.status in (ExcuseStatus.ACCEPTED, ExcuseStatus.RECORDED)

    def covers(self, day: date) -> bool:
        if self.date_from is None:
            return False
        return self.date_from <= day <= (self.date_to or self.date_from)


@dataclass
class AbsenceReminder:
    """EduPage's reminder that absences on some days are not excused yet."""

    event_id: int
    dates: list[date]
    student_id: int | None
    sent_at: datetime
    raw: dict = field(repr=False)

    @staticmethod
    def from_event(event: TimelineEvent) -> AbsenceReminder | None:
        data = event.additional_data if isinstance(event.additional_data, dict) else {}
        dates = [d for d in (_parse_date(v) for v in data.get("dates") or []) if d is not None]
        return AbsenceReminder(
            event.event_id,
            dates,
            ModuleHelper.parse_int(data.get("studentid")),
            event.timestamp,
            data,
        )


@dataclass
class AbsencesOverview:
    date_from: date
    absences: list[Absence]  # oldest first
    excuses: list[AbsenceExcuse]  # oldest first
    reminders: list[AbsenceReminder]

    @property
    def unexcused_absences(self) -> list[Absence]:
        """Days a teacher recorded as absent that are neither excused nor
        covered by an accepted or pending excuse note."""
        covered = [e for e in self.excuses if e.status != ExcuseStatus.REJECTED]
        return [
            a
            for a in self.absences
            if not a.is_excused and not any(e.covers(a.date) for e in covered)
        ]

    @property
    def pending_excuses(self) -> list[AbsenceExcuse]:
        return [e for e in self.excuses if e.status == ExcuseStatus.PENDING]

    def excuses_for(self, day: date) -> list[AbsenceExcuse]:
        return [e for e in self.excuses if e.covers(day)]


class Absences(Module):
    @ModuleHelper.logged_in
    def get_absences(self, date_from: date) -> AbsencesOverview:
        try:
            events = TimelineEvents(self.edupage).get_notifications_history(date_from)
        except MissingDataException:
            events = []
        return self.from_events(events, date_from)

    @staticmethod
    def from_events(events: list[TimelineEvent], date_from: date) -> AbsencesOverview:
        absences: list[Absence] = []
        reminders: list[AbsenceReminder] = []
        by_excuse: dict[str, list[TimelineEvent]] = {}

        for event in events:
            if event.is_removed:
                continue
            if event.event_type == EventType.STUDENT_ABSENT:
                absence = Absence.from_event(event)
                if absence is not None:
                    absences.append(absence)
            elif event.event_type == EventType.EXCUSED_LESSON:
                data = event.additional_data if isinstance(event.additional_data, dict) else {}
                key = data.get("id") or event.other_id
                if key is not None:
                    by_excuse.setdefault(str(key), []).append(event)
            elif event.event_type == EventType.EXCUSED_LESSON_REMINDER:
                reminder = AbsenceReminder.from_event(event)
                if reminder is not None:
                    reminders.append(reminder)

        # A day can be posted more than once (e.g. when a teacher adds lessons);
        # keep the latest item per day.
        latest_absence: dict[date, Absence] = {}
        for absence in sorted(absences, key=lambda a: (a.recorded_at, a.event_id)):
            latest_absence[absence.date] = absence

        excuses = [
            excuse
            for excuse in (AbsenceExcuse.from_events(items) for items in by_excuse.values())
            if excuse is not None
        ]

        return AbsencesOverview(
            date_from,
            sorted(latest_absence.values(), key=lambda a: a.date),
            sorted(excuses, key=lambda e: (e.date_from or date.min, e.created_at)),
            sorted(reminders, key=lambda r: r.sent_at),
        )
