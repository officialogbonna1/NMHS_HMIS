"""
The doctor's single view of a patient.

Assembled server-side in one response rather than a dozen frontend
requests, so the chart can't render half-loaded — and so the same access
rules that guard each record type are applied in one place. Every section
is filtered by what the caller is allowed to see: a doctor only gets here
for a patient assigned to them (see PatientViewSet.get_queryset), and the
billing block is omitted for roles with no business reading money.
"""
from django.db import models
from django.db.models import Sum

from apps.appointments.models import Appointment
from apps.billing.models import Charge
from apps.clinical.models import Vitals, ConsultationNote, NursingNote
from apps.pharmacy.models import Prescription
from apps.workflow.models import Visit

BILLING_ROLES = {"admin", "hospital_admin", "cashier", "accountant", "reception", "pharmacist"}

VITAL_FIELDS = [
    ("temperature_c", "Temperature", "°C"),
    ("heart_rate", "Heart rate", "bpm"),
    ("respiratory_rate", "Respiratory rate", "/min"),
    ("sao2", "SaO₂", "%"),
    ("weight_kg", "Weight", "kg"),
    ("height_cm", "Height", "cm"),
    ("glucose_level", "Glucose", "mmol/L"),
]


def _file_url(field):
    """A test with no file on disk must not take the whole chart down."""
    try:
        return field.url if field else None
    except ValueError:
        return None


def _name(user):
    if not user:
        return None
    return user.get_full_name() or user.username


def latest_vitals_summary(patient):
    """The most recent reading, flattened into label/value pairs to render."""
    vitals = patient.vitals.select_related("recorded_by").first()
    if not vitals:
        return None
    readings = [
        {"label": label, "value": getattr(vitals, field), "unit": unit}
        for field, label, unit in VITAL_FIELDS
        if getattr(vitals, field) is not None
    ]
    if vitals.bp_systolic and vitals.bp_diastolic:
        readings.insert(0, {"label": "Blood pressure", "value": f"{vitals.bp_systolic}/{vitals.bp_diastolic}", "unit": "mmHg"})
    return {
        "id": vitals.id,
        "visit_time": vitals.visit_time,
        "recorded_by": _name(vitals.recorded_by),
        "readings": readings,
    }


def build_overview(*, patient, user):
    """Everything attached to this patient, in the order a clinician reads it."""
    allergies = list(patient.allergies.all())
    overview = {
        "patient": {
            "id": patient.id,
            "uuid": str(patient.uuid),
            # Both names, same value: `patient_number` is the field,
            # `file_number` is what the chart already reads.
            "patient_number": patient.patient_number,
            "file_number": patient.patient_number,
            "name": f"{patient.last_name}, {patient.first_name}",
            "sex": patient.get_sex_display(),
            "birthdate": patient.birthdate,
            "age_display": patient.age_display,
            "phone_number": patient.phone_number,
            "city": patient.city,
            "address": ", ".join(
                p for p in [patient.street_address, patient.city, patient.state, patient.country] if p
            ),
            "short_note": patient.short_note,
            # On the chart because the moment it is needed is not the moment
            # to be opening the registration screen.
            "emergency_contact": None if not patient.emergency_contact_name else {
                "name": patient.emergency_contact_name,
                "relationship": patient.emergency_contact_relationship,
                "phone": patient.emergency_contact_phone,
                "alt_phone": patient.emergency_contact_alt_phone,
                "address": patient.emergency_contact_address,
                "notes": patient.emergency_contact_notes,
            },
            "registered": patient.created_at,
        },
        # Allergies lead: they change what the doctor is allowed to prescribe.
        "alerts": [
            {"label": allergy.name, "detail": ", ".join(allergy.reactions or []), "is_dangerous": allergy.is_dangerous}
            for allergy in allergies
        ],
        "counts": {
            "allergies": len(allergies),
            "conditions": patient.conditions.count(),
            "medications": patient.medications.count(),
            "surgeries": patient.surgeries.count(),
            "vaccinations": patient.vaccinations.count(),
            "devices": patient.devices.count(),
            "tests": patient.tests.count(),
            "family_history": patient.family_history.count(),
            "social_history": patient.social_history.count(),
        },
        # The nine tiles in full. A count told the doctor a surgical history
        # existed without saying what it was, which is the half of the answer
        # that matters at the bedside.
        "allergies": [
            {
                "id": a.id, "name": a.name, "reactions": a.reactions or [],
                "is_dangerous": a.is_dangerous, "notes": a.notes,
            }
            for a in allergies
        ],
        "surgeries": [
            {"id": s.id, "name": s.name, "surgery_date": s.surgery_date, "description": s.description}
            for s in patient.surgeries.all()
        ],
        "vaccinations": [
            {
                "id": v.id, "name": v.name, "date_administered": v.date_administered,
                "next_due_date": v.next_due_date, "notes": v.notes,
            }
            for v in patient.vaccinations.all()
        ],
        "devices": [
            {
                "id": d.id, "name": d.name, "make": d.make, "model": d.model,
                "device_id": d.device_id, "date_acquired": d.date_acquired,
                "next_update": d.next_update, "notes": d.notes,
            }
            for d in patient.devices.all()
        ],
        "tests": [
            {
                "id": t.id, "title": t.title, "test_type": t.get_test_type_display(),
                "test_date": t.test_date, "impressions": t.impressions, "notes": t.notes,
                "file_url": _file_url(t.file),
                "file_name": t.file.name.rsplit("/", 1)[-1] if t.file else None,
            }
            for t in patient.tests.all()
        ],
        "family_history": [
            {
                "id": f.id, "relationship": f.relationship, "is_deceased": f.is_deceased,
                "conditions": f.conditions or [], "notes": f.notes,
            }
            for f in patient.family_history.all()
        ],
        "social_history": [
            {
                "id": h.id, "category": h.category, "is_active": h.is_active,
                "frequency": h.frequency, "amount": h.amount,
                "started_year": h.started_year, "notes": h.notes,
            }
            for h in patient.social_history.all()
        ],
        "latest_vitals": latest_vitals_summary(patient),
        "vitals_history": [
            {
                "id": v.id, "visit_time": v.visit_time, "recorded_by": _name(v.recorded_by),
                "temperature_c": v.temperature_c, "heart_rate": v.heart_rate,
                "bp_systolic": v.bp_systolic, "bp_diastolic": v.bp_diastolic,
                "weight_kg": v.weight_kg, "sao2": v.sao2,
            }
            for v in patient.vitals.select_related("recorded_by")[:10]
        ],
        "conditions": [
            {"id": c.id, "name": c.name, "date_diagnosed": c.date_diagnosed, "notes": c.notes}
            for c in patient.conditions.all()
        ],
        "medications": [
            {
                "id": m.id, "name": m.name, "strength": m.strength,
                "dose_frequency": m.dose_frequency, "dose_schedule": m.dose_schedule,
                "consumption_type": m.consumption_type, "as_needed": m.as_needed,
                "notes": m.notes,
            }
            for m in patient.medications.all()
        ],
        "nursing_notes": [
            {
                "id": n.id, "created_at": n.created_at, "nurse": _name(n.nurse),
                "complaint": n.complaint, "observation": n.observation,
            }
            for n in NursingNote.objects.filter(patient=patient).select_related("nurse")[:10]
        ],
        "consultation_notes": [
            {
                "id": n.id, "visit_time": n.visit_time, "doctor": _name(n.doctor),
                "reason_for_visit": n.reason_for_visit, "diagnosis": n.diagnosis,
                "plan": n.plan, "note_text": n.note_text,
            }
            for n in ConsultationNote.objects.filter(patient=patient).select_related("doctor")[:10]
        ],
        "prescriptions": [
            {
                "id": p.id, "item": p.item.name, "quantity": p.quantity, "status": p.status,
                "status_label": p.get_status_display(), "dosage_instructions": p.dosage_instructions,
                "doctor": _name(p.doctor), "created_at": p.created_at,
                "dispensed_at": p.dispensed_at, "dispensed_by": _name(p.dispensed_by),
            }
            for p in Prescription.objects.filter(patient=patient).select_related("item", "doctor", "dispensed_by")[:10]
        ],
        "appointments": [
            {
                "id": a.id, "status": a.status, "status_label": a.get_status_display(),
                "doctor": _name(a.doctor), "reason": a.reason,
                "created_at": a.created_at, "start_time": a.start_time, "end_time": a.end_time,
            }
            for a in Appointment.objects.filter(patient=patient).select_related("doctor").order_by("-created_at")[:10]
        ],
        "visits": [
            {
                "id": v.id, "created_at": v.created_at, "status": v.status,
                "visit_type": v.get_visit_type_display(), "reason": v.reason,
                "attending_doctor": _name(v.attending_doctor),
                "routes": [
                    {
                        "id": r.id, "department": r.department.name, "purpose": r.get_purpose_display(),
                        "status": r.status, "assigned_to": _name(r.assigned_to), "priority": r.priority,
                        "notes": r.notes,
                        # What came back from the lab, imaging or the eye
                        # clinic. A referral the doctor cannot read the
                        # answer to is a patient sent away and lost.
                        "result": r.result, "result_by": _name(r.result_by), "result_at": r.result_at,
                    }
                    for r in v.routes.select_related("department", "assigned_to", "result_by").all()
                ],
            }
            for v in Visit.objects.filter(patient=patient).select_related("attending_doctor").prefetch_related("routes")[:5]
        ],
    }

    if user.role in BILLING_ROLES:
        ledger = getattr(patient, "ledger", None)
        overview["billing"] = {
            "total_charges": ledger.total_charges if ledger else 0,
            "total_payments": ledger.total_payments if ledger else 0,
            "outstanding_balance": ledger.outstanding_balance if ledger else 0,
            "unpaid_count": Charge.objects.filter(patient=patient, status__in=["unpaid", "partial"]).count(),
            "unpaid_total": Charge.objects.filter(patient=patient, status__in=["unpaid", "partial"]).aggregate(
                v=Sum(models.F("amount") - models.F("amount_paid"))
            )["v"] or 0,
        }
    return overview
