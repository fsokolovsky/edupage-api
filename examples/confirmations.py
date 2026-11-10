from datetime import date, timedelta

from edupage_api import Edupage

# Messages that ask for a read receipt or a vote, and whether anybody in the
# family (you, the other parent, the child) already confirmed them. Read only.
with Edupage() as edupage:
    edupage.login(
        "Username (or e-mail)",
        "Password",
        "Subdomain of your school (SUBDOMAIN.edupage.org)",
    )

    children = edupage.get_children()
    if children:
        edupage.switch_to_child(children[0])

    overview = edupage.get_confirmations(date.today() - timedelta(days=60))
    print("Family accounts:", overview.family_ids)

    print("\nStill to confirm:")
    for status in overview.pending:
        kind = "vote" if status.is_voting else "read receipt"
        print(f"  {status.event.timestamp:%Y-%m-%d}  {kind:12}  {status.event.text[:60]!r}")

    print("\nConfirmed:")
    for status in overview.confirmed:
        print(f"  {status.event.timestamp:%Y-%m-%d}  by {', '.join(status.confirmed_by)} at {status.confirmed_at}")

    if overview.messages:
        reactions = edupage.get_message_confirmations(overview.messages[-1].event)
        print(f"\nThe last one has {len(reactions.confirmations)} confirmations in total")
