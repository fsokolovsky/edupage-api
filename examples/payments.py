from datetime import date

from edupage_api import Edupage

# Fees, payments and the school's bank accounts ("Payments" module). Read only.
with Edupage() as edupage:
    edupage.login(
        "Username (or e-mail)",
        "Password",
        "Subdomain of your school (SUBDOMAIN.edupage.org)",
    )

    # A parent has to pick the child first; a student account can skip this.
    children = edupage.get_children()
    if children:
        edupage.switch_to_child(children[0])

    payments = edupage.get_payments()  # or edupage.get_payments(school_year=2025)

    print(f"{payments.user}, school year {payments.school_year}/{payments.school_year + 1}")
    print(f"charged so far: {payments.charged_to_date} {payments.currency}")
    print(f"paid:           {payments.paid} {payments.currency}")
    print(f"balance:        {payments.balance} {payments.currency}")
    print()

    print("Unpaid fees:")
    for fee in payments.outstanding_fees:
        overdue = " (OVERDUE)" if fee.is_overdue(date.today()) else ""
        account = payments.account(fee.account_id) if fee.account_id else None
        iban = account.iban if account else "?"
        print(
            f"  {fee.due_date}  {fee.outstanding:>7.2f}  {fee.plan_name}{overdue}"
            f"  -> {iban}, VS {fee.specific_symbol or '-'}"
        )
    print()

    print("Payments received:")
    for payment in payments.payments:
        kind = "opening balance" if payment.is_opening_balance else payment.type
        print(f"  {payment.date}  {payment.amount:>7.2f}  {kind}  {payment.plan_name or ''}")
