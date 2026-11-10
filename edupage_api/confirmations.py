"""Confirmations of messages: which messages ask the reader to confirm that
they read them (or to vote), and who in the family already did.

EduPage marks such messages on the timeline (``data.receipt == "1"`` or
``data.votingParams``). The timeline only carries the confirmation of the
logged-in account (``data.myConfirmations``) and it is unreliable even for
that; the confirmations of everybody else (the other parent, the child) are
only available as reactions to the message, fetched with
``/timeline/?akcia=getRepliesItem``. This module reads both and combines them
into one answer per message: confirmed by me, by somebody else in the family,
or still pending.

What EduPage does and does not expose (as observed on parent accounts):

* every confirmation of a message is listed as a ``confirmation`` reaction
  with the account that made it and the time (``cas_pridania``);
* on a voting message the chosen answer of other people is not exposed, only
  that they reacted;
* a reaction that only carries a ``like`` counts as a read receipt (EduPage
  counts it in the ``receipt`` total) but not as a vote (it is missing from
  the ``vote`` totals).

Read only: this module does not confirm or vote.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from edupage_api.exceptions import MissingDataException, RequestError
from edupage_api.module import Module, ModuleHelper
from edupage_api.timeline import EventType, TimelineEvent, TimelineEvents
from edupage_api.utils import RequestUtil

_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_datetime(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    return ModuleHelper.strptime_or_none(value, _DATETIME_FORMAT)


def _parse_data(value) -> dict:
    """Reaction ``data`` arrives as a dict, a JSON string or an empty list."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _parent_account_id(parent_id) -> str | None:
    """EduPage parent ids are negative numbers ("-1234"); the account is "Rodic-1234"."""
    if parent_id in (None, ""):
        return None
    parent_id = str(parent_id)
    return parent_id if parent_id.startswith("Rodic") else f"Rodic{parent_id}"


@dataclass
class VotingOption:
    option_id: int
    text: str


@dataclass
class MessageParticipant:
    """A recipient of a message as EduPage lists them with the reactions.

    Only some of them are sent with details, the rest are id-only stubs."""

    account_id: str  # e.g. "Rodic-1234", "Student5678", "Ucitel90"
    account_type: str | None  # EduPage's "Rodic", "Student", "Ucitel", "Admin"...
    first_name: str | None
    last_name: str | None
    children: list[int]  # student ids, for parents
    raw: dict = field(repr=False)

    @staticmethod
    def parse(account_id: str, data: dict) -> MessageParticipant:
        children = []
        for child in data.get("children") or []:
            child_id = ModuleHelper.parse_int(child)
            if child_id is not None:
                children.append(child_id)
        return MessageParticipant(
            str(account_id),
            data.get("typ") or None,
            data.get("firstname") or None,
            data.get("lastname") or None,
            children,
            data,
        )


@dataclass
class MessageConfirmation:
    """One account's confirmation (or like) of a message."""

    event_id: int
    # The message copy this reaction belongs to. EduPage sends a message to
    # several groups as separate copies, so this can differ from the id of the
    # copy you asked about.
    message_id: int | None
    account_id: str  # the account the confirmation belongs to (``vlastnik``)
    account_name: str | None
    # The account that made it (``user``), e.g. a parent confirming while
    # viewing the child's account; usually the same as `account_id`.
    actor_id: str | None
    confirmed_at: datetime
    receipt_at: datetime | None  # only present on your own confirmation
    liked_at: datetime | None
    raw: dict = field(repr=False)

    @property
    def is_like_only(self) -> bool:
        """The reaction carries a like and nothing else. On a voting message
        this is not a vote."""
        return self.liked_at is not None and self.receipt_at is None and not any(
            key.startswith("vote") for key in self.raw
        )

    def is_by(self, account_ids: Iterable[str]) -> bool:
        ids = set(account_ids)
        return self.account_id in ids or (self.actor_id is not None and self.actor_id in ids)

    @staticmethod
    def parse(reaction: dict, viewer_id: str | None = None) -> MessageConfirmation | None:
        event_id = ModuleHelper.parse_int(reaction.get("timelineid"))
        if event_id is None:
            return None
        data = _parse_data(reaction.get("data"))
        account_id = reaction.get("vlastnik") or reaction.get("user")
        if not account_id:
            return None
        confirmed_at = _parse_datetime(reaction.get("cas_pridania")) or _parse_datetime(
            reaction.get("timestamp")
        )
        if confirmed_at is None:
            return None
        # ``myConfirmations`` inside other people's reactions is the viewer's
        # own confirmation; ``receipt`` is only sent for the viewer's own.
        receipt_at = _parse_datetime(data.get("receipt"))
        if receipt_at is None and viewer_id is not None and account_id == viewer_id:
            my = data.get("myConfirmations")
            receipt_at = _parse_datetime(my.get("receipt")) if isinstance(my, dict) else None
        own_data = {k: v for k, v in data.items() if k != "myConfirmations"}
        return MessageConfirmation(
            event_id,
            ModuleHelper.parse_int(reaction.get("reakcia_na")),
            str(account_id),
            reaction.get("vlastnik_meno") or reaction.get("user_meno") or None,
            reaction.get("user") or None,
            confirmed_at,
            receipt_at,
            _parse_datetime(data.get("like")),
            own_data,
        )


@dataclass
class MessageConfirmations:
    """All confirmations of one message, as `get_message_confirmations()` returns them."""

    message_id: int
    confirmations: list[MessageConfirmation]  # oldest first
    participants: dict[str, MessageParticipant]  # by account id
    participant_count: int | None
    raw: dict = field(repr=False)

    def by(self, account_ids: Iterable[str]) -> list[MessageConfirmation]:
        ids = list(account_ids)
        return [c for c in self.confirmations if c.is_by(ids)]

    def parents_of(self, student_id: int) -> list[str]:
        """Account ids of the parents of a student, as far as the participant
        list names them."""
        return [
            p.account_id
            for p in self.participants.values()
            if student_id in p.children and p.account_id.startswith("Rodic")
        ]

    @staticmethod
    def parse(message_id: int, data: dict, viewer_id: str | None = None) -> MessageConfirmations:
        confirmations = []
        for reaction in data.get("reakcie") or []:
            if not isinstance(reaction, dict) or reaction.get("typ") != "confirmation":
                continue
            if str(reaction.get("removed") or "0") not in ("0", "False", "false"):
                continue
            confirmation = MessageConfirmation.parse(reaction, viewer_id)
            if confirmation is not None:
                confirmations.append(confirmation)
        confirmations.sort(key=lambda c: (c.confirmed_at, c.event_id))

        participants: dict[str, MessageParticipant] = {}
        raw_participants = data.get("participants")
        if isinstance(raw_participants, dict):
            entries = raw_participants.items()
        elif isinstance(raw_participants, list):
            entries = ((p.get("id"), p) for p in raw_participants if isinstance(p, dict))
        else:
            entries = ()
        for account_id, entry in entries:
            if account_id and isinstance(entry, dict):
                participants[str(account_id)] = MessageParticipant.parse(str(account_id), entry)

        return MessageConfirmations(
            message_id,
            confirmations,
            participants,
            ModuleHelper.parse_int(data.get("numParticipants")),
            data,
        )


@dataclass
class MessageStatus:
    """A message that asks for a read receipt or a vote, and who dealt with it."""

    event: TimelineEvent
    requires_receipt: bool
    voting_options: list[VotingOption]
    voting_multiple: bool
    counts: dict[str, int]  # EduPage's totals, e.g. {"receipt": 42, "like": 3, "vote2": 7}
    my_confirmations: dict[str, datetime]  # the logged-in account's own, by kind
    # Accounts counted as "the family": the logged-in account, the selected
    # child and the child's parents (from the login data).
    family_ids: list[str]
    # Confirmations by the family found in the message's reactions;
    # None when the reactions were not fetched (see `reactions_error`).
    family_confirmations: list[MessageConfirmation] | None = None
    # Why the reactions could not be fetched, when they were asked for.
    reactions_error: str | None = None

    @property
    def reactions_fetched(self) -> bool:
        return self.family_confirmations is not None

    @property
    def message_id(self) -> int:
        return self.event.event_id

    @property
    def is_voting(self) -> bool:
        return bool(self.voting_options)

    @property
    def my_receipt_at(self) -> datetime | None:
        return self.my_confirmations.get("receipt")

    @property
    def confirmed_by_me(self) -> bool:
        return any(kind != "like" for kind in self.my_confirmations)

    @property
    def confirmed_by(self) -> list[str]:
        """Account ids in the family that confirmed (or voted), oldest first."""
        ids: list[str] = []
        for confirmation in self.family_confirmations or []:
            if self.is_voting and confirmation.is_like_only:
                continue
            if confirmation.account_id not in ids:
                ids.append(confirmation.account_id)
        me = self.family_ids[0] if self.family_ids else None
        if self.confirmed_by_me and me is not None and me not in ids:
            ids.append(me)
        return ids

    @property
    def confirmed_at(self) -> datetime | None:
        """When the first confirmation in the family was made."""
        times = [c.confirmed_at for c in self.family_confirmations or []
                 if not (self.is_voting and c.is_like_only)]
        times += [t for kind, t in self.my_confirmations.items() if kind != "like"]
        return min(times) if times else None

    @property
    def is_confirmed(self) -> bool:
        return bool(self.confirmed_by)

    @property
    def is_pending(self) -> bool:
        """Nobody known confirmed it. When the reactions could not be fetched
        (`reactions_error`) this only reflects your own confirmation."""
        return not self.is_confirmed

    @staticmethod
    def from_event(event: TimelineEvent, family_ids: list[str]) -> MessageStatus | None:
        """The status from the timeline item alone; None when the message
        asks for nothing."""
        data = event.additional_data if isinstance(event.additional_data, dict) else {}
        requires_receipt = str(data.get("receipt")) in ("1", "True", "true")
        voting: dict = data.get("votingParams") if isinstance(data.get("votingParams"), dict) else {}
        options = []
        for answer in voting.get("answers") or []:
            if not isinstance(answer, dict):
                continue
            option_id = ModuleHelper.parse_int(answer.get("id"))
            if option_id is not None:
                options.append(VotingOption(option_id, str(answer.get("text") or "")))
        if not requires_receipt and not options:
            return None

        counts = {}
        for kind, value in (data.get("confirmations") or {}).items():
            count = ModuleHelper.parse_int(value)
            if count is not None:
                counts[str(kind)] = count
        my_confirmations = {}
        for kind, value in (data.get("myConfirmations") or {}).items():
            when = _parse_datetime(value)
            if when is not None:
                my_confirmations[str(kind)] = when

        return MessageStatus(
            event,
            requires_receipt,
            options,
            bool(voting.get("multiple")),
            counts,
            my_confirmations,
            list(family_ids),
        )


@dataclass
class ConfirmationsOverview:
    date_from: date
    family_ids: list[str]
    messages: list[MessageStatus]  # only messages asking for a receipt or a vote, oldest first

    @property
    def pending(self) -> list[MessageStatus]:
        """Messages nobody in the family confirmed, as far as is known."""
        return [m for m in self.messages if m.is_pending and m.reactions_error is None]

    @property
    def confirmed(self) -> list[MessageStatus]:
        return [m for m in self.messages if m.is_confirmed]

    @property
    def unknown(self) -> list[MessageStatus]:
        """Messages you did not confirm whose reactions could not be fetched,
        so the rest of the family is unknown."""
        return [m for m in self.messages if m.is_pending and m.reactions_error is not None]


class Confirmations(Module):
    _REQUESTING_TYPES = (EventType.MESSAGE, EventType.NEWS, EventType.POLL)

    def family_ids(self) -> list[str]:
        """The logged-in account first, then the selected child (or all the
        children) and their parents, as the login data lists them."""
        data = self.edupage.data if isinstance(self.edupage.data, dict) else {}
        ids: list[str] = []

        def add(account_id):
            if account_id and account_id not in ids:
                ids.append(str(account_id))

        add(data.get("userid"))
        selected = getattr(self.edupage, "_selected_child_id", None)
        children = [selected] if selected is not None else list(data.get("parentStudentids") or [])
        dbi: dict = data.get("dbi") if isinstance(data.get("dbi"), dict) else {}
        students: dict = dbi.get("students") if isinstance(dbi.get("students"), dict) else {}
        for child in children:
            child_id = ModuleHelper.parse_int(child)
            if child_id is None:
                continue
            add(f"Student{child_id}")
            student = students.get(str(child_id))
            if isinstance(student, dict):
                for key in ("parent1id", "parent2id", "parent3id"):
                    add(_parent_account_id(student.get(key)))
        return ids

    @ModuleHelper.logged_in
    def get_message_confirmations(self, message: TimelineEvent | int) -> MessageConfirmations:
        message_id = message.event_id if isinstance(message, TimelineEvent) else int(message)
        request_url = f"https://{self.edupage.subdomain}.edupage.org/timeline/"
        response = self.edupage.session.post(
            request_url,
            params=[("akcia", "getRepliesItem")],
            data=RequestUtil.encode_form_data({"groupid": str(message_id), "lastsync": ""}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise RequestError(f"Edupage returned an error: status={response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise RequestError("Edupage returned a non-JSON response for the message's reactions")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("status") != "ok" or not isinstance(data, dict):
            raise MissingDataException(
                f"Edupage returned no reactions for message {message_id} (not a message of yours?)"
            )
        viewer_id = self.edupage.data.get("userid") if isinstance(self.edupage.data, dict) else None
        return MessageConfirmations.parse(message_id, data, viewer_id)

    @ModuleHelper.logged_in
    def get_confirmations(self, date_from: date, fetch_reactions: bool = True) -> ConfirmationsOverview:
        try:
            events = TimelineEvents(self.edupage).get_notifications_history(date_from)
        except MissingDataException:
            events = []
        family = self.family_ids()
        messages = self.from_events(events, family)
        if fetch_reactions:
            for status in messages:
                try:
                    reactions = self.get_message_confirmations(status.message_id)
                except (MissingDataException, RequestError, ValueError) as error:
                    status.reactions_error = str(error)
                    continue
                status.family_confirmations = reactions.by(family)
        return ConfirmationsOverview(date_from, family, messages)

    @classmethod
    def from_events(cls, events: list[TimelineEvent], family_ids: list[str]) -> list[MessageStatus]:
        messages = []
        for event in events:
            if event.is_removed or event.response_to is not None:
                continue
            if event.event_type not in cls._REQUESTING_TYPES:
                continue
            status = MessageStatus.from_event(event, family_ids)
            if status is not None:
                messages.append(status)
        return sorted(messages, key=lambda m: (m.event.timestamp, m.message_id))
