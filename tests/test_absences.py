import json
import unittest
from datetime import date, datetime
from unittest.mock import Mock, patch

from edupage_api import Edupage
from edupage_api.absences import ExcuseStatus

# Synthetic timeline items in the shape EduPage sends (no real account data).


def item(timeline_id, timestamp, typ, text, data, author="Example Teacher", ineid=None, event_time=None):
    return {
        "timelineid": str(timeline_id),
        "timestamp": timestamp,
        "cas_pridania": timestamp,
        "cas_udalosti": event_time,
        "typ": typ,
        "text": text,
        "data": json.dumps(data),
        "user": "Student42",
        "user_meno": "Example Student",
        "vlastnik": "Ucitel1",
        "vlastnik_meno": author,
        "ineid": ineid,
        "pocet_reakcii": 0,
        "removed": 0,
    }


def absence(timeline_id, timestamp, day, periods, excused, author="Example Teacher"):
    data = {"cas_udalosti": day, "isFirstInDay": False, "isExcused": excused}
    data.update(periods)
    return item(timeline_id, timestamp, "student_absent", f"Absence {day}", data, author, ineid=day,
                event_time=f"{day} 00:00:00")


def excuse(timeline_id, timestamp, excuse_id, day_from, day_to, note, decided=None, sent_type="sa_parent"):
    data = {
        "id": excuse_id,
        "studentid": "42",
        "datefrom": day_from,
        "dateto": day_to,
        "year": 2026,
        "type": sent_type,
        "note": note,
        "studentabsent_typeid": "-2",
        "changes": None,
    }
    if decided is not None:
        data["akceptovane"] = decided == "accepted"
        data["zamietnute"] = decided == "rejected"
    return item(timeline_id, timestamp, "ospravedlnenka", f"Nová ospravedlnenka {day_from}", data,
                ineid=excuse_id, event_time=f"{day_from} 00:00:00")


ITEMS = [
    # 2026-10-07: absent, note sent and accepted
    absence(100, "2026-10-07 10:00:00", "2026-10-07", {"p_1": "A", "p_2": "A"}, False),
    absence(101, "2026-10-08 09:00:00", "2026-10-07", {"p_1": "A", "p_2": "A", "p_3": "A-L"}, True),
    excuse(110, "2026-10-07 08:00:00", "AAAA", "2026-10-07", "2026-10-07", "Doctor", sent_type=None),
    excuse(111, "2026-10-08 09:00:00", "AAAA", "2026-10-07", "2026-10-07", "Doctor", decided="accepted"),
    # 2026-10-16: absent, note pending
    absence(120, "2026-10-16 12:00:00", "2026-10-16", {"p_5": "A"}, False),
    excuse(121, "2026-10-16 13:00:00", "BBBB", "2026-10-16", "2026-10-17", "Ill"),
    # 2026-10-20: absent, note rejected -> still unexcused
    absence(130, "2026-10-20 12:00:00", "2026-10-20", {"p_1": "A"}, False, author="Other Teacher"),
    excuse(131, "2026-10-21 08:00:00", "CCCC", "2026-10-20", "2026-10-20", "Overslept", decided="rejected"),
    # 2026-10-22: absent, the teacher entered the note (no parent note, no decision)
    absence(135, "2026-10-22 12:00:00", "2026-10-22", {"p_1": "A", "p_2": "A"}, True),
    excuse(136, "2026-10-23 09:00:00", "DDDD", "2026-10-22", "2026-10-22", None, sent_type=None),
    # 2026-11-04: absent, nothing sent
    absence(140, "2026-11-04 12:03:59", "2026-11-04", {"p_07-40": "A-P"}, False),
    item(150, "2026-11-05 13:24:57", "ospravedlnenka_reminder",
         "Vaše dieťa má nevyriešené absencie.", {"dates": ["2026-11-04"], "studentid": 42}, author="", ineid="42"),
    # unrelated
    item(160, "2026-11-05 14:00:00", "sprava", "Hello", {}),
]


class AbsencesTests(unittest.TestCase):
    def setUp(self):
        self.api = Edupage()
        self.api.is_logged_in = True
        self.api.subdomain = "example"
        self.api.data = {"items": [], "userProps": {}}
        self.api.session = Mock()
        self.api.session.post.return_value.status_code = 200
        self.api.session.post.return_value.json.return_value = {
            "timelineItems": ITEMS,
            "timelineUserProps": {},
        }
        person_lookup = patch(
            "edupage_api.timeline.DbiHelper.fetch_person_data_by_name",
            return_value=None,
        )
        person_lookup.start()
        self.addCleanup(person_lookup.stop)

    def test_absences_keep_the_latest_item_per_day(self):
        overview = self.api.get_absences(date(2026, 10, 1))

        self.assertEqual([a.date for a in overview.absences],
                         [date(2026, 10, 7), date(2026, 10, 16), date(2026, 10, 20), date(2026, 10, 22),
                          date(2026, 11, 4)])
        first = overview.absences[0]
        self.assertEqual(first.event_id, 101)
        self.assertEqual(first.periods, {"1": "A", "2": "A", "3": "A-L"})
        self.assertTrue(first.is_excused)
        self.assertEqual(first.recorded_by, "Example Teacher")
        self.assertEqual(first.recorded_at, datetime(2026, 10, 8, 9, 0, 0))

        late = overview.absences[4]
        self.assertEqual(late.periods, {"07-40": "A-P"})
        self.assertFalse(late.is_excused)
        self.assertEqual(overview.absences[2].recorded_by, "Other Teacher")

    def test_excuses_merge_their_items_and_carry_the_decision(self):
        overview = self.api.get_absences(date(2026, 10, 1))

        self.assertEqual([e.excuse_id for e in overview.excuses], ["AAAA", "BBBB", "CCCC", "DDDD"])
        accepted, pending, rejected, recorded = overview.excuses

        self.assertEqual(accepted.status, ExcuseStatus.ACCEPTED)
        self.assertEqual(accepted.event_ids, [110, 111])
        self.assertEqual(accepted.created_at, datetime(2026, 10, 7, 8, 0, 0))
        self.assertEqual(accepted.updated_at, datetime(2026, 10, 8, 9, 0, 0))
        self.assertTrue(accepted.sent_by_parent)
        self.assertEqual(accepted.note, "Doctor")
        self.assertEqual(accepted.student_id, 42)
        self.assertEqual(accepted.teacher, "Example Teacher")
        self.assertEqual(accepted.reason_type_id, "-2")
        self.assertEqual((accepted.date_from, accepted.date_to), (date(2026, 10, 7), date(2026, 10, 7)))

        self.assertEqual(pending.status, ExcuseStatus.PENDING)
        self.assertTrue(pending.covers(date(2026, 10, 17)))
        self.assertFalse(pending.covers(date(2026, 10, 18)))

        self.assertEqual(rejected.status, ExcuseStatus.REJECTED)
        self.assertEqual(recorded.status, ExcuseStatus.RECORDED)
        self.assertFalse(recorded.sent_by_parent)
        self.assertIsNone(recorded.note)
        self.assertEqual([e.is_resolved for e in overview.excuses], [True, False, False, True])
        self.assertEqual(overview.pending_excuses, [pending])
        self.assertEqual(overview.excuses_for(date(2026, 10, 7)), [accepted])

    def test_unexcused_absences_and_reminders(self):
        overview = self.api.get_absences(date(2026, 10, 1))

        self.assertEqual([a.date for a in overview.unexcused_absences],
                         [date(2026, 10, 20), date(2026, 11, 4)])
        self.assertEqual(len(overview.reminders), 1)
        reminder = overview.reminders[0]
        self.assertEqual(reminder.dates, [date(2026, 11, 4)])
        self.assertEqual(reminder.student_id, 42)
        self.assertEqual(reminder.sent_at, datetime(2026, 11, 5, 13, 24, 57))

    def test_empty_period(self):
        self.api.session.post.return_value.json.return_value = {"no": "events"}
        overview = self.api.get_absences(date(2026, 10, 1))
        self.assertEqual((overview.absences, overview.excuses, overview.reminders), ([], [], []))
        self.assertEqual(overview.unexcused_absences, [])


if __name__ == "__main__":
    unittest.main()
