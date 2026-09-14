from django.core.management.base import BaseCommand

from apps.billing import integrity


class Command(BaseCommand):
    help = ("Read-only check of the billing tables: paid charges with no payment allocation "
            "behind them, payments not fully allocated, patients in credit, and ledgers that "
            "have drifted from their rows. Changes nothing.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict", action="store_true",
            help="Exit with status 1 if any paid charge cannot be traced to a payment.")

    def handle(self, *args, strict=False, **options):
        found = integrity.report()
        out = self.stdout.write
        out(f"Charges:                          {found['charges']}")
        out(f"  paid (amount_paid > 0):         {found['paid_charges']}")
        out(f"  cancelled:                      {found['cancelled_charges']}")
        out(f"Payments:                         {found['payments']}")
        out(f"Refunds:                          {found['refunds']}")
        out("")
        sections = [
            ("Paid charges with no PaymentAllocation", "allocation_less_paid_charges"),
            ("Paid charges not fully traceable to payments", "untraceable_paid_charges"),
            ("Payments not fully allocated to charges", "unallocated_payments"),
            ("Patients whose ledger reads in credit", "negative_ledgers"),
            ("Ledgers that differ from their rows", "ledger_drift"),
        ]
        for title, key in sections:
            rows = found[key]
            out(f"{title}: {len(rows)}")
            for row in rows:
                out(f"  {row}")

        if strict and found["untraceable_paid_charges"]:
            raise SystemExit(1)
