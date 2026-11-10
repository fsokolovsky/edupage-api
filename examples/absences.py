from datetime import date, timedelta

from edupage_api import Edupage

# Absences, excuse notes and their state ("pending", "accepted", "rejected"). Read only.
with Edupage() as edupage:
    edupage.login(
        "Username (or e-mail)",
        "Password",
        "Subdomain of your school (SUBDOMAIN.edupage.org)",
    )

    children = edupage.get_children()
    if children:
        edupage.switch_to_child(children[0])

    overview = edupage.get_absences(date.today() - timedelta(days=60))

    print("Absences:")
    for absence in overview.absences:
        state = "excused" if absence.is_excused else "NOT excused"
        print(f"  {absence.date}  lessons {', '.join(absence.periods)}  {state}  ({absence.recorded_by})")

    print("\nExcuse notes:")
    for excuse in overview.excuses:
        print(f"  {excuse.date_from} - {excuse.date_to}  {excuse.status.value:8}  {excuse.note}")

    print("\nStill to excuse:", [str(a.date) for a in overview.unexcused_absences])
