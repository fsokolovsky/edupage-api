import json
import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

from edupage_api import Edupage
from edupage_api.exceptions import MissingDataException

# Synthetic timeline items and reactions in the shape EduPage sends (no real account data).
# The family: parent Rodic-10 (logged in), parent Rodic-11, child Student42.

FAMILY_LOGIN_DATA = {
    "userid": "Rodic-10",
    "parentStudentids": [42],
    "items": [],
    "userProps": {},
    "dbi": {
        "students": {"42": {"id": 42, "parent1id": "-10", "parent2id": "-11", "parent3id": ""}},
        "parents": {"-10": {"id": -10, "studentids": [42]}, "-11": {"id": -11, "studentids": [42]}},
    },
}


def message(timeline_id, timestamp, data, typ="sprava", text="Dôležitá správa", reply_to=None):
    return {
        "timelineid": str(timeline_id),
        "timestamp": timestamp,
        "cas_pridania": timestamp,
        "cas_udalosti": None,
        "reakcia_na": reply_to,
        "typ": typ,
        "text": text,
        "data": json.dumps(data),
        "user": "Student*",
        "user_meno": "*",
        "vlastnik": "Ucitel1",
        "vlastnik_meno": "Example Teacher",
        "ineid": None,
        "pocet_reakcii": 0,
        "removed": 0,
    }


def confirmation(timeline_id, message_id, account, when, data=None, user=None, name="Somebody"):
    return {
        "timelineid": timeline_id,
        "timestamp": "2026-11-01 00:00:00",
        "reakcia_na": message_id,
        "typ": "confirmation",
        "user": user or account,
        "user_meno": name,
        "ineid": str(message_id),
        "text": "",
        "cas_pridania": when,
        "cas_udalosti": None,
        "data": [] if data is None else data,
        "vlastnik": account,
        "vlastnik_meno": name,
        "pocet_reakcii": 0,
        "removed": 0,
        "nieJePreMna": account != "Rodic-10",
    }


RECEIPT = {"receipt": "1", "confirmations": {"like": 3, "receipt": 42}, "messageContent": "Please confirm"}
VOTING = {
    "votingParams": {"answers": [{"id": 2, "text": "yes"}, {"id": 3, "text": "no"}], "multiple": False},
    "confirmations": {"vote2": 7, "vote3": 45, "like": 1},
    "messageContent": "Vote",
}

ITEMS = [
    # 100: receipt, confirmed by me (timeline knows it)
    message(100, "2026-10-01 08:00:00", dict(RECEIPT, myConfirmations={"receipt": "2026-10-01 09:00:00"})),
    # 101: receipt, confirmed only by the other parent
    message(101, "2026-10-02 08:00:00", RECEIPT),
    # 102: receipt, confirmed in the child's account by me
    message(102, "2026-10-03 08:00:00", RECEIPT),
    # 103: voting, the other parent only liked it
    message(103, "2026-10-04 08:00:00", VOTING),
    # 104: voting, the other parent voted
    message(104, "2026-10-05 08:00:00", VOTING),
    # 105: receipt, nobody in the family
    message(105, "2026-10-06 08:00:00", RECEIPT),
    # 108: receipt, EduPage has no reactions for it
    message(108, "2026-10-06 09:00:00", RECEIPT),
    # plain message and a reply: not requests
    message(106, "2026-10-07 08:00:00", {"messageContent": "Hello"}),
    message(107, "2026-10-07 09:00:00", RECEIPT, reply_to="101"),
]

REACTIONS = {
    "100": [
        confirmation(1000, 100, "Rodic-10", "2026-10-01 09:00:00",
                     json.dumps({"receipt": "2026-10-01 09:00:00", "myConfirmations": {"receipt": "2026-10-01 09:00:00"}})),
        confirmation(1001, 100, "Rodic-77", "2026-10-01 09:30:00"),
    ],
    "101": [
        confirmation(1010, 100099, "Rodic-11", "2026-10-02 17:46:25", name="Other Parent"),  # a sibling copy
        confirmation(1011, 101, "Rodic-77", "2026-10-02 18:00:00",
                     json.dumps({"myConfirmations": {"receipt": "2026-10-02 19:00:00"}})),
    ],
    "102": [
        confirmation(1020, 102, "Student42", "2026-10-03 15:53:43", {"repliesToAllDisabled": "1"}, user="Rodic-10"),
    ],
    "103": [
        confirmation(1030, 103, "Rodic-11", "2026-10-04 11:48:16", {"like": "2026-10-04 11:48:19"}),
        confirmation(1031, 103, "Rodic-77", "2026-10-04 12:00:00"),
    ],
    "104": [
        confirmation(1040, 104, "Rodic-11", "2026-10-05 20:00:00"),
    ],
    "105": [
        confirmation(1050, 105, "Rodic-77", "2026-10-06 10:00:00"),
        confirmation(1051, 105, "Student99", "2026-10-06 10:00:00"),
        {"timelineid": 1052, "typ": "sprava", "reakcia_na": 105, "text": "a reply", "data": "{}", "vlastnik": "Rodic-77"},
    ],
}

PARTICIPANTS = {
    "Ucitel1": {"id": "Ucitel1", "firstname": "Example", "lastname": "Teacher", "typ": "Ucitel"},
    "Rodic-10": {"id": "Rodic-10", "firstname": "Me", "lastname": "Parent", "typ": "Rodic", "children": [42]},
    "Rodic-11": {"id": "Rodic-11", "firstname": "Other", "lastname": "Parent", "typ": "Rodic", "children": [42]},
    "Rodic-77": {"id": "-77", "firstname": "", "lastname": ""},
    "Student42": {"id": "Student42", "firstname": "Example", "lastname": "Child", "typ": "Student"},
}


class ConfirmationsTests(unittest.TestCase):
    def setUp(self):
        self.api = Edupage()
        self.api.is_logged_in = True
        self.api.subdomain = "example"
        self.api.data = json.loads(json.dumps(FAMILY_LOGIN_DATA))
        self.api._selected_child_id = 42
        self.api.session = Mock()
        self.api.session.post.side_effect = self.post
        self.requested = []
        person_lookup = patch(
            "edupage_api.timeline.DbiHelper.fetch_person_data_by_name",
            return_value=None,
        )
        person_lookup.start()
        self.addCleanup(person_lookup.stop)

    def post(self, url, params=None, data="", headers=None, **kwargs):
        response = Mock()
        response.status_code = 200
        actions = dict(params or [])
        if actions.get("akcia") == "getData":
            response.json.return_value = {"timelineItems": ITEMS, "timelineUserProps": {}}
        elif actions.get("akcia") == "getRepliesItem":
            self.assertIn("lastsync=", data)
            group_id = dict(pair.split("=") for pair in data.split("&"))["groupid"]
            self.requested.append(group_id)
            if group_id in REACTIONS:
                response.json.return_value = {
                    "status": "ok",
                    "data": {
                        "reakcie": REACTIONS[group_id],
                        "participants": PARTICIPANTS,
                        "numParticipants": len(PARTICIPANTS),
                        "item": ITEMS[0],
                    },
                }
            else:
                response.json.return_value = {"status": "fail"}
        else:
            self.fail(f"unexpected request {url} {params}")
        return response

    def test_family_from_login_data(self):
        self.assertEqual(self.api.get_confirmations(date(2026, 10, 1), fetch_reactions=False).family_ids,
                         ["Rodic-10", "Student42", "Rodic-11"])
        self.api._selected_child_id = None
        self.assertEqual(self.api.get_confirmations(date(2026, 10, 1), fetch_reactions=False).family_ids,
                         ["Rodic-10", "Student42", "Rodic-11"])

    def test_only_requests_are_listed(self):
        overview = self.api.get_confirmations(date(2026, 10, 1), fetch_reactions=False)
        self.assertEqual([m.message_id for m in overview.messages], [100, 101, 102, 103, 104, 105, 108])
        self.assertEqual(self.requested, [])

        first = overview.messages[0]
        self.assertTrue(first.requires_receipt)
        self.assertFalse(first.is_voting)
        self.assertEqual(first.counts, {"like": 3, "receipt": 42})
        self.assertEqual(first.my_receipt_at, datetime(2026, 10, 1, 9, 0, 0))
        self.assertTrue(first.confirmed_by_me)
        self.assertTrue(first.is_confirmed)
        self.assertEqual(first.confirmed_by, ["Rodic-10"])
        self.assertIsNone(first.family_confirmations)

        voting = overview.messages[3]
        self.assertTrue(voting.is_voting)
        self.assertEqual([(o.option_id, o.text) for o in voting.voting_options], [(2, "yes"), (3, "no")])
        self.assertFalse(voting.voting_multiple)
        self.assertEqual(voting.counts, {"vote2": 7, "vote3": 45, "like": 1})

        # without reactions, everything I did not confirm myself is pending
        self.assertEqual([m.message_id for m in overview.pending], [101, 102, 103, 104, 105, 108])
        self.assertEqual(overview.unknown, [])

    def test_family_confirmations_from_reactions(self):
        overview = self.api.get_confirmations(date(2026, 10, 1))
        self.assertEqual(self.requested, ["100", "101", "102", "103", "104", "105", "108"])
        by_id = {m.message_id: m for m in overview.messages}

        mine = by_id[100]
        self.assertEqual(mine.confirmed_by, ["Rodic-10"])
        self.assertEqual(mine.family_confirmations[0].receipt_at, datetime(2026, 10, 1, 9, 0, 0))
        self.assertEqual(mine.family_confirmations[0].raw, {"receipt": "2026-10-01 09:00:00"})

        other_parent = by_id[101]
        self.assertTrue(other_parent.is_confirmed)
        self.assertFalse(other_parent.confirmed_by_me)
        self.assertEqual(other_parent.confirmed_by, ["Rodic-11"])
        self.assertEqual(other_parent.confirmed_at, datetime(2026, 10, 2, 17, 46, 25))
        self.assertEqual(other_parent.family_confirmations[0].account_name, "Other Parent")
        self.assertEqual(other_parent.family_confirmations[0].message_id, 100099)

        via_child = by_id[102]
        self.assertEqual(via_child.confirmed_by, ["Student42"])
        self.assertEqual(via_child.family_confirmations[0].actor_id, "Rodic-10")

        liked_vote = by_id[103]
        self.assertTrue(liked_vote.family_confirmations[0].is_like_only)
        self.assertEqual(liked_vote.family_confirmations[0].liked_at, datetime(2026, 10, 4, 11, 48, 19))
        self.assertTrue(liked_vote.is_pending)

        voted = by_id[104]
        self.assertEqual(voted.confirmed_by, ["Rodic-11"])

        nobody = by_id[105]
        self.assertEqual(nobody.family_confirmations, [])
        self.assertTrue(nobody.is_pending)

        unknown = by_id[108]
        self.assertIsNone(unknown.family_confirmations)
        self.assertFalse(unknown.reactions_fetched)
        self.assertIn("108", unknown.reactions_error)
        self.assertTrue(nobody.reactions_fetched)
        self.assertIsNone(nobody.reactions_error)

        self.assertEqual([m.message_id for m in overview.pending], [103, 105])
        self.assertEqual([m.message_id for m in overview.unknown], [108])
        self.assertEqual([m.message_id for m in overview.confirmed], [100, 101, 102, 104])

    def test_message_confirmations(self):
        reactions = self.api.get_message_confirmations(105)
        self.assertEqual(reactions.message_id, 105)
        self.assertEqual([c.account_id for c in reactions.confirmations], ["Rodic-77", "Student99"])
        self.assertEqual(reactions.participant_count, 5)
        self.assertEqual(reactions.parents_of(42), ["Rodic-10", "Rodic-11"])
        self.assertEqual(reactions.participants["Rodic-77"].account_type, None)
        self.assertEqual(reactions.participants["Rodic-11"].last_name, "Parent")
        self.assertEqual(reactions.by(["Rodic-77"])[0].event_id, 1050)

        with self.assertRaises(MissingDataException):
            self.api.get_message_confirmations(999)

    def test_empty_period(self):
        self.api.session.post.side_effect = None
        self.api.session.post.return_value.status_code = 200
        self.api.session.post.return_value.json.return_value = {"no": "events"}
        overview = self.api.get_confirmations(date(2026, 10, 1))
        self.assertEqual(overview.messages, [])


if __name__ == "__main__":
    unittest.main()
