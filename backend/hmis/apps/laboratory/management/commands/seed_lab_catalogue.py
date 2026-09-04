"""
Put the starting laboratory catalogue in, or top it up after an upgrade.

Safe to run repeatedly: a test that already exists is left exactly as the
hospital has it — price, name, parameters and all. Only missing rows are
added, so this can be run after pulling a release that extends the catalogue
without undoing a single local edit.
"""
from django.core.management.base import BaseCommand

from apps.laboratory.catalog import seed_catalogue
from apps.laboratory.models import LabPanel, LabParameter, LabTest


class Command(BaseCommand):
    help = "Seed or top up the laboratory test catalogue."

    def handle(self, *args, **options):
        tests, params, panels = seed_catalogue(
            LabTest=LabTest, LabParameter=LabParameter, LabPanel=LabPanel,
            stdout=self.stdout,
        )
        if not (tests or params or panels):
            self.stdout.write("Laboratory catalogue already complete — nothing added.")
