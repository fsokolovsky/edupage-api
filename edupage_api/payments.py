"""The EduPage "Payments" module (Platby): fees charged to a student, payments
the school received for them and the school's bank accounts.

EduPage renders the payments page (``/platby/``) with all of the data embedded
as JSON; this module reads that page. Everything here is read only: it never
starts a payment (VIAMO, card...) and never changes anything on EduPage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from edupage_api.exceptions import FailedToParsePaymentsDataError, RequestError
from edupage_api.module import Module, ModuleHelper

_PERIOD_MONTH = re.compile(r"^asc_m(\d{1,2})$")


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _amount(value) -> float | None:
    """EduPage sends money as JSON numbers with float noise (137.23999999999998)."""
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _str(value) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


@dataclass
class PaymentAccount:
    """A bank account of the school that fees are paid to (``ucty_skoly``)."""

    account_id: int
    name: str | None  # e.g. "Školská jedáleň"
    iban: str | None
    swift: str | None
    bank_code: str | None
    account_number: str | None
    account_prefix: str | None
    holder: str | None  # the account's registered owner (``acc_name``)
    raw: dict = field(repr=False)

    @staticmethod
    def parse(data: dict) -> PaymentAccount | None:
        account_id = ModuleHelper.parse_int(data.get("ucet_id"))
        if account_id is None:
            return None
        return PaymentAccount(
            account_id,
            _str(data.get("nazov")),
            _str(data.get("iban")),
            _str(data.get("swift")),
            _str(data.get("kod_banky")),
            _str(data.get("cislo_uctu")),
            _str(data.get("predcislie_uctu")),
            _str(data.get("acc_name")),
            data,
        )


@dataclass
class PaymentPlan:
    """A fee prescription (``predpis``): what the school charges and why, e.g.
    "Školská jedáleň" or "Exkurzia Aurélium". The student's actual fees are
    the `Fee` objects; the amounts listed on a plan are only a template
    (0 for meal plans)."""

    plan_id: int
    name: str
    account_id: int | None
    specific_symbol: str | None
    last_due_date: date | None
    created_by: str | None  # EduPage user id of the creator, e.g. "Ucitel572149" or "Admin"
    owner_name: str | None  # name of the teacher who owns a private plan
    is_meals: bool  # created by the meals module (school canteen)
    note: str | None
    visible: bool
    raw: dict = field(repr=False)

    @staticmethod
    def parse(data: dict) -> PaymentPlan | None:
        plan_id = ModuleHelper.parse_int(data.get("predpis_id"))
        if plan_id is None:
            return None
        return PaymentPlan(
            plan_id,
            str(data.get("predpis_meno") or ""),
            ModuleHelper.parse_int(data.get("ucet_id")),
            _str(data.get("ss")),
            _parse_date(data.get("lastSplatnost")),
            _str(data.get("zadavatel_id") or data.get("sukromny_owner")),
            _str(data.get("sukromny_owner_fullName")),
            bool(data.get("stravaData")),
            _str(data.get("system_note")),
            data.get("visible") in (1, "1", True),
            data,
        )


@dataclass
class Fee:
    """One fee charged to the student (``poplatok``), e.g. the lunches for
    October or the ticket for a school trip."""

    fee_id: int
    plan_id: int | None
    plan_name: str
    due_date: date | None
    amount: float
    paid: float
    period: str | None  # EduPage period code, e.g. "asc_m9" (October), "" when none
    account_id: int | None
    specific_symbol: str | None
    note: str | None  # e.g. "strava 2026-10"
    visible: bool
    # payment_id -> amount of that payment used for this fee
    allocations: dict[int, float]
    raw: dict = field(repr=False)

    @property
    def outstanding(self) -> float:
        return round(max(self.amount - self.paid, 0.0), 2)

    @property
    def is_paid(self) -> bool:
        return self.paid + 0.005 >= self.amount

    def is_overdue(self, on: date | None = None) -> bool:
        on = on or date.today()
        return not self.is_paid and self.due_date is not None and self.due_date < on

    @property
    def period_month(self) -> int | None:
        """The month (1-12) a monthly fee is for, parsed from `period`
        ("asc_m0" is January). None for other period codes."""
        match = _PERIOD_MONTH.match(self.period or "")
        if not match:
            return None
        month = int(match.group(1)) + 1
        return month if 1 <= month <= 12 else None

    @staticmethod
    def parse(data: dict) -> Fee | None:
        fee_id = ModuleHelper.parse_int(data.get("poplatok_id"))
        if fee_id is None:
            return None

        allocations: dict[int, float] = {}
        for key, value in (data.get("platbaPriradenieSuma") or {}).items():
            # "<platba_id>#<priradenie_id>": amount
            payment_id = ModuleHelper.parse_int(str(key).split("#", 1)[0])
            amount = _amount(value)
            if payment_id is not None and amount is not None:
                allocations[payment_id] = allocations.get(payment_id, 0.0) + amount

        return Fee(
            fee_id,
            ModuleHelper.parse_int(data.get("predpis_id")),
            str(data.get("predpis_meno") or ""),
            _parse_date(data.get("datum_splatnosti")),
            _amount(data.get("suma")) or 0.0,
            _amount(data.get("zaplatene")) or 0.0,
            data.get("obdobie") if isinstance(data.get("obdobie"), str) else None,
            ModuleHelper.parse_int(data.get("ucet_id")),
            _str(data.get("ss")),
            _str(data.get("system_note")),
            data.get("visible") in (1, "1", True),
            allocations,
            data,
        )


@dataclass
class Payment:
    """A payment the school received from the student's payer (``platba``).
    The opening balance carried over from the previous school year is also a
    payment (`is_opening_balance`), with a negative `amount` for a debt."""

    payment_id: int
    amount: float
    date: date | None  # when it was paid (``datum_zaplatenia``)
    credited_date: date | None  # when the school's bank credited it
    registered_at: datetime | None  # when EduPage recorded it
    type: str | None  # EduPage payment type: "viamoPB", "hotovost", "bankovy prevod", "pociatocny_stav"...
    method: str | None  # payment gateway method, e.g. "BANK"; None for manual records
    variable_symbol: str | None
    specific_symbol: str | None
    constant_symbol: str | None
    message: str | None  # message for the recipient
    note: str | None
    viamo_id: str | None  # VIAMO transaction id (``viamo_rid``)
    account_id: int | None
    plan_id: int | None  # the plan this payment was assigned to
    plan_name: str | None
    allocated_amount: float | None  # how much of it was assigned to the plan
    # fee_id -> amount of this payment used for that fee
    fee_allocations: dict[int, float]
    state: str | None  # EduPage state code (``stav``), "o" for ordinary records
    raw: dict = field(repr=False)

    @property
    def is_opening_balance(self) -> bool:
        return self.type == "pociatocny_stav"

    @staticmethod
    def parse(data: dict) -> Payment | None:
        payment_id = ModuleHelper.parse_int(data.get("platba_id"))
        if payment_id is None:
            return None

        fee_allocations: dict[int, float] = {}
        for key, value in (data.get("zaplatilPoplatkyIDS") or {}).items():
            # "<poplatok_id>&<n>": amount
            fee_id = ModuleHelper.parse_int(str(key).split("&", 1)[0])
            amount = _amount(value)
            if fee_id is not None and amount is not None:
                fee_allocations[fee_id] = fee_allocations.get(fee_id, 0.0) + amount

        return Payment(
            payment_id,
            _amount(data.get("suma")) or 0.0,
            _parse_date(data.get("datum_zaplatenia")),
            _parse_date(data.get("datum_pripisania")),
            _parse_datetime(data.get("datum_zaevidovania")),
            _str(data.get("typ_platby")),
            _str(data.get("paymethod")),
            _str(data.get("variabilny_symbol")),
            _str(data.get("specificky_symbol")),
            _str(data.get("konstantny_symbol")),
            _str(data.get("sprava_pre_prijemcu")),
            _str(data.get("poznamka")),
            _str(data.get("viamo_rid")),
            ModuleHelper.parse_int(data.get("ucet_id")),
            ModuleHelper.parse_int(data.get("predpis_id")),
            _str(data.get("predpis_meno")),
            _amount(data.get("priradenie_suma")),
            fee_allocations,
            _str(data.get("stav")),
            data,
        )


@dataclass
class PaymentsOverview:
    """Everything the payments page shows for one student in one school year."""

    user: str  # EduPage user id the data belongs to, e.g. "Student123456"
    school_year: int  # starting year, e.g. 2026 for 2026/2027
    currency: str | None  # e.g. "eur"
    total_charged: float  # all fees of the school year, including future ones
    charged_to_date: float  # fees that are already due
    paid: float  # all payments received (including the opening balance)
    fees: list[Fee]
    payments: list[Payment]
    plans: list[PaymentPlan]
    accounts: list[PaymentAccount]
    problems: list  # EduPage's own warnings about the payer (``userProblems``), raw
    raw: dict = field(repr=False)

    @property
    def balance(self) -> float:
        """`paid` minus `charged_to_date`: negative means the student owes
        money now, positive means credit (which may already be assigned to
        future fees - see `outstanding_fees`)."""
        return round(self.paid - self.charged_to_date, 2)

    @property
    def outstanding_fees(self) -> list[Fee]:
        return [fee for fee in self.fees if not fee.is_paid]

    def overdue_fees(self, on: date | None = None) -> list[Fee]:
        return [fee for fee in self.fees if fee.is_overdue(on)]

    def plan(self, plan_id: int) -> PaymentPlan | None:
        return next((plan for plan in self.plans if plan.plan_id == plan_id), None)

    def account(self, account_id: int) -> PaymentAccount | None:
        return next((acc for acc in self.accounts if acc.account_id == account_id), None)


class Payments(Module):
    _INITIAL_DATA = re.compile(r"var\s+initial_data\s*=\s*(?=\{)")
    _STUDENT_DATA = re.compile(r"var\s+veci_udaje\s*=\s*(?=\{)")

    def _page(self) -> tuple[dict, dict]:
        """Fetch /platby/ and return (initial_data, veci_udaje) embedded in it."""
        url = f"https://{self.edupage.subdomain}.edupage.org/platby/"
        response = self.edupage.session.get(url)
        if response.status_code != 200:
            raise RequestError(
                f"Edupage returned an error: status={response.status_code}"
            )

        html = response.text
        return self._embedded(html, self._INITIAL_DATA, "initial_data"), self._embedded(
            html, self._STUDENT_DATA, "veci_udaje"
        )

    @staticmethod
    def _embedded(html: str, pattern: re.Pattern, name: str) -> dict:
        match = pattern.search(html)
        if match is None:
            raise FailedToParsePaymentsDataError(
                f"The payments page has no {name} (payments module disabled or EduPage changed?)"
            )
        try:
            data, _ = json.JSONDecoder().raw_decode(html, match.end())
        except ValueError as exc:
            raise FailedToParsePaymentsDataError(f"Invalid {name} JSON: {exc}") from None
        if not isinstance(data, dict):
            raise FailedToParsePaymentsDataError(f"{name} is not an object")
        return data

    def _fetch_year(self, user: str, school_year: int) -> dict:
        """Ask the server for another school year (what the page's year switcher does)."""
        url = f"https://{self.edupage.subdomain}.edupage.org/platby/"
        veci = {
            "platbyAPoplatkyVybranychZiakov": {"ziaci": {user: user}},
            "predpisyRows": {},
            "userProblems": {"user": user},
            "menuPredpisyIDS": {},
            "useSkupiny": {},
            "skupinyIDS": {},
            "walletsIDS": {},
        }
        response = self.edupage.session.post(
            url,
            data={
                "akcia": "getUdaje",
                "veci": json.dumps(veci),
                "schoolYear": str(school_year),
                "ziaci": json.dumps({"0": user}),
                "vsetciZiaci": "0",
                "iba_neplaticov": "0",
            },
        )
        if response.status_code != 200:
            raise RequestError(
                f"Edupage returned an error: status={response.status_code}"
            )
        try:
            data = response.json()
        except ValueError:
            raise FailedToParsePaymentsDataError(
                "Unexpected (non-JSON) response to getUdaje"
            ) from None
        if not isinstance(data, dict):
            raise FailedToParsePaymentsDataError("getUdaje response is not an object")
        return data

    @ModuleHelper.logged_in
    def get_payments(self, school_year: int | None = None) -> PaymentsOverview:
        initial_data, student_data = self._page()

        settings = (initial_data.get("setPlatbyModuleSettingsUdaje") or {}).get(
            "platbyModuleSettings"
        ) or {}
        current_year = ModuleHelper.parse_int(settings.get("schoolYear"))
        if current_year is None:
            raise FailedToParsePaymentsDataError("Missing schoolYear in payments settings")

        accounts = [
            account
            for account in (
                PaymentAccount.parse(a) for a in settings.get("ucty_skoly") or [] if isinstance(a, dict)
            )
            if account is not None
        ]

        if school_year is not None and school_year != current_year:
            user = self._user(student_data)
            student_data = self._fetch_year(user, school_year)
        else:
            school_year = current_year

        error = student_data.get("error")
        if error:
            raise FailedToParsePaymentsDataError(f"EduPage reported an error: {error}")

        user = self._user(student_data)
        rows = student_data.get("platbyAPoplatkyVybranychZiakov") or []
        row = rows[0] if rows else {}

        # The paid state of a fee is only in poplatkyPoUctoch (fees grouped by
        # bank account); the plain poplatky list has the same fees without it.
        fees = []
        for fee_rows in (row.get("poplatkyPoUctoch") or {}).values():
            for fee_data in fee_rows or []:
                fee = Fee.parse(fee_data)
                if fee is not None:
                    fees.append(fee)
        if not fees:
            fees = [f for f in (Fee.parse(d) for d in row.get("poplatky") or []) if f is not None]
        fees.sort(key=lambda fee: (fee.due_date or date.max, fee.fee_id))

        payments = [p for p in (Payment.parse(d) for d in row.get("platby") or []) if p is not None]
        payments.sort(key=lambda payment: (payment.date or date.min, payment.payment_id))

        plans = [
            p for p in (PaymentPlan.parse(d) for d in student_data.get("predpisyRows") or []) if p is not None
        ]

        return PaymentsOverview(
            user,
            school_year,
            _str(settings.get("currency")),
            _amount(row.get("madat_dohromady")) or 0.0,
            _amount(row.get("madat")) or 0.0,
            _amount(row.get("dal")) or 0.0,
            fees,
            payments,
            plans,
            accounts,
            list(student_data.get("userProblems") or []),
            student_data,
        )

    def _user(self, student_data: dict) -> str:
        """The EduPage user the page shows; for a parent that is the selected child."""
        rows = student_data.get("platbyAPoplatkyVybranychZiakov") or []
        shown = str(rows[0].get("user")) if rows and isinstance(rows[0], dict) else None

        selected_child = getattr(self.edupage, "_selected_child_id", None)
        if selected_child is not None:
            expected = f"Student{selected_child}"
            if shown is not None and shown != expected:
                raise FailedToParsePaymentsDataError(
                    f"The payments page shows {shown}, not the selected child {expected}"
                )
            return expected

        if shown is None:
            raise FailedToParsePaymentsDataError("The payments page shows no student")
        return shown
