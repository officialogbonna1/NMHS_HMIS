"""
The eye examination is part of the consultation note (`clinical/eye_exam.py`):
checked against its one definition, locked with the note, archived with it by
an amendment, and absent from a general doctor's note.
"""
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.eye_exam import FIELDS, clean_eye_examination, schema
from apps.clinical.models import ConsultationNote, ConsultationNoteAmendment
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class CleaningTests(SimpleTestCase):
    def test_blanks_are_dropped_and_an_empty_examination_is_none(self):
        self.assertIsNone(clean_eye_examination(None))
        self.assertIsNone(clean_eye_examination({}))
        self.assertIsNone(clean_eye_examination({"distance_right": "  ", "iop_left": ""}))
        self.assertEqual(clean_eye_examination({"distance_right": " 6/9 ", "near_left": "", "lens_right": None}),
                         {"distance_right": "6/9"})

    def test_pressure_is_a_number_a_tonometer_can_read(self):
        self.assertEqual(clean_eye_examination({"iop_right": "14", "iop_left": 15.25}),
                         {"iop_right": 14, "iop_left": 15.3})
        self.assertEqual(clean_eye_examination({"iop_right": 0, "iop_left": "80"}),
                         {"iop_right": 0, "iop_left": 80})
        for bad in ("-1", 81, "80.1", "high", True, [14], "nan", "inf"):
            with self.subTest(value=bad), self.assertRaises(ValidationError) as caught:
                clean_eye_examination({"iop_right": bad})
            self.assertEqual(list(caught.exception.message_dict), ["iop_right"])

    def test_an_unknown_field_is_refused_by_name(self):
        with self.assertRaises(ValidationError) as caught:
            clean_eye_examination({"distance_right": "6/6", "diagnosis": "Glaucoma"})
        self.assertEqual(list(caught.exception.message_dict), ["diagnosis"])

    def test_the_method_comes_from_the_list_and_findings_are_text_of_a_sane_length(self):
        self.assertEqual(clean_eye_examination({"iop_method": "rebound"}), {"iop_method": "rebound"})
        for payload in ({"iop_method": "guess"}, {"cornea_right": 12}, {"lens_left": "x" * 1001},
                        {"distance_left": "x" * 61}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                clean_eye_examination(payload)

    def test_it_has_to_be_a_set_of_findings(self):
        for bad in (["6/6"], "6/6", 12):
            with self.subTest(value=bad), self.assertRaises(ValidationError):
                clean_eye_examination(bad)

    def test_the_definition_holds_the_agreed_fields_and_nothing_the_note_already_has(self):
        paired = ["distance", "near", "pinhole", "refraction", "iop", "lids", "conjunctiva", "cornea",
                  "anterior_chamber", "iris_pupil", "lens", "optic_disc", "macula", "retina"]
        expected = {f"{name}_{eye}" for name in paired for eye in ("right", "left")}
        expected |= {"iop_method", "eye_movements", "visual_fields", "follow_up"}
        self.assertEqual(set(FIELDS), expected)
        for already_on_the_note in ("chief_complaint", "diagnosis", "plan", "note_text", "history"):
            self.assertNotIn(already_on_the_note, FIELDS)

    def test_the_form_is_served_exactly_the_fields_that_are_validated(self):
        served = schema()
        keys = {row[eye] for section in served["sections"] for row in section["rows"]
                for eye in ("right", "left")}
        keys |= {field["key"] for section in served["sections"] for field in section["fields"]}
        self.assertEqual(keys, set(FIELDS))


class EyeConsultationTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.eye_doctor = User.objects.create_user(username="eye", password="t", role="ophthalmologist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.admin = User.objects.create_user(username="adm", password="t", role="admin")
        department = Department.objects.create(code="eye-unit-test", name="Eye Unit (test)")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                     attending_doctor=self.doctor)
        PatientRoute.objects.create(visit=visit, department=department, purpose="eye",
                                    assigned_to=self.eye_doctor, routed_by=self.reception,
                                    status="in_progress")

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def write(self, user, patient=None, **fields):
        body = {"patient": (patient or self.patient).id, "visit_time": timezone.now().isoformat(),
                "reason_for_visit": "Blurred vision", **fields}
        return self.as_(user).post("/api/notes/", body, format="json")

    def test_the_eye_doctor_saves_only_what_they_examined(self):
        response = self.write(
            self.eye_doctor, chief_complaint="Blurred vision, right eye",
            diagnosis="Primary open-angle glaucoma", plan="Timolol; review in 4 weeks",
            eye_examination={"distance_right": "6/18", "distance_left": "6/9", "iop_right": "28",
                             "iop_left": 17, "iop_method": "applanation",
                             "optic_disc_right": "CDR 0.8", "cornea_left": "", "lens_right": None})
        self.assertEqual(response.status_code, 201, response.data)
        note = ConsultationNote.objects.get(pk=response.data["id"])
        self.assertEqual(note.eye_examination, {
            "distance_right": "6/18", "distance_left": "6/9", "iop_right": 28, "iop_left": 17,
            "iop_method": "applanation", "optic_disc_right": "CDR 0.8"})
        # The consultation's own fields carry the history, diagnosis and plan.
        self.assertEqual((note.chief_complaint, note.diagnosis, note.doctor),
                         ("Blurred vision, right eye", "Primary open-angle glaucoma", self.eye_doctor))
        self.assertTrue(note.is_locked)

    def test_an_invalid_examination_is_refused_and_nothing_is_written(self):
        for exam in ({"iop_right": 95}, {"colour_vision": "normal"}, {"iop_method": "guess"}, ["6/6"]):
            with self.subTest(exam=exam):
                response = self.write(self.eye_doctor, eye_examination=exam)
                self.assertEqual(response.status_code, 400)
                self.assertIn("eye_examination", response.data)
        self.assertFalse(ConsultationNote.objects.exists())

    def test_the_examination_locks_with_the_note(self):
        note_id = self.write(self.eye_doctor, eye_examination={"iop_right": 20}).data["id"]
        response = self.as_(self.eye_doctor).patch(
            f"/api/notes/{note_id}/", {"eye_examination": {"iop_right": 12}}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).eye_examination, {"iop_right": 20})

    def test_an_admin_amendment_archives_the_examination_with_the_rest_of_the_note(self):
        note_id = self.write(self.eye_doctor, diagnosis="Cataract",
                             eye_examination={"lens_right": "Dense NS 3+"}).data["id"]
        response = self.as_(self.admin).patch(
            f"/api/notes/{note_id}/",
            {"diagnosis": "Cataract, right", "eye_examination": {"lens_right": "NS 2+"}}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        amendment = ConsultationNoteAmendment.objects.get(note_id=note_id)
        self.assertEqual((amendment.previous_diagnosis, amendment.previous_eye_examination),
                         ("Cataract", {"lens_right": "Dense NS 3+"}))
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).eye_examination, {"lens_right": "NS 2+"})
        # The eye doctor reads the archive of their own note.
        archive = self.as_(self.eye_doctor).get("/api/note-amendments/", {"note": note_id}).data
        rows = archive["results"] if isinstance(archive, dict) else archive
        self.assertEqual(rows[0]["previous_eye_examination"], {"lens_right": "Dense NS 3+"})

    def test_a_general_doctors_note_is_what_it_was(self):
        response = self.write(self.doctor, note_text="Headache", diagnosis="Tension headache")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(ConsultationNote.objects.get(pk=response.data["id"]).eye_examination)
        self.assertEqual(self.write(self.doctor, eye_examination={"distance_right": "6/6"}).status_code, 400)
        # An empty examination is no examination, not a refusal.
        self.assertEqual(self.write(self.doctor, eye_examination={}).status_code, 201)

    def test_the_eye_doctor_cannot_write_on_a_patient_who_is_not_theirs(self):
        stranger = Patient.objects.create(first_name="Bo", last_name="Eze", sex="M", created_by=self.reception)
        response = self.write(self.eye_doctor, patient=stranger, eye_examination={"iop_right": 14})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ConsultationNote.objects.filter(patient=stranger).exists())

    def test_the_form_reads_the_definition_the_server_validates_against(self):
        for user in (self.eye_doctor, self.doctor):
            response = self.as_(user).get("/api/notes/eye-examination-fields/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, schema())
        self.assertEqual(self.as_(self.reception).get("/api/notes/eye-examination-fields/").status_code, 403)
