import json
import unittest
from datetime import date, datetime
from unittest.mock import Mock

from edupage_api import Edupage
from edupage_api.exceptions import FailedToParsePaymentsDataError, RequestError
from edupage_api.payments import Fee, Payment

# Synthetic data in the shape EduPage embeds into /platby/ (no real account data).
INITIAL_DATA = {
    "setPlatbyModuleSettingsUdaje": {
        "error": "",
        "platbyModuleSettings": {
            "currency": "eur",
            "schoolYear": 2026,
            "current_schoolYear": 2026,
            "ucty_skoly": [
                {
                    "ucet_id": "1",
                    "nazov": "Hlavný účet",
                    "cislo_uctu": "1234567890",
                    "kod_banky": "0200",
                    "iban": "SK0000000000001234567890",
                    "swift": "SUBASKBX",
                    "predcislie_uctu": None,
                    "acc_name": "ZÁKLADNÁ ŠKOLA",
                },
                {
                    "ucet_id": 2,
                    "nazov": "Školská jedáleň",
                    "iban": "SK0000000000000000000002",
                    "swift": "SUBASKBX",
                    "kod_banky": "0200",
                    "cislo_uctu": "2",
                    "predcislie_uctu": "131",
                    "acc_name": "ZÁKLADNÁ ŠKOLA",
                },
            ],
        },
    }
}


def fee(fee_id, plan_id, name, due, amount, account, paid=None, allocations=None, **extra):
    row = {
        "poplatok_id": fee_id,
        "datum_splatnosti": due,
        "suma": amount,
        "predpis_id": plan_id,
        "predpis_meno": name,
        "obdobie": "",
        "visible": 1,
        "edupage": "example",
        "ucet_id": account,
        "ss": None,
        "system_note": None,
    }
    row.update(extra)
    if paid is not None:
        row["zaplatene"] = paid
        row["platbaPriradenieSuma"] = allocations
    return row


FEES = [
    fee(10, 5, "Školská jedáleň", "2026-09-20", 10.799999999999999, 2, obdobie="asc_m8", system_note="strava 2026-09"),
    fee(11, 5, "Školská jedáleň", "2026-10-20", 7.9, 2, obdobie="asc_m9", system_note="strava 2026-10"),
    fee(12, 7, "Exkurzia", "2026-11-05", 7, 1, ss="450"),
    fee(13, 7, "Exkurzia 2", "2027-03-05", 12, 1),
]
FEES_BY_ACCOUNT = {
    "2": [
        dict(FEES[0], zaplatene=10.799999999999999, platbaPriradenieSuma={"100#900": 10.799999999999999}),
        dict(FEES[1], zaplatene=3, platbaPriradenieSuma={"101#901": 3}),
    ],
    "1": [
        dict(FEES[2], zaplatene=0, platbaPriradenieSuma=None),
        dict(FEES[3], zaplatene=0, platbaPriradenieSuma=None),
    ],
}


def payment(payment_id, amount, paid_on, **extra):
    row = {
        "platba_id": payment_id,
        "ucet_id": 2,
        "datum_zuctovania": paid_on,
        "datum_pripisania": paid_on,
        "datum_zaevidovania": f"{paid_on} 12:48:32",
        "suma": amount,
        "paymethod": None,
        "typ_platby": "hotovost",
        "variabilny_symbol": "2779",
        "konstantny_symbol": None,
        "specificky_symbol": None,
        "sprava_pre_prijemcu": None,
        "viamo_rid": None,
        "poznamka": None,
        "stav": "o",
        "priradenie_id": 900 + payment_id - 100,
        "predpis_id": 5,
        "priradenie_suma": amount,
        "user": "Student42",
        "predpis_meno": "Školská jedáleň",
        "datum_zaplatenia": paid_on,
        "zaplatilPoplatkyIDS": None,
    }
    row.update(extra)
    return row


PAYMENTS = [
    payment(99, -5, "2026-09-01", typ_platby="pociatocny_stav", priradenie_suma=-5),
    payment(
        100, 15.799999999999999, "2026-09-03",
        typ_platby="viamoPB", paymethod="BANK", viamo_rid="V2EXAMPLE1",
        sprava_pre_prijemcu="V2EXAMPLE1 Example Student",
        zaplatilPoplatkyIDS={"10&0": 10.799999999999999, "11&0": 5},
    ),
    payment(101, 3, "2026-10-01", zaplatilPoplatkyIDS={"11&0": 3}),
]

STUDENT_DATA = {
    "error": "",
    "platbyAPoplatkyVybranychZiakov": [
        {
            "user": "Student42",
            "madat_dohromady": 37.699999999999996,
            "madat": 18.7,
            "dal": 13.799999999999999,
            "poplatky": FEES,
            "poplatkyPoUctoch": FEES_BY_ACCOUNT,
            "platby": PAYMENTS,
        }
    ],
    "predpisyRows": [
        {
            "predpis_id": 5, "ly_predpis_id": 1, "predpis_meno": "Školská jedáleň", "sukromny_owner": None,
            "poplatky": [{"suma": 0, "poplatok_id": 10, "datum_splatnosti": "2026-09-20", "predpis_id": 5}],
            "lastSplatnost": "2027-06-20", "zadavatel_id": "Admin", "ss": None, "ucet_id": 2,
            "visible": 1, "stravaData": {"predpisType": "system"}, "system_note": "strava", "pravo": "ja",
        },
        {
            "predpis_id": 7, "predpis_meno": "Exkurzia", "sukromny_owner": "Ucitel1",
            "poplatky": [{"suma": 7, "poplatok_id": 12, "datum_splatnosti": "2026-11-05", "predpis_id": 7}],
            "lastSplatnost": "2026-11-05", "zadavatel_id": "Ucitel1", "ss": "450", "ucet_id": 1,
            "visible": 1, "stravaData": None, "system_note": None,
            "sukromny_owner_fullName": "Example Teacher", "pravo": "ja",
        },
    ],
    "userProblems": [],
    "menuPredpisyIDS": {"1": 1},
    "useSkupiny": True,
    "skupinyIDS": [],
    "walletsIDS": [],
}


def page_html(initial_data=INITIAL_DATA, student_data=STUDENT_DATA):
    return (
        "<html><script>var initial_data = " + json.dumps(initial_data) + ";\n"
        "var veci_udaje = " + json.dumps(student_data) + ";\n"
        "platbyWidget.platby_ziaka('setUdajeZiaka', 'Student42', veci_udaje);</script></html>"
    )


class PaymentsTests(unittest.TestCase):
    def setUp(self):
        self.api = Edupage()
        self.api.is_logged_in = True
        self.api.subdomain = "example"
        self.api.session = Mock()
        self.page(page_html())

    def page(self, html, status=200):
        response = Mock()
        response.status_code = status
        response.text = html
        self.api.session.get.return_value = response

    def test_overview_totals_and_accounts(self):
        overview = self.api.get_payments()

        self.assertEqual(overview.user, "Student42")
        self.assertEqual(overview.school_year, 2026)
        self.assertEqual(overview.currency, "eur")
        self.assertEqual(overview.total_charged, 37.7)
        self.assertEqual(overview.charged_to_date, 18.7)
        self.assertEqual(overview.paid, 13.8)
        self.assertEqual(overview.balance, -4.9)
        self.assertEqual([a.account_id for a in overview.accounts], [1, 2])
        self.assertEqual(overview.account(2).name, "Školská jedáleň")
        self.assertEqual(overview.account(2).iban, "SK0000000000000000000002")
        self.assertEqual(overview.account(2).account_prefix, "131")
        self.api.session.get.assert_called_once_with("https://example.edupage.org/platby/")

    def test_fees_come_from_the_per_account_lists_with_paid_state(self):
        overview = self.api.get_payments()

        self.assertEqual([f.fee_id for f in overview.fees], [10, 11, 12, 13])  # sorted by due date
        lunches_september, lunches_october, trip, trip2 = overview.fees

        self.assertEqual(lunches_september.amount, 10.8)
        self.assertEqual(lunches_september.paid, 10.8)
        self.assertTrue(lunches_september.is_paid)
        self.assertEqual(lunches_september.allocations, {100: 10.8})
        self.assertEqual(lunches_september.period, "asc_m8")
        self.assertEqual(lunches_september.period_month, 9)
        self.assertEqual(lunches_september.note, "strava 2026-09")
        self.assertEqual(lunches_september.account_id, 2)
        self.assertEqual(lunches_september.due_date, date(2026, 9, 20))

        self.assertEqual(lunches_october.paid, 3)
        self.assertEqual(lunches_october.outstanding, 4.9)
        self.assertFalse(lunches_october.is_paid)
        self.assertEqual(lunches_october.allocations, {101: 3})

        self.assertEqual(trip.specific_symbol, "450")
        self.assertIsNone(trip.period_month)
        self.assertTrue(trip.is_overdue(date(2026, 11, 10)))
        self.assertFalse(trip.is_overdue(date(2026, 11, 5)))
        self.assertFalse(trip2.is_overdue(date(2026, 11, 10)))

        self.assertEqual([f.fee_id for f in overview.outstanding_fees], [11, 12, 13])
        self.assertEqual([f.fee_id for f in overview.overdue_fees(date(2026, 11, 10))], [11, 12])

    def test_payments(self):
        overview = self.api.get_payments()

        self.assertEqual([p.payment_id for p in overview.payments], [99, 100, 101])
        opening, viamo, cash = overview.payments

        self.assertTrue(opening.is_opening_balance)
        self.assertEqual(opening.amount, -5)

        self.assertEqual(viamo.amount, 15.8)
        self.assertEqual(viamo.type, "viamoPB")
        self.assertEqual(viamo.method, "BANK")
        self.assertEqual(viamo.viamo_id, "V2EXAMPLE1")
        self.assertEqual(viamo.date, date(2026, 9, 3))
        self.assertEqual(viamo.registered_at, datetime(2026, 9, 3, 12, 48, 32))
        self.assertEqual(viamo.variable_symbol, "2779")
        self.assertEqual(viamo.plan_id, 5)
        self.assertEqual(viamo.plan_name, "Školská jedáleň")
        self.assertEqual(viamo.allocated_amount, 15.8)
        self.assertEqual(viamo.fee_allocations, {10: 10.8, 11: 5})
        self.assertFalse(viamo.is_opening_balance)

        self.assertEqual(cash.type, "hotovost")
        self.assertIsNone(cash.method)
        self.assertEqual(cash.fee_allocations, {11: 3})

    def test_plans(self):
        overview = self.api.get_payments()

        lunches = overview.plan(5)
        self.assertEqual(lunches.name, "Školská jedáleň")
        self.assertTrue(lunches.is_meals)
        self.assertEqual(lunches.created_by, "Admin")
        self.assertEqual(lunches.last_due_date, date(2027, 6, 20))
        self.assertEqual(lunches.account_id, 2)

        trip = overview.plan(7)
        self.assertFalse(trip.is_meals)
        self.assertEqual(trip.owner_name, "Example Teacher")
        self.assertEqual(trip.specific_symbol, "450")
        self.assertIsNone(overview.plan(99))

    def test_parent_gets_the_selected_child(self):
        self.api._selected_child_id = 42
        self.assertEqual(self.api.get_payments().user, "Student42")

        self.api._selected_child_id = 43
        with self.assertRaisesRegex(FailedToParsePaymentsDataError, "Student43"):
            self.api.get_payments()

    def test_other_school_year_is_requested_from_the_server(self):
        other_year = json.loads(json.dumps(STUDENT_DATA))
        other_year["platbyAPoplatkyVybranychZiakov"][0]["platby"] = []
        response = Mock()
        response.status_code = 200
        response.json.return_value = other_year
        self.api.session.post.return_value = response

        overview = self.api.get_payments(2025)

        self.assertEqual(overview.school_year, 2025)
        self.assertEqual(overview.payments, [])
        self.assertEqual(len(overview.accounts), 2)  # still from the page
        args, kwargs = self.api.session.post.call_args
        self.assertEqual(args[0], "https://example.edupage.org/platby/")
        self.assertEqual(kwargs["data"]["akcia"], "getUdaje")
        self.assertEqual(kwargs["data"]["schoolYear"], "2025")
        self.assertEqual(json.loads(kwargs["data"]["ziaci"]), {"0": "Student42"})
        self.assertEqual(
            json.loads(kwargs["data"]["veci"])["platbyAPoplatkyVybranychZiakov"],
            {"ziaci": {"Student42": "Student42"}},
        )

    def test_current_school_year_does_not_post(self):
        self.api.get_payments(2026)
        self.api.session.post.assert_not_called()

    def test_server_error_in_data_is_raised(self):
        self.page(page_html(student_data=dict(STUDENT_DATA, error="Nemáte prístup")))
        with self.assertRaisesRegex(FailedToParsePaymentsDataError, "Nemáte prístup"):
            self.api.get_payments()

    def test_missing_module_and_http_errors(self):
        self.page("<html>no payments here</html>")
        with self.assertRaisesRegex(FailedToParsePaymentsDataError, "initial_data"):
            self.api.get_payments()

        self.page("", status=404)
        with self.assertRaises(RequestError):
            self.api.get_payments()

    def test_fee_and_payment_parsers_tolerate_missing_fields(self):
        self.assertIsNone(Fee.parse({}))
        self.assertIsNone(Payment.parse({"suma": 1}))

        minimal = Fee.parse({"poplatok_id": "7", "suma": "3.5"})
        self.assertEqual(minimal.amount, 3.5)
        self.assertEqual(minimal.paid, 0)
        self.assertIsNone(minimal.due_date)
        self.assertFalse(minimal.is_overdue())
        self.assertEqual(minimal.allocations, {})

        minimal_payment = Payment.parse({"platba_id": 1})
        self.assertEqual(minimal_payment.amount, 0)
        self.assertIsNone(minimal_payment.date)
        self.assertEqual(minimal_payment.fee_allocations, {})


if __name__ == "__main__":
    unittest.main()
