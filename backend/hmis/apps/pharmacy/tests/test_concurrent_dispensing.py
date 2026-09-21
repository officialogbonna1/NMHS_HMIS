"""
The last unit, and two people reaching for it.

Two doctors may each write a prescription for the one box left — a
prescription is a clinical order, not a claim on stock, and nothing is
reserved when one is written (`create_prescription`). The conflict is settled
where the stock actually moves, and these tests are about that moment.

What holds it, smallest first: the prescription row is locked, so one script
cannot be dispensed twice; the stock records for that product on the
dispensing shelf are locked `of=("self",)`; and the deduction itself is a
conditional `UPDATE` in `inventory.services.apply_stock_change` —
`SET quantity = quantity - n WHERE quantity >= n` — so checking the shelf and
taking stock off it are one indivisible statement.

That last one is the load-bearing part and the reason these tests do not need
two threads to be meaningful. `select_for_update` is a documented no-op on
SQLite, which is what `settings.DATABASES` configures, so on this deployment
the locks compile away entirely and the conditional UPDATE is the whole
protection. `TwoPharmacistsAtOnce` at the foot runs the real race on
PostgreSQL where there is one to run; everything above it forces the
interleave deterministically instead, by letting a second dispense land in
the middle of the first — which is the same window, examined rather than
raced for.
"""
import threading
from decimal import Decimal
from unittest import mock, skipUnless

from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge
from apps.inventory.models import (MAIN_STORE, PHARMACY, StockLocation, StockMovement,
                                   StockRecord)
from apps.inventory.services import InsufficientStockError, transfer_stock
from apps.inventory.testing import product, stock_the_pharmacy, stock_the_store
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription
from apps.pharmacy.services import (AlreadyDispensedError, OutOfStockError,
                                    available_quantity, create_prescription,
                                    dispense_prescription)


class Counter(TestCase):
    """One product, one unit of it on the dispensing shelf, two doctors."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        self.doctor_a = User.objects.create_user(username="da", password="t", role="doctor",
                                                 first_name="Ada", last_name="Okoro")
        self.doctor_b = User.objects.create_user(username="db", password="t", role="doctor",
                                                 first_name="Bala", last_name="Musa")
        self.patient_a = Patient.objects.create(first_name="Ann", last_name="One", sex="F",
                                                created_by=self.reception)
        self.patient_b = Patient.objects.create(first_name="Ben", last_name="Two", sex="M",
                                                created_by=self.reception)
        self.item = product("Paracetamol", unit_name="tablet", category_name="Pain Relief")
        self.batch = stock_the_pharmacy(item=self.item, quantity=1, actor=self.pharmacist)
        self.pharmacy = StockLocation.objects.get(code=PHARMACY)
        self.store = StockLocation.objects.get(code=MAIN_STORE)

    # -- helpers ---------------------------------------------------------------

    def script(self, doctor, patient, quantity=1):
        return create_prescription(patient=patient, doctor=doctor, item=self.item,
                                   quantity=quantity)

    def on_shelf(self):
        return available_quantity(self.item, self.pharmacy)

    def movements(self):
        return StockMovement.objects.filter(batch__item=self.item, reason="prescription")


class BothMayPrescribe(Counter):
    """A prescription is a clinical order. It reserves nothing."""

    def test_two_doctors_both_prescribe_the_single_remaining_unit(self):
        first = self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)

        self.assertEqual([first.status, second.status], ["pending", "pending"])
        # Neither took the unit off the shelf, and neither raised a charge.
        self.assertEqual(self.on_shelf(), 1)
        self.assertFalse(self.movements().exists())
        self.assertFalse(Charge.objects.filter(source_type="prescription").exists())

    def test_prescribing_more_than_the_shelf_holds_is_still_refused_at_writing(self):
        """The doctor is told straight away — this check is a courtesy and is
        unchanged; the one that decides is at dispensing."""
        with self.assertRaises(OutOfStockError):
            self.script(self.doctor_a, self.patient_a, quantity=2)


class OnlyOneOfThemGetsIt(Counter):
    """The second attempt is refused, and leaves nothing behind."""

    def test_the_first_dispense_takes_the_last_unit_and_the_second_is_refused(self):
        first = self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)

        dispense_prescription(prescription=first, pharmacist=self.pharmacist)

        with self.assertRaises(OutOfStockError) as caught:
            dispense_prescription(prescription=second, pharmacist=self.pharmacist)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "dispensed")
        self.assertEqual(second.status, "pending")       # still fillable once restocked
        self.assertEqual(self.on_shelf(), 0)
        self.assertEqual(caught.exception.available, 0)
        self.assertEqual(caught.exception.requested, 1)

    def test_the_refusal_says_the_units_may_have_gone_to_another_transaction(self):
        first = self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)
        dispense_prescription(prescription=first, pharmacist=self.pharmacist)

        with self.assertRaises(OutOfStockError) as caught:
            dispense_prescription(prescription=second, pharmacist=self.pharmacist)

        message = " ".join(caught.exception.messages)
        self.assertIn("Insufficient stock", message)
        self.assertIn("Paracetamol has 0 unit(s) available", message)
        self.assertIn("dispensed or allocated by another transaction", message)
        self.assertEqual(caught.exception.code, "insufficient_stock")

    def test_the_refusal_points_at_the_store_only_when_the_store_has_some(self):
        self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)
        dispense_prescription(prescription=Prescription.objects.get(doctor=self.doctor_a),
                              pharmacist=self.pharmacist)

        with self.assertRaises(OutOfStockError) as caught:
            dispense_prescription(prescription=second, pharmacist=self.pharmacist)
        self.assertNotIn("Main Store", " ".join(caught.exception.messages))

        stock_the_store(item=self.item, quantity=40, actor=self.pharmacist, batch_no="B-STORE")
        with self.assertRaises(OutOfStockError) as caught:
            dispense_prescription(prescription=second, pharmacist=self.pharmacist)
        self.assertIn("40 unit(s) in Main Store", " ".join(caught.exception.messages))

    def test_the_failed_attempt_writes_no_movement_and_no_charge(self):
        first = self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)
        dispense_prescription(prescription=first, pharmacist=self.pharmacist)

        movements_before = list(self.movements().values_list("pk", flat=True))
        charges_before = Charge.objects.count()

        with self.assertRaises(OutOfStockError):
            dispense_prescription(prescription=second, pharmacist=self.pharmacist)

        # Exactly the one movement the successful dispense wrote, and no
        # second charge on anybody's ledger.
        self.assertEqual(list(self.movements().values_list("pk", flat=True)), movements_before)
        self.assertEqual(self.movements().count(), 1)
        self.assertEqual(Charge.objects.count(), charges_before)
        self.assertFalse(Charge.objects.filter(patient=self.patient_b).exists())

    def test_no_dispensing_record_is_stamped_on_the_failed_prescription(self):
        first = self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)
        dispense_prescription(prescription=first, pharmacist=self.pharmacist)

        with self.assertRaises(OutOfStockError):
            dispense_prescription(prescription=second, pharmacist=self.pharmacist)

        second.refresh_from_db()
        self.assertEqual(second.status, "pending")
        self.assertIsNone(second.dispensed_by)
        self.assertIsNone(second.dispensed_at)
        self.assertIsNone(second.dispensed_value)

    def test_stock_never_goes_below_zero(self):
        first = self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)
        dispense_prescription(prescription=first, pharmacist=self.pharmacist)
        with self.assertRaises(OutOfStockError):
            dispense_prescription(prescription=second, pharmacist=self.pharmacist)

        self.assertEqual(StockRecord.objects.get(batch=self.batch,
                                                 location=self.pharmacy).quantity, 0)
        self.assertFalse(StockRecord.objects.filter(quantity__lt=0).exists())
        # And the shelf reconstructs from its own movements: +1 in, −1 out.
        self.assertEqual(
            sum(StockMovement.objects.filter(batch=self.batch, location=self.pharmacy)
                .values_list("change", flat=True)), 0)

    def test_one_script_cannot_be_dispensed_twice(self):
        """Two clicks on one counter, not two counters — held by the
        prescription's own status under its own lock."""
        stock_the_pharmacy(item=self.item, quantity=5, actor=self.pharmacist, batch_no="B-EXTRA")
        script = self.script(self.doctor_a, self.patient_a)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        with self.assertRaises(AlreadyDispensedError):
            dispense_prescription(prescription=script, pharmacist=self.pharmacist)
        self.assertEqual(self.movements().count(), 1)


class TheDeductionIsIndivisible(Counter):
    """
    The interleave itself, forced rather than raced for.

    Each test below lets a *whole second dispense* run at the exact point the
    first has read the shelf and is about to take stock off it — the window
    `select_for_update` closes on PostgreSQL and cannot close on SQLite. The
    conditional UPDATE is what has to refuse it.
    """

    def _steal_the_unit_mid_dispense(self, thief_script):
        """Patch `apply_stock_change` so the first call dispenses `thief_script`
        first — i.e. the shelf empties between the caller's read and its write."""
        from apps.pharmacy import services as pharmacy_services
        real = pharmacy_services.apply_stock_change
        state = {"done": False}

        def interleaved(record, delta, **kwargs):
            if not state["done"]:
                state["done"] = True
                dispense_prescription(prescription=thief_script, pharmacist=self.pharmacist)
            return real(record, delta, **kwargs)

        return mock.patch.object(pharmacy_services, "apply_stock_change", interleaved)

    def test_a_dispense_that_loses_the_unit_mid_transaction_is_refused(self):
        loser = self.script(self.doctor_a, self.patient_a)
        winner = self.script(self.doctor_b, self.patient_b)

        with self._steal_the_unit_mid_dispense(winner):
            with self.assertRaises(OutOfStockError) as caught:
                dispense_prescription(prescription=loser, pharmacist=self.pharmacist)

        self.assertIn("another transaction", " ".join(caught.exception.messages))
        self.assertEqual(caught.exception.available, 0)

    def test_the_loser_leaves_nothing_behind(self):
        """
        Everything the refused dispense touched goes back with it.

        Only the loser's side is asserted here, and deliberately: a `TestCase`
        is one connection in one transaction, so the interleaved dispense is a
        savepoint inside the very block that rolls back — the winner's commit
        cannot survive it here whatever the code does. That half is
        `TwoPharmacistsAtOnce`'s to prove, on a database with two connections.
        What *is* meaningful here is that the loser wrote nothing.
        """
        loser = self.script(self.doctor_a, self.patient_a)
        winner = self.script(self.doctor_b, self.patient_b)

        with self._steal_the_unit_mid_dispense(winner):
            with self.assertRaises(OutOfStockError):
                dispense_prescription(prescription=loser, pharmacist=self.pharmacist)

        loser.refresh_from_db()
        self.assertEqual(loser.status, "pending")
        self.assertIsNone(loser.dispensed_at)
        # Not one unit moved on its reference, and not one naira was charged.
        self.assertFalse(
            StockMovement.objects.filter(reference=f"prescription:{loser.pk}").exists())
        self.assertFalse(Charge.objects.filter(patient=self.patient_a).exists())
        self.assertFalse(StockRecord.objects.filter(quantity__lt=0).exists())

    def test_the_guard_is_in_the_update_itself_not_in_a_python_check(self):
        """
        Read the quantity, let somebody else take it, then write: the write is
        what must refuse. `apply_stock_change` is called with a record whose
        in-memory quantity is a lie by the time it lands.
        """
        from apps.inventory.services import apply_stock_change

        record = StockRecord.objects.get(batch=self.batch, location=self.pharmacy)
        self.assertEqual(record.quantity, 1)

        # Somebody else empties the shelf. `record` still says 1.
        transfer_stock(source=self.pharmacy, destination=self.store,
                       lines=[{"batch": self.batch, "quantity": 1}], actor=self.pharmacist)
        self.assertEqual(record.quantity, 1)

        before = StockMovement.objects.count()
        with self.assertRaises(InsufficientStockError):
            apply_stock_change(record, -1, reason="prescription", actor=self.pharmacist)
        # Refused on the stale figure, and no movement written for it.
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(
            StockRecord.objects.get(pk=record.pk).quantity, 0)


class FefoSurvivesIt(Counter):
    """Locking changed which rows are held, never which batch is chosen."""

    def setUp(self):
        super().setUp()
        # self.batch (B1) expires in 180 days and holds 1. Add a sooner one
        # holding 1, and a later one holding 5.
        self.sooner = stock_the_pharmacy(item=self.item, quantity=1, actor=self.pharmacist,
                                         batch_no="SOON", expiry_days=30)
        self.later = stock_the_pharmacy(item=self.item, quantity=5, actor=self.pharmacist,
                                        batch_no="LATER", expiry_days=365)

    def _held(self, batch):
        return StockRecord.objects.get(batch=batch, location=self.pharmacy).quantity

    def test_one_unit_comes_off_the_soonest_expiring_batch(self):
        script = self.script(self.doctor_a, self.patient_a)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        self.assertEqual(self._held(self.sooner), 0)
        self.assertEqual(self._held(self.batch), 1)
        self.assertEqual(self._held(self.later), 5)
        self.assertEqual(self.movements().get().batch, self.sooner)

    def test_two_dispenses_walk_the_batches_in_expiry_order(self):
        first = self.script(self.doctor_a, self.patient_a, quantity=2)
        second = self.script(self.doctor_b, self.patient_b, quantity=3)

        dispense_prescription(prescription=first, pharmacist=self.pharmacist)
        self.assertEqual((self._held(self.sooner), self._held(self.batch),
                          self._held(self.later)), (0, 0, 5))

        dispense_prescription(prescription=second, pharmacist=self.pharmacist)
        self.assertEqual(self._held(self.later), 2)
        # Nothing was taken twice: seven units in, five out, two left.
        self.assertEqual(self.on_shelf(), 2)

    def test_a_line_spanning_batches_is_all_or_nothing(self):
        """Six requested against seven held, with six vanishing mid-dispense:
        the part already taken goes back rather than half-filling the script."""
        script = self.script(self.doctor_a, self.patient_a, quantity=6)
        thief = self.script(self.doctor_b, self.patient_b, quantity=6)

        from apps.pharmacy import services as pharmacy_services
        real = pharmacy_services.apply_stock_change
        state = {"calls": 0}

        def interleaved(record, delta, **kwargs):
            state["calls"] += 1
            if state["calls"] == 2:          # after the first batch has been taken
                dispense_prescription(prescription=thief, pharmacist=self.pharmacist)
            return real(record, delta, **kwargs)

        with mock.patch.object(pharmacy_services, "apply_stock_change", interleaved):
            with self.assertRaises(OutOfStockError):
                dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        script.refresh_from_db()
        self.assertEqual(script.status, "pending")
        # The first batch had already been taken when the shelf emptied under
        # it. None of that survives: no movement carries this script's
        # reference, so it half-filled nothing. (As above, the interleaved
        # dispense shares this connection's transaction, so only the refused
        # script's own footprint is asserted here.)
        self.assertFalse(
            StockMovement.objects.filter(reference=f"prescription:{script.pk}").exists())
        self.assertFalse(Charge.objects.filter(patient=self.patient_a).exists())
        self.assertFalse(StockRecord.objects.filter(quantity__lt=0).exists())


class TheLocationsStayApart(Counter):
    """Rule 30: the Main Store is not the counter, and never fills for it."""

    def test_stock_in_the_main_store_cannot_satisfy_a_dispense(self):
        stock_the_store(item=self.item, quantity=500, actor=self.pharmacist, batch_no="B-STORE")
        script = self.script(self.doctor_a, self.patient_a)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)  # takes the shelf's 1

        # The shelf is empty; the building holds 500.
        self.assertEqual(self.on_shelf(), 0)
        with self.assertRaises(OutOfStockError) as caught:
            create_prescription(patient=self.patient_b, doctor=self.doctor_b,
                                item=self.item, quantity=1)
        self.assertIn("0 unit(s)", " ".join(caught.exception.messages))
        self.assertEqual(StockRecord.objects.get(batch__batch_no="B-STORE").quantity, 500)

    def test_a_dispense_deducts_the_pharmacy_shelf_and_leaves_the_store_alone(self):
        stock_the_store(item=self.item, quantity=500, actor=self.pharmacist, batch_no="B-STORE")
        script = self.script(self.doctor_a, self.patient_a)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        self.assertEqual(StockRecord.objects.get(batch=self.batch,
                                                 location=self.pharmacy).quantity, 0)
        self.assertEqual(StockRecord.objects.get(batch__batch_no="B-STORE",
                                                 location=self.store).quantity, 500)
        self.assertEqual(self.movements().get().location, self.pharmacy)

    def test_a_transfer_still_moves_stock_between_the_two(self):
        """The conditional deduction did not change what a transfer does."""
        batch = stock_the_store(item=self.item, quantity=500, actor=self.pharmacist,
                                batch_no="B-STORE")
        transfer = transfer_stock(source=self.store, destination=self.pharmacy,
                                  lines=[{"batch": batch, "quantity": 100}],
                                  actor=self.pharmacist)

        self.assertEqual(StockRecord.objects.get(batch=batch, location=self.store).quantity, 400)
        self.assertEqual(StockRecord.objects.get(batch=batch, location=self.pharmacy).quantity, 100)
        self.assertEqual(
            StockMovement.objects.filter(transfer=transfer).count(), 2)

    def test_a_transfer_of_more_than_the_source_holds_is_refused_whole(self):
        batch = stock_the_store(item=self.item, quantity=5, actor=self.pharmacist,
                                batch_no="B-STORE")
        with self.assertRaises(InsufficientStockError):
            transfer_stock(source=self.store, destination=self.pharmacy,
                           lines=[{"batch": batch, "quantity": 6}], actor=self.pharmacist)
        self.assertEqual(StockRecord.objects.get(batch=batch, location=self.store).quantity, 5)
        self.assertFalse(StockRecord.objects.filter(batch=batch,
                                                    location=self.pharmacy,
                                                    quantity__gt=0).exists())


class OverTheApi(Counter):
    """The counter's own experience: a 400 it can read, never a 500."""

    def test_the_second_dispense_answers_400_with_the_figures(self):
        first = self.script(self.doctor_a, self.patient_a)
        second = self.script(self.doctor_b, self.patient_b)

        api = APIClient()
        api.force_authenticate(self.pharmacist)
        ok = api.post(f"/api/prescriptions/{first.pk}/dispense/")
        self.assertEqual(ok.status_code, 200)

        refused = api.post(f"/api/prescriptions/{second.pk}/dispense/")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.data["code"], "insufficient_stock")
        self.assertEqual(refused.data["available"], 0)
        self.assertEqual(refused.data["requested"], 1)
        self.assertIn("Insufficient stock", refused.data["detail"])

    def test_an_already_dispensed_script_is_a_different_answer(self):
        """So the screen can tell "somebody beat me to the stock" from
        "somebody already filled this very script"."""
        stock_the_pharmacy(item=self.item, quantity=5, actor=self.pharmacist, batch_no="B-EXTRA")
        script = self.script(self.doctor_a, self.patient_a)

        api = APIClient()
        api.force_authenticate(self.pharmacist)
        api.post(f"/api/prescriptions/{script.pk}/dispense/")
        again = api.post(f"/api/prescriptions/{script.pk}/dispense/")

        self.assertEqual(again.status_code, 400)
        self.assertNotIn("code", again.data)
        self.assertIn("already dispensed", again.data["detail"])


class TheMoneyIsUnchanged(Counter):
    """Dispensing still raises exactly the charge it always did (rule 6)."""

    def test_a_successful_dispense_charges_the_patient_once_at_the_batch_price(self):
        script = self.script(self.doctor_a, self.patient_a)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        charge = Charge.objects.get(source_type="prescription")
        script.refresh_from_db()
        self.assertEqual(charge.patient, self.patient_a)
        self.assertEqual(charge.amount, Decimal("20"))      # 1 × sale_price
        self.assertEqual(script.dispensed_value, Decimal("20"))
        self.assertEqual(charge.department.code, "pharmacy")
        self.assertEqual(charge.source_id, script.pk)


class ThePosSharesTheProtection(Counter):
    """
    The till moves stock through `consume_fefo`, which deducts through the same
    `apply_stock_change` — so it inherits this without a second implementation
    (rule 39).
    """

    def test_the_till_cannot_sell_a_unit_the_counter_has_dispensed(self):
        from apps.inventory.services import consume_fefo

        script = self.script(self.doctor_a, self.patient_a)
        dispense_prescription(prescription=script, pharmacist=self.pharmacist)

        with self.assertRaises(InsufficientStockError):
            consume_fefo(item=self.item, quantity=1, location=self.pharmacy,
                         actor=self.pharmacist, reason="sale", reference="POS-1")
        self.assertFalse(StockMovement.objects.filter(reason="sale").exists())

    def test_the_till_takes_fefo_and_leaves_the_shelf_consistent(self):
        from apps.inventory.services import consume_fefo

        sooner = stock_the_pharmacy(item=self.item, quantity=2, actor=self.pharmacist,
                                    batch_no="SOON", expiry_days=10)
        pieces = consume_fefo(item=self.item, quantity=3, location=self.pharmacy,
                              actor=self.pharmacist, reason="sale", reference="POS-1")

        self.assertEqual(pieces[0]["batch"], sooner)
        self.assertEqual(pieces[0]["quantity"], 2)
        self.assertEqual(pieces[1]["batch"], self.batch)
        self.assertEqual(self.on_shelf(), 0)


@skipUnless(connection.vendor == "postgresql",
            "Real row locks. On SQLite select_for_update compiles away and the "
            "conditional UPDATE is held deterministically in TheDeductionIsIndivisible.")
class TwoPharmacistsAtOnce(TransactionTestCase):
    """The genuine race, where the database can run one."""

    def test_only_one_of_two_simultaneous_dispenses_takes_the_last_unit(self):
        reception = User.objects.create_user(username="rec2", password="t", role="reception")
        pharmacist = User.objects.create_user(username="ph2", password="t", role="pharmacist")
        doctor = User.objects.create_user(username="doc2", password="t", role="doctor")
        item = product("Paracetamol", unit_name="tablet")
        stock_the_pharmacy(item=item, quantity=1, actor=pharmacist)

        scripts = []
        for name in ("Ann", "Ben"):
            patient = Patient.objects.create(first_name=name, last_name="Race", sex="F",
                                             created_by=reception)
            scripts.append(create_prescription(patient=patient, doctor=doctor,
                                               item=item, quantity=1))

        barrier = threading.Barrier(2)
        outcomes = []

        def attempt(script):
            from django.db import connection as thread_connection
            try:
                barrier.wait()
                dispense_prescription(
                    prescription=Prescription.objects.get(pk=script.pk),
                    pharmacist=pharmacist)
                outcomes.append("ok")
            except Exception as exc:          # the loser's refusal, whatever shape
                outcomes.append(type(exc).__name__)
            finally:
                thread_connection.close()

        threads = [threading.Thread(target=attempt, args=(s,)) for s in scripts]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=30)

        self.assertEqual(outcomes.count("ok"), 1, outcomes)
        self.assertEqual(available_quantity(item, StockLocation.objects.get(code=PHARMACY)), 0)
        self.assertEqual(
            StockMovement.objects.filter(batch__item=item, reason="prescription").count(), 1)
        self.assertEqual(Prescription.objects.filter(status="dispensed").count(), 1)
        self.assertEqual(Charge.objects.filter(source_type="prescription").count(), 1)
        self.assertFalse(StockRecord.objects.filter(quantity__lt=0).exists())
