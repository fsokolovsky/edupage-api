# `edupage-api` &middot; [![Current version on PyPI](https://img.shields.io/pypi/v/edupage-api)](https://pypi.org/project/edupage-api/) [![Supported Python versions](https://img.shields.io/pypi/pyversions/edupage-api)](https://pypi.org/project/edupage-api/) [![PyPI - Downloads](https://img.shields.io/pypi/dw/edupage-api)](https://pypistats.org/packages/edupage-api) [![CodeFactor](https://www.codefactor.io/repository/github/EdupageAPI/edupage-api/badge)](https://www.codefactor.io/repository/github/EdupageAPI/edupage-api)

This Python library allows easy access to EduPage. It's not a Selenium web scraper. It makes requests directly to EduPage's endpoints and parses the HTML document.

> **This is a fork** of [EdupageAPI/edupage-api](https://github.com/EdupageAPI/edupage-api) maintained at
> [fsokolovsky/edupage-api](https://github.com/fsokolovsky/edupage-api). On top of upstream 0.13.3 it carries
> the upstream pull requests #131 (a parent's children and school accounts), #135 (numeric `udalostid` in grades)
> and #136 (registrations, consultations, standalone grades), plus `TimelineEvent.event_subtype` / `kind`
> for planner events and `get_payments()` for the Payments module (see below). Versions are tagged `0.13.3+fs1`, `0.13.3+fs2`, … and are not published on PyPI;
> install from a tag, e.g. `pip install https://github.com/fsokolovsky/edupage-api/archive/refs/tags/0.13.3+fs1.tar.gz`.

# Installing
__Warning__: Requires Python >= 3.9!

You can install this library using [`pip`](https://pypi.org/project/pip/):

```
pip install edupage-api
```

# Usage

## Login

You can log in easily, it works with any school:

```python
from edupage_api import Edupage
from edupage_api.exceptions import BadCredentialsException, CaptchaException

edupage = Edupage()

try:
    edupage.login("Username", "Password", "Your school's subdomain")
except BadCredentialsException:
    print("Wrong username or password!")
except CaptchaException:
    print("Captcha required!")
```

# Documentation
The docs are available [here](https://edupageapi.github.io/edupage-api/)

# I have a problem or an idea!

- If you find any issue with this code, or it doesn't work please, let us know by opening an [issue](https://github.com/EdupageAPI/edupage-api/issues/new/choose)!
- Feel free to suggest any other features! Just open an [issue with the _Feature Request_ tag](https://github.com/EdupageAPI/edupage-api/issues/new?labels=feature+request&template=feature_request.md&title=%5BFeature+request%5D+).
- If you, even better, have fixed the issue, added a new feature, or made something work better, please, open a [pull request](https://github.com/EdupageAPI/edupage-api/compare)!

# Discord
https://discord.gg/fg6zBu9ZAn

## Planner events: exams, trips, free days

Entries from the school planner (written exams, oral exams, trips, excursions,
holidays, ...) arrive in the timeline with the generic `event_type`
`EventType.EVENT`; EduPage keeps the actual kind in the event's data. The library
exposes it:

```python
from edupage_api.timeline import EventType

for event in edupage.get_notification_history(date_from):
    if event.event_type == EventType.EVENT:
        print(event.event_subtype)  # raw EduPage code, e.g. "bexam", "trip", "freeday"
    if event.kind == EventType.BIG_EXAM:  # event_subtype parsed, falls back to event_type
        print("written exam:", event.text)
```

`event_type` is unchanged for backwards compatibility. `event_subtype` is `None`
for every other event type, and `kind` returns `event_type` when EduPage sends a
code this library does not know yet.

## Payments (read only)

The "Payments" module (Platby): fees the school charges to a student, payments it
received for them and the bank accounts to pay to. A parent selects the child
first; this does not start any payment.

```python
children = edupage.get_children()
edupage.switch_to_child(children[0])

payments = edupage.get_payments()          # current school year
# payments = edupage.get_payments(2025)    # an earlier one (starting year)

print(payments.charged_to_date, payments.paid, payments.balance, payments.currency)

for fee in payments.outstanding_fees:      # Fee: plan_name, due_date, amount, paid, outstanding
    account = payments.account(fee.account_id)
    print(fee.due_date, fee.plan_name, fee.outstanding, account.iban, fee.specific_symbol)

for payment in payments.payments:          # Payment: date, amount, type, viamo_id, fee_allocations
    print(payment.date, payment.amount, payment.type, payment.plan_name)
```

`total_charged` covers the whole school year including future fees, `charged_to_date`
only fees that are already due, and `paid` every payment received (the opening balance
carried over from the previous year is a `Payment` with `is_opening_balance`). The paid
part of a fee comes from EduPage (`Fee.paid`, `Fee.allocations` by payment id); the
amounts listed on a `PaymentPlan` are only a template and are 0 for meal plans. Every
object keeps the original EduPage row in `raw`. Tested on three Slovak schools with
one child each; which child a multi-child parent sees is checked against
`switch_to_child()` and raises when it differs.

## Registrations, surveys and consultations (read only)

The following methods use the authenticated session in its current account/child
context. For a parent account, select the intended child with `switch_to_child`
and restore the parent with `switch_to_parent` in a `finally` block.

```python
# School timezone; change for schools outside this timezone.
actions = edupage.get_registrations(timezone_name="Europe/Prague")
for action in actions:
    print(action.name, action.closes_at, action.votable)
    # Survey questions retain their EduPage JSON content; answers belong only
    # to the current participant. No question JavaScript is executed.
    print(action.targets, action.selection)

history = edupage.get_registration_history()
overview = edupage.get_consultations()
for consultation in overview.consultations:
    slots = edupage.get_consultation_slots(consultation.consultation_id)
    print(consultation.teacher_name, slots)
print(overview.reservations)
```

Assigned actions can include closed registrations. `active`, `votable` and the
opening/closing dates must be considered together. A timeline notification's
completion flag does not establish that a participant answered a survey.
Selection statuses preserve EduPage's values (`ok`, `reserved`, `answered`, or
other values). Parent confirmation and temporary reservation expiry are separate.

Consultation slots preserve server availability rather than inferring it from
the block duration. Their status is `free`, `reserved`, `temporary`, or `unknown`;
`own` and `closed` are separate flags. Unknown states must never be considered
available. Local timestamps are timezone aware; slot epoch timestamps use UTC.
Different consultation blocks may overlap, so preserve the block identifier.

These methods do not reserve, cancel, confirm, or submit answers. They send POST
requests for read operations because that is how EduPage loads these views.
HTTP failures, expired login pages and missing expected data raise exceptions;
they do not silently become empty lists.

Compatibility: HTML/bootstrap and slot shapes were inspected on one parent
account. Tests use synthetic, anonymized examples. Nonempty own reservations,
occupied-slot variants and actual survey question payloads still need live
compatibility testing. Raw survey content is intentionally not interpreted as a
universal questionnaire schema. No credentials or real school data are fixtures.

### Explicit consultation booking

Only the methods below perform writes. `dry_run=True` performs the same fresh
availability/ownership checks but never calls reserve, confirm or cancel.

```python
from datetime import datetime

plan = edupage.book_consultation_slot(
    "3", datetime.fromisoformat("2026-11-10T13:30:00+01:00"), dry_run=True
)
# Only after the user chooses to make this real reservation:
reservation = edupage.book_consultation_slot(
    "3", datetime.fromisoformat("2026-11-10T13:30:00+01:00")
)
# Explicit cancellation; verifies ownership and refuses ambiguous blocks:
edupage.cancel_consultation(reservation.visit_id, dry_run=True)
```

Booking verifies the server slot, creates a temporary hold, confirms its visit ID
and reads back a confirmed own slot. No automatic retry is made after a write:
`ConsultationUncertain` means the user must inspect EduPage before trying again.
The server's cancellation command addresses an entire consultation block, so the
client first verifies that the requested visit is the only own visit in it.
`ConsultationSlotTaken`, `ConsultationClosed`, `ConsultationLimitReached`,
`ConsultationNotOwned` and `ExpiredSessionException` distinguish known failures.
Unrecognized localized server errors remain a generic `ConsultationError` rather
than being guessed. Participant notes are not supported by the inspected write
protocol; passing a nonempty `note` raises an explicit error before any write.

Consultation blocks also expose `teaches_student` from EduPage's `coUci` map
(`None` means unknown), and `is_class_teacher` for client enrichment.

Write behavior is covered by synthetic tests. No real booking or cancellation
was used during development. Real success/error responses still need supervised
validation on a term the user actually intends to book.

### Standalone grades and read completeness

Some schools return marks with udalostid=0, without a paper/event. get_grades()
and term reads retain these marks using the row's predmetid, datum, data and
ucitelid. EduGrade.event_id is None for such marks; grade_id retains znamkaid
when provided. Consumers must not confuse these IDs with timeline event IDs.
Missing standalone weights use 1. Existing event metadata continues to supply
weights for marks attached to papers/events.

The latest successful get_grades() call exposes grade_read_report on Edupage:
received, parsed, skipped and skip_reasons. Copy it before another child/term
read if needed. Skipped rows log field names and reasons without grade values.
Invalid event metadata fails explicitly instead of silently dropping a mark.
