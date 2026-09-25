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
        paired = ["distance", "corrected", "near", "pinhole",
                  "sphere", "cylinder", "axis", "add", "refraction",
                  "iop", "pupil", "pupil_reaction",
                  "lids", "conjunctiva", "sclera", "cornea", "anterior_chamber", "iris_pupil", "lens",
                  "optic_disc", "macula", "vessels", "retina", "posterior_other",
                  # Asked of each eye, from the manual consultation sheet.
                  "contrast", "colour_vision", "cdr"]
        expected = {f"{name}_{eye}" for name in paired for eye in ("right", "left")}
        expected |= {"complaints", "complaint_other", "complaint_duration", "complaint_onset",
                     "complaint_severity", "associated_symptoms",
                     "ocular_history", "ocular_history_notes",
                     "iop_method", "rapd", "eye_movements", "visual_fields", "follow_up",
                     # The manual sheet's history questions, asked once.
                     "family_history_blindness", "hypertension", "diabetes",
                     "eye_drops", "eye_drops_details", "eye_drops_frequency",
                     "symptom_eye_pain", "symptom_eye_redness", "symptom_eye_itching"}
        self.assertEqual(set(FIELDS), expected)

    def test_the_manual_sheets_questions_are_in_the_definition(self):
        """
        The fifteen the paper consultation sheet asks for and the form did
        not. They are listed here as well as in `SECTIONS` on purpose — this
        is the drift detector for the file above it.
        """
        for added in ("family_history_blindness", "hypertension", "diabetes",
                      "eye_drops", "eye_drops_details", "eye_drops_frequency",
                      "symptom_eye_pain", "symptom_eye_redness", "symptom_eye_itching",
                      "contrast_right", "contrast_left",
                      "colour_vision_right", "colour_vision_left",
                      "cdr_right", "cdr_left"):
            self.assertIn(added, FIELDS, added)
        # Everything the consultation note already carries stays on the note.
        # The general medical history, drugs and allergies stay on the nine
        # health-record tiles — there is no second copy of either here.
        for already_recorded in ("chief_complaint", "diagnosis", "plan", "note_text", "history",
                                 "allergies", "medications", "medical_conditions"):
            self.assertNotIn(already_recorded, FIELDS)

    def test_a_complaint_list_keeps_what_was_ticked_and_refuses_what_is_not_on_it(self):
        self.assertEqual(
            clean_eye_examination({"complaints": ["redness", "blurred_vision", "redness"],
                                   "ocular_history": ["glaucoma"]}),
            # Deduplicated, and in the catalogue's own order.
            {"complaints": ["blurred_vision", "redness"], "ocular_history": ["glaucoma"]})
        # Nothing ticked is nothing stored, not an empty list on the record.
        self.assertIsNone(clean_eye_examination({"complaints": [], "ocular_history": None}))
        with self.assertRaises(ValidationError) as caught:
            clean_eye_examination({"complaints": ["redness", "toothache"]})
        self.assertEqual(list(caught.exception.message_dict), ["complaints"])
        self.assertIn("toothache", caught.exception.message_dict["complaints"][0])
        # A complaint list is a list, and a stray number in one is refused.
        with self.assertRaises(ValidationError):
            clean_eye_examination({"complaints": [7]})

    def test_each_choice_field_is_held_to_its_own_options(self):
        self.assertEqual(
            clean_eye_examination({"complaint_onset": "sudden", "complaint_severity": "severe",
                                   "pupil_right": "abnormal", "pupil_reaction_left": "not_assessed",
                                   "rapd": "present", "iop_method": "tonopen"}),
            {"complaint_onset": "sudden", "complaint_severity": "severe", "pupil_right": "abnormal",
             "pupil_reaction_left": "not_assessed", "rapd": "present", "iop_method": "tonopen"})
        # A value from a *different* field's list is still not on this one's.
        for payload in ({"complaint_onset": "severe"}, {"rapd": "normal"},
                        {"pupil_right": "present"}, {"complaint_severity": "sudden"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                clean_eye_examination(payload)

    def test_refraction_is_written_the_way_it_is_prescribed(self):
        # Text, not a number input: "+1.25" loses its sign to one of those.
        self.assertEqual(
            clean_eye_examination({"sphere_right": "+1.25", "cylinder_right": "-0.50",
                                   "axis_right": "180", "add_left": "+2.00"}),
            {"sphere_right": "+1.25", "cylinder_right": "-0.50", "axis_right": "180",
             "add_left": "+2.00"})

    def test_every_choice_field_the_form_is_served_actually_offers_options(self):
        for section in schema()["sections"]:
            for spec in [*section["rows"], *section["fields"]]:
                if spec["kind"] in ("choice", "multi"):
                    with self.subTest(field=spec.get("key") or spec.get("name")):
                        self.assertTrue(spec["choices"])

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

    def test_the_examination_is_never_silently_overwritten(self):
        """
        The note still locks on save, so the examination cannot be changed by
        writing over it. What the eye doctor may do is *amend* their own note,
        which is refused without a reason and archives what it said before —
        see `test_note_amendments.py` for the rule in full.
        """
        note_id = self.write(self.eye_doctor, eye_examination={"iop_right": 20}).data["id"]
        self.assertTrue(ConsultationNote.objects.get(pk=note_id).is_locked)

        silent = self.as_(self.eye_doctor).patch(
            f"/api/notes/{note_id}/", {"eye_examination": {"iop_right": 12}}, format="json")
        self.assertEqual((silent.status_code, silent.data["code"]),
                         (400, "amendment_reason_required"))
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).eye_examination, {"iop_right": 20})

        amended = self.as_(self.eye_doctor).patch(
            f"/api/notes/{note_id}/",
            {"eye_examination": {"iop_right": 12}, "amendment_reason": "correction"},
            format="json")
        self.assertEqual(amended.status_code, 200, amended.data)
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).eye_examination, {"iop_right": 12})
        self.assertEqual(ConsultationNoteAmendment.objects.get(note_id=note_id)
                         .previous_eye_examination, {"iop_right": 20})

    def test_another_clinician_cannot_amend_the_eye_doctors_examination(self):
        note_id = self.write(self.eye_doctor, eye_examination={"iop_right": 20}).data["id"]
        refused = self.as_(self.doctor).patch(
            f"/api/notes/{note_id}/",
            {"eye_examination": {"iop_right": 12}, "amendment_reason": "correction"},
            format="json")
        self.assertEqual(refused.status_code, 403, refused.data)
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).eye_examination, {"iop_right": 20})

    def test_an_admin_amendment_archives_the_examination_with_the_rest_of_the_note(self):
        note_id = self.write(self.eye_doctor, diagnosis="Cataract",
                             eye_examination={"lens_right": "Dense NS 3+"}).data["id"]
        response = self.as_(self.admin).patch(
            f"/api/notes/{note_id}/",
            {"diagnosis": "Cataract, right", "eye_examination": {"lens_right": "NS 2+"},
             "amendment_reason": "correction"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

        amendment = ConsultationNoteAmendment.objects.get(note_id=note_id)
        self.assertEqual((amendment.previous_diagnosis, amendment.previous_eye_examination),
                         ("Cataract", {"lens_right": "Dense NS 3+"}))
        # The amendment says who and why, not only what it used to hold.
        self.assertEqual((amendment.amended_by, amendment.reason), (self.admin, "correction"))
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).eye_examination, {"lens_right": "NS 2+"})
        # The eye doctor reads the archive of their own note.
        archive = self.as_(self.eye_doctor).get("/api/note-amendments/", {"note": note_id}).data
        rows = archive["results"] if isinstance(archive, dict) else archive
        self.assertEqual(rows[0]["previous_eye_examination"], {"lens_right": "Dense NS 3+"})

    def test_a_whole_structured_eye_consultation_saves_with_most_of_it_left_blank(self):
        """The acceptance scenario: the eye doctor fills in what they
        examined — complaint, history, acuity, refraction, pressure, pupils,
        both segments — leaves the rest empty, and the note takes it."""
        response = self.write(
            self.eye_doctor,
            chief_complaint="Blurred vision, both eyes, 3 months",
            note_text="Gradual painless loss of vision. No trauma.",
            diagnosis="Immature senile cataract, both eyes",
            plan="Book for phacoemulsification. Review in 2 weeks.",
            eye_examination={
                "complaints": ["blurred_vision", "photophobia"],
                "complaint_duration": "3 months", "complaint_onset": "gradual",
                "complaint_severity": "moderate",
                "ocular_history": ["previous_glasses", "cataract"],
                "distance_right": "6/24", "distance_left": "6/36",
                "corrected_right": "6/18", "pinhole_right": "6/12", "near_left": "N18",
                "sphere_right": "+1.50", "cylinder_right": "-0.75", "axis_right": "90",
                "iop_right": "16", "iop_left": "17", "iop_method": "applanation",
                "pupil_right": "normal", "pupil_reaction_right": "normal", "rapd": "absent",
                "cornea_right": "Clear", "lens_right": "NS 2+", "lens_left": "NS 3+",
                "optic_disc_right": "CDR 0.3", "macula_left": "",
                "vessels_right": None, "follow_up": "2 weeks",
            })
        self.assertEqual(response.status_code, 201, response.data)
        exam = ConsultationNote.objects.get(pk=response.data["id"]).eye_examination
        self.assertEqual(exam["complaints"], ["blurred_vision", "photophobia"])
        self.assertEqual(exam["ocular_history"], ["cataract", "previous_glasses"])
        self.assertEqual((exam["iop_right"], exam["sphere_right"], exam["rapd"]),
                         (16, "+1.50", "absent"))
        # Blanks were dropped rather than stored as empty findings.
        for untouched in ("macula_left", "vessels_right", "posterior_other_right", "visual_fields"):
            self.assertNotIn(untouched, exam)

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


class TheManualSheetsMissingQuestions(TestCase):
    """
    The history and examination inputs the paper consultation sheet asks for
    and the form did not.

    **Added to `clinical/eye_exam.py` and nowhere else.** The examination is a
    JSON field on the consultation note, so there is no migration, no second
    model and no second copy of the field list in JavaScript — the form reads
    `GET /api/notes/eye-examination-fields/` (rule 43). Every existing field
    stays exactly where it was.
    """

    def setUp(self):
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi",
                                              sex="F")
        self.eye_doctor = User.objects.create_user(
            username="eyedoc", password="t", role="ophthalmologist")
        visit = Visit.objects.create(patient=self.patient, opened_by=self.eye_doctor,
                                     attending_doctor=self.eye_doctor)
        PatientRoute.objects.create(
            visit=visit, department=Department.objects.get(code="eye"),
            purpose="eye", assigned_to=self.eye_doctor, routed_by=self.eye_doctor)
        self.client_ = APIClient()
        self.client_.force_authenticate(self.eye_doctor)

    def write(self, examination):
        return self.client_.post("/api/notes/", {
            "patient": self.patient.pk, "visit_time": timezone.now().isoformat(),
            "reason_for_visit": "Eye check", "chief_complaint": "Eye check",
            "eye_examination": examination}, format="json")

    def stored(self, response):
        return ConsultationNote.objects.get(pk=response.data["id"]).eye_examination

    # --- history -------------------------------------------------------
    def test_the_systemic_history_questions_save(self):
        answer = self.write({"family_history_blindness": "yes",
                             "hypertension": "no",
                             "diabetes": "unknown"})
        self.assertEqual(answer.status_code, 201, answer.data)
        self.assertEqual(self.stored(answer), {"family_history_blindness": "yes",
                                               "hypertension": "no",
                                               "diabetes": "unknown"})

    def test_each_of_them_takes_yes_no_or_unknown_and_nothing_else(self):
        for field in ("family_history_blindness", "hypertension", "diabetes"):
            for value in ("yes", "no", "unknown"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.write({field: value}).status_code, 201)
            with self.subTest(field=field, value="maybe"):
                self.assertEqual(self.write({field: "maybe"}).status_code, 400)

    def test_unknown_is_a_finding_and_is_kept(self):
        """It is not a blank: "nobody knows" and "nobody asked" differ."""
        answer = self.write({"diabetes": "unknown"})
        self.assertEqual(self.stored(answer), {"diabetes": "unknown"})

    def test_the_eye_symptoms_save_beside_the_presenting_complaint(self):
        answer = self.write({
            "complaints": ["blurred_vision"],
            "symptom_eye_pain": "yes",
            "symptom_eye_redness": "no",
            "symptom_eye_itching": "unknown"})
        self.assertEqual(answer.status_code, 201, answer.data)
        stored = self.stored(answer)
        # The complaint selector is untouched — these are asked *as well*.
        self.assertEqual(stored["complaints"], ["blurred_vision"])
        self.assertEqual(stored["symptom_eye_pain"], "yes")
        self.assertEqual(stored["symptom_eye_redness"], "no")
        self.assertEqual(stored["symptom_eye_itching"], "unknown")

    def test_the_existing_complaint_options_are_unchanged(self):
        from apps.clinical.eye_exam import EYE_COMPLAINTS

        values = [value for value, _ in EYE_COMPLAINTS]
        self.assertIn("eye_pain", values, "the complaint list lost an option")
        self.assertIn("blurred_vision", values)

    # --- eye drops -----------------------------------------------------
    def test_the_eye_drop_question_and_its_details_are_separate_fields(self):
        answer = self.write({"eye_drops": "yes",
                             "eye_drops_details": "Timolol 0.5%",
                             "eye_drops_frequency": "twice_daily"})
        self.assertEqual(answer.status_code, 201, answer.data)
        self.assertEqual(self.stored(answer), {"eye_drops": "yes",
                                               "eye_drops_details": "Timolol 0.5%",
                                               "eye_drops_frequency": "twice_daily"})

    def test_the_frequency_offers_the_three_the_sheet_asks_for(self):
        for value in ("once_daily", "twice_daily", "other"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.write({"eye_drops": "yes",
                                "eye_drops_frequency": value}).status_code, 201)
        self.assertEqual(self.write({"eye_drops_frequency": "hourly"}).status_code, 400)

    def test_the_details_are_revealed_by_the_answer_above_them(self):
        """
        A **display** rule, served on the field so the form holds no copy —
        and deliberately not a validation one. Nothing in an eye examination
        is mandatory, and a detail typed before the answer was changed is a
        finding somebody recorded rather than an error to reject.
        """
        schema = self.client_.get("/api/notes/eye-examination-fields/").data
        history = next(s for s in schema["sections"] if s["key"] == "eye_history")
        by_key = {f["key"]: f for f in history["fields"]}
        for field in ("eye_drops_details", "eye_drops_frequency"):
            self.assertEqual(by_key[field]["revealed_by"],
                             {"field": "eye_drops", "values": ["yes"]})
        self.assertNotIn("revealed_by", by_key["eye_drops"])
        # And the server still stores them whatever the answer says.
        self.assertEqual(self.write({"eye_drops": "no",
                                     "eye_drops_details": "Stopped last week"}
                                    ).status_code, 201)

    # --- examination ---------------------------------------------------
    def test_contrast_is_recorded_for_each_eye(self):
        answer = self.write({"contrast_right": "1.25 log units",
                             "contrast_left": "0.90 log units"})
        self.assertEqual(answer.status_code, 201, answer.data)
        self.assertEqual(self.stored(answer), {"contrast_right": "1.25 log units",
                                               "contrast_left": "0.90 log units"})

    def test_colour_vision_is_recorded_for_each_eye(self):
        answer = self.write({"colour_vision_right": "14/14 Ishihara",
                             "colour_vision_left": "9/14 Ishihara"})
        self.assertEqual(answer.status_code, 201, answer.data)
        self.assertEqual(self.stored(answer), {"colour_vision_right": "14/14 Ishihara",
                                               "colour_vision_left": "9/14 Ishihara"})

    def test_cdr_is_recorded_for_each_eye_and_is_not_the_optic_disc(self):
        answer = self.write({"cdr_right": "0.3", "cdr_left": "0.7",
                             "optic_disc_right": "Pink, well defined margins"})
        self.assertEqual(answer.status_code, 201, answer.data)
        stored = self.stored(answer)
        self.assertEqual(stored["cdr_right"], "0.3")
        self.assertEqual(stored["cdr_left"], "0.7")
        self.assertEqual(stored["optic_disc_right"], "Pink, well defined margins")

    def test_cdr_takes_how_a_clinician_writes_it(self):
        """A ratio, and sometimes two of them — so text, like refraction."""
        for written in ("0.3", "0.65", "0.6 H / 0.7 V", "0.9 (glaucomatous)"):
            with self.subTest(written=written):
                self.assertEqual(self.write({"cdr_right": written}).status_code, 201)

    def test_cdr_offers_no_options_because_none_were_invented(self):
        from apps.clinical.eye_exam import FIELDS, OPTIONS

        self.assertEqual(FIELDS["cdr_right"]["kind"], "short")
        self.assertIsNone(FIELDS["cdr_right"]["options"])
        self.assertNotIn("cdr", OPTIONS)

    # --- the form reads it, and nothing else changed --------------------
    def test_the_form_is_served_every_new_field(self):
        schema = self.client_.get("/api/notes/eye-examination-fields/").data
        keys = set()
        for section in schema["sections"]:
            keys.update(f["key"] for f in section["fields"])
            for row in section["rows"]:
                keys.update({row["right"], row["left"]})
        for expected in ("family_history_blindness", "hypertension", "diabetes",
                         "eye_drops", "eye_drops_details", "eye_drops_frequency",
                         "symptom_eye_pain", "symptom_eye_redness",
                         "symptom_eye_itching", "contrast_right", "contrast_left",
                         "colour_vision_right", "colour_vision_left",
                         "cdr_right", "cdr_left"):
            self.assertIn(expected, keys, expected)

    def test_every_field_the_form_had_is_still_there(self):
        from apps.clinical.eye_exam import FIELDS

        for kept in ("complaints", "complaint_other", "complaint_duration",
                     "complaint_onset", "complaint_severity", "associated_symptoms",
                     "ocular_history", "ocular_history_notes", "distance_right",
                     "corrected_left", "pinhole_right", "near_left", "sphere_right",
                     "refraction_left", "iop_right", "iop_method", "pupil_right",
                     "pupil_reaction_left", "rapd", "lids_right", "conjunctiva_left",
                     "sclera_right", "cornea_left", "anterior_chamber_right",
                     "iris_pupil_left", "lens_right", "optic_disc_left",
                     "macula_right", "vessels_left", "retina_right",
                     "posterior_other_left", "eye_movements", "visual_fields",
                     "follow_up"):
            self.assertIn(kept, FIELDS, f"{kept} was removed from the eye examination")

    def test_a_note_written_before_these_fields_existed_still_reads(self):
        """Existing records stay valid — nothing became required."""
        answer = self.write({"distance_right": "6/6", "iop_right": "14"})
        self.assertEqual(answer.status_code, 201, answer.data)
        note = ConsultationNote.objects.get(pk=answer.data["id"])
        self.assertEqual(note.eye_examination, {"distance_right": "6/6",
                                                "iop_right": 14})
        read = self.client_.get(f"/api/notes/{note.pk}/")
        self.assertEqual(read.status_code, 200)

    def test_nothing_new_is_mandatory(self):
        """An examination of one finding still saves (rule 20's direction)."""
        self.assertEqual(self.write({"hypertension": "yes"}).status_code, 201)
        self.assertEqual(self.write({}).status_code, 201)

    def test_a_blank_is_still_dropped_rather_than_stored(self):
        answer = self.write({"hypertension": "yes", "diabetes": "",
                             "cdr_right": "   ", "eye_drops_details": ""})
        self.assertEqual(self.stored(answer), {"hypertension": "yes"})

    def test_an_unknown_key_is_still_refused(self):
        self.assertEqual(self.write({"blood_group": "O+"}).status_code, 400)

    def test_the_new_findings_read_back_through_their_labels(self):
        from apps.clinical.eye_exam import describe

        described = describe({"family_history_blindness": "yes", "cdr_right": "0.3",
                              "contrast_left": "0.9"})
        rendered = str(described)
        self.assertIn("Family history of blindness", rendered)
        self.assertIn("Cup-to-disc ratio", rendered)
        self.assertIn("Contrast", rendered)
