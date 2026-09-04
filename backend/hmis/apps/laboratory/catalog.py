"""
The starting laboratory catalogue.

This is *seed data*, not the definition of the module. Everything here lands
in `LabTest` / `LabParameter` rows on first migrate and is the hospital's
from that moment: rename a test, retire it, change a unit, add a parameter —
all from the Laboratory Catalogue page, with no code change and no migration.
Re-running the seed only fills in what is missing (`seed_catalogue` below
never overwrites a row somebody has edited).

Two deliberate choices about reference ranges:

* `ref` is what gets **printed** — free text, because real ranges read
  "12 – 16 (M), 11 – 15 (F)" or "< 200".
* `low` / `high` are what drive the **flag**, and they are left out wherever
  a range depends on sex, age or method. A parameter with no bounds is never
  flagged, which is the safe direction: an unflagged real result is read by
  a doctor, whereas a wrongly flagged one gets acted on.

Nothing here is marked required. The bench enters what it measured.
"""
from decimal import Decimal


def P(code, name, result_type="numeric", unit="", ref="", low=None, high=None,
      options=None, group="", normal=""):
    return {
        "code": code, "name": name, "result_type": result_type, "unit": unit,
        "reference_range": ref, "ref_low": low, "ref_high": high,
        "options": options or [], "group": group, "normal_value": normal,
    }


def T(code, name, category, parameters, price="0", specimen="", container="",
      turnaround=None, description=""):
    return {
        "code": code, "name": name, "category": category, "price": Decimal(price),
        "specimen_type": specimen, "container": container,
        "turnaround_hours": turnaround, "description": description,
        "parameters": parameters,
    }


# --- shared parameter sets ------------------------------------------------

# Culture & sensitivity reads the same whatever the swab is: what grew, what
# it answered to. One shared set, so adding "Intermediate" adds it everywhere.
def CULTURE(default_specimen=""):
    return [
        P("specimen", "Specimen", "text", group="Specimen",
          ref="", normal=default_specimen),
        P("macroscopy", "Macroscopic appearance", "text", group="Specimen"),
        P("culture_result", "Culture result", "select", group="Culture",
          options=["No growth", "Growth", "Mixed growth", "Normal flora",
                   "Contaminated", "Pending"]),
        P("organism", "Organism isolated", "text", group="Culture"),
        P("colony_count", "Colony count", "text", unit="cfu/mL", group="Culture"),
        P("antibiotics_tested", "Antibiotics tested", "text", group="Sensitivity"),
        P("sensitive", "Sensitive to", "text", group="Sensitivity"),
        P("intermediate", "Intermediate", "text", group="Sensitivity"),
        P("resistant", "Resistant to", "text", group="Sensitivity"),
        P("comments", "Comments", "text", group="Sensitivity"),
    ]


SEROLOGY_QUALITATIVE = "Non-reactive"


def SEROLOGY(code, name, result_type="reactive_nonreactive", normal="Non-reactive",
             price="2500", specimen="Serum", extra=None):
    params = [P("result", "Result", result_type, normal=normal)]
    params += extra or []
    params.append(P("method", "Method / kit", "text"))
    return T(code, name, "serology", params, price=price, specimen=specimen,
             container="Plain (red top)", turnaround=4)


def HORMONE(code, name, unit, ref="", low=None, high=None, price="6000"):
    return T(code, name, "endocrinology",
             [P("result", name, "numeric", unit=unit, ref=ref, low=low, high=high),
              P("comment", "Comment", "text")],
             price=price, specimen="Serum", container="Plain (red top)", turnaround=48)


# --- 1. Haematology -------------------------------------------------------

HAEMATOLOGY = [
    T("fbc", "Full Blood Count (FBC)", "haematology", [
        # Hb, PCV and RBC differ by sex; the printed range says so and the
        # bounds are left off rather than flagging half the women anaemic.
        P("hb", "Haemoglobin (Hb)", unit="g/dL", ref="M 13 – 17, F 12 – 15"),
        P("pcv", "Packed Cell Volume (PCV/HCT)", unit="%", ref="M 40 – 52, F 36 – 46"),
        P("rbc", "Red Blood Cell (RBC)", unit="×10¹²/L", ref="M 4.5 – 5.9, F 4.1 – 5.1"),
        P("wbc", "White Blood Cell (WBC)", unit="×10⁹/L", ref="4.0 – 11.0", low=4, high=11),
        P("neutrophils", "Neutrophils", unit="%", ref="40 – 75", low=40, high=75),
        P("lymphocytes", "Lymphocytes", unit="%", ref="20 – 45", low=20, high=45),
        P("monocytes", "Monocytes", unit="%", ref="2 – 10", low=2, high=10),
        P("eosinophils", "Eosinophils", unit="%", ref="1 – 6", low=1, high=6),
        P("basophils", "Basophils", unit="%", ref="0 – 1", low=0, high=1),
        P("platelets", "Platelets", unit="×10⁹/L", ref="150 – 450", low=150, high=450),
        P("mcv", "MCV", unit="fL", ref="80 – 100", low=80, high=100),
        P("mch", "MCH", unit="pg", ref="27 – 32", low=27, high=32),
        P("mchc", "MCHC", unit="g/dL", ref="32 – 36", low=32, high=36),
        P("rdw", "RDW", unit="%", ref="11.5 – 14.5", low=11.5, high=14.5),
    ], price="3500", specimen="Whole blood", container="EDTA (purple top)", turnaround=4),

    T("pcv", "Packed Cell Volume (PCV)", "haematology", [
        P("pcv", "Packed Cell Volume", unit="%", ref="M 40 – 52, F 36 – 46"),
    ], price="1000", specimen="Whole blood", container="EDTA (purple top)", turnaround=1),

    T("hb", "Haemoglobin", "haematology", [
        P("hb", "Haemoglobin", unit="g/dL", ref="M 13 – 17, F 12 – 15"),
    ], price="1000", specimen="Whole blood", container="EDTA (purple top)", turnaround=1),

    T("esr", "Erythrocyte Sedimentation Rate (ESR)", "haematology", [
        P("esr", "ESR", unit="mm/hr", ref="M 0 – 15, F 0 – 20"),
        P("method", "Method", "select", options=["Westergren", "Wintrobe"]),
    ], price="1500", specimen="Whole blood", container="EDTA (purple top)", turnaround=2),

    T("blood-film", "Blood Film / Peripheral Blood Film", "haematology", [
        P("rbc_morphology", "RBC morphology", "text"),
        P("wbc_morphology", "WBC morphology", "text"),
        P("platelet_morphology", "Platelet morphology", "text"),
        P("parasites", "Parasites seen", "text"),
        P("comment", "Comment", "text"),
    ], price="2500", specimen="Whole blood", container="EDTA (purple top)", turnaround=6),

    T("blood-group", "Blood Group & Rhesus", "haematology", [
        P("abo", "ABO group", "select", options=["A", "B", "AB", "O"]),
        P("rhesus", "Rhesus factor (D)", "select", options=["Positive", "Negative"]),
    ], price="1500", specimen="Whole blood", container="EDTA (purple top)", turnaround=2),

    T("rhesus", "Rhesus Factor", "haematology", [
        P("rhesus", "Rhesus factor (D)", "select", options=["Positive", "Negative"]),
    ], price="1000", specimen="Whole blood", container="EDTA (purple top)", turnaround=2),

    T("genotype", "Genotype (Haemoglobin Electrophoresis)", "haematology", [
        P("genotype", "Genotype", "select",
          options=["AA", "AS", "AC", "SS", "SC", "CC", "Other"]),
        P("comment", "Comment", "text"),
    ], price="2500", specimen="Whole blood", container="EDTA (purple top)", turnaround=24),

    T("bleeding-time", "Bleeding Time", "haematology", [
        P("bleeding_time", "Bleeding time", unit="minutes", ref="2 – 7", low=2, high=7),
    ], price="1500", specimen="Capillary blood", turnaround=1),

    T("clotting-time", "Clotting Time", "haematology", [
        P("clotting_time", "Clotting time", unit="minutes", ref="5 – 10", low=5, high=10),
    ], price="1500", specimen="Whole blood", turnaround=1),

    T("pt", "Prothrombin Time (PT) & INR", "haematology", [
        P("pt", "Prothrombin time", unit="seconds", ref="11 – 14", low=11, high=14),
        P("control", "Control", unit="seconds", ref="11 – 14"),
        P("inr", "INR", ref="0.8 – 1.2", low=0.8, high=1.2),
    ], price="4000", specimen="Whole blood", container="Citrate (blue top)", turnaround=6),

    T("inr", "INR", "haematology", [
        P("inr", "INR", ref="0.8 – 1.2", low=0.8, high=1.2),
    ], price="2500", specimen="Whole blood", container="Citrate (blue top)", turnaround=6),

    T("aptt", "Activated Partial Thromboplastin Time (APTT)", "haematology", [
        P("aptt", "APTT", unit="seconds", ref="25 – 35", low=25, high=35),
        P("control", "Control", unit="seconds", ref="25 – 35"),
    ], price="4000", specimen="Whole blood", container="Citrate (blue top)", turnaround=6),
]


# --- 2-6. Chemical pathology ---------------------------------------------

GLUCOSE_REF = "3.9 – 5.5 mmol/L (fasting)"

CHEMISTRY = [
    T("fbg", "Fasting Blood Glucose (FBG)", "chemistry", [
        P("glucose", "Glucose", unit="mmol/L", ref="3.9 – 5.5", low=3.9, high=5.5),
    ], price="1500", specimen="Fluoride oxalate plasma", container="Fluoride (grey top)",
        turnaround=2, description="Patient fasting 8–12 hours."),

    T("rbg", "Random Blood Glucose (RBG)", "chemistry", [
        P("glucose", "Glucose", unit="mmol/L", ref="< 7.8", high=7.8),
    ], price="1500", specimen="Fluoride oxalate plasma", container="Fluoride (grey top)",
        turnaround=1),

    T("2hpp", "2-Hour Postprandial Blood Glucose (2HPP)", "chemistry", [
        P("glucose", "Glucose (2 hours after meal)", unit="mmol/L", ref="< 7.8", high=7.8),
    ], price="1500", specimen="Fluoride oxalate plasma", container="Fluoride (grey top)",
        turnaround=2),

    T("ogtt", "Oral Glucose Tolerance Test (OGTT)", "chemistry", [
        P("fasting", "Fasting glucose", unit="mmol/L", ref="3.9 – 5.5", low=3.9, high=5.5),
        P("half_hour", "30-minute glucose", unit="mmol/L"),
        P("one_hour", "1-hour glucose", unit="mmol/L", ref="< 10.0", high=10),
        P("two_hour", "2-hour glucose", unit="mmol/L", ref="< 7.8", high=7.8),
        P("three_hour", "3-hour glucose", unit="mmol/L"),
        P("glucose_load", "Glucose load given", "text", unit="g"),
        P("comment", "Comment", "text"),
    ], price="6000", specimen="Fluoride oxalate plasma", container="Fluoride (grey top)",
        turnaround=6, description="Time points are filled in as they are drawn — leave the rest empty."),

    T("hba1c", "HbA1c (Glycated Haemoglobin)", "chemistry", [
        P("hba1c", "HbA1c", unit="%", ref="< 5.7 (non-diabetic)", high=5.7),
        P("mean_glucose", "Estimated average glucose", unit="mmol/L"),
    ], price="8000", specimen="Whole blood", container="EDTA (purple top)", turnaround=24),

    T("rft", "Renal Function Test (Kidney Function)", "chemistry", [
        P("urea", "Urea", unit="mmol/L", ref="2.5 – 7.1", low=2.5, high=7.1),
        # Creatinine differs by sex and muscle mass; printed, not flagged.
        P("creatinine", "Creatinine", unit="µmol/L", ref="M 62 – 106, F 44 – 80"),
        P("sodium", "Sodium (Na⁺)", unit="mmol/L", ref="135 – 145", low=135, high=145),
        P("potassium", "Potassium (K⁺)", unit="mmol/L", ref="3.5 – 5.1", low=3.5, high=5.1),
        P("chloride", "Chloride (Cl⁻)", unit="mmol/L", ref="98 – 107", low=98, high=107),
        P("bicarbonate", "Bicarbonate (HCO₃⁻)", unit="mmol/L", ref="22 – 29", low=22, high=29),
        P("egfr", "eGFR", unit="mL/min/1.73m²", ref="> 90", low=90),
    ], price="8000", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("lft", "Liver Function Test (LFT)", "chemistry", [
        P("total_bilirubin", "Total Bilirubin", unit="µmol/L", ref="3 – 21", low=3, high=21),
        P("direct_bilirubin", "Direct Bilirubin", unit="µmol/L", ref="0 – 7", low=0, high=7),
        P("ast", "AST (SGOT)", unit="U/L", ref="0 – 40", high=40),
        P("alt", "ALT (SGPT)", unit="U/L", ref="0 – 41", high=41),
        P("alp", "ALP", unit="U/L", ref="40 – 129", low=40, high=129),
        P("total_protein", "Total Protein", unit="g/L", ref="64 – 83", low=64, high=83),
        P("albumin", "Albumin", unit="g/L", ref="35 – 52", low=35, high=52),
        P("globulin", "Globulin", unit="g/L", ref="20 – 35", low=20, high=35),
    ], price="8000", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("lipid", "Lipid Profile", "chemistry", [
        P("total_cholesterol", "Total Cholesterol", unit="mmol/L", ref="< 5.2", high=5.2),
        P("hdl", "HDL Cholesterol", unit="mmol/L", ref="> 1.0", low=1.0),
        P("ldl", "LDL Cholesterol", unit="mmol/L", ref="< 3.4", high=3.4),
        P("triglycerides", "Triglycerides", unit="mmol/L", ref="< 1.7", high=1.7),
    ], price="7000", specimen="Serum", container="Plain (red top)", turnaround=6,
        description="Patient fasting 9–12 hours."),

    T("calcium", "Serum Calcium", "chemistry", [
        P("calcium", "Calcium", unit="mmol/L", ref="2.15 – 2.55", low=2.15, high=2.55),
        P("corrected_calcium", "Corrected calcium", unit="mmol/L", ref="2.15 – 2.55"),
    ], price="3500", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("phosphate", "Serum Phosphate", "chemistry", [
        P("phosphate", "Phosphate", unit="mmol/L", ref="0.81 – 1.45", low=0.81, high=1.45),
    ], price="3500", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("uric-acid", "Serum Uric Acid", "chemistry", [
        P("uric_acid", "Uric acid", unit="µmol/L", ref="M 202 – 416, F 143 – 339"),
    ], price="3500", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("amylase", "Serum Amylase", "chemistry", [
        P("amylase", "Amylase", unit="U/L", ref="28 – 100", low=28, high=100),
    ], price="5000", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("lipase", "Serum Lipase", "chemistry", [
        P("lipase", "Lipase", unit="U/L", ref="13 – 60", low=13, high=60),
    ], price="5000", specimen="Serum", container="Plain (red top)", turnaround=6),
]


# --- 7-12. Microbiology & parasitology -----------------------------------

MICROBIOLOGY = [
    T("mp", "Malaria Parasite (MP) — Microscopy", "parasitology", [
        P("result", "Malaria parasite", "detected_notdetected", normal="Not detected"),
        P("species", "Species", "select",
          options=["P. falciparum", "P. vivax", "P. ovale", "P. malariae", "Mixed"]),
        P("density", "Parasite density", "text", unit="parasites/µL"),
        P("stage", "Stage seen", "text"),
        P("comment", "Comment", "text"),
    ], price="1500", specimen="Whole blood", container="EDTA (purple top)", turnaround=2,
        description="Species and density are filled in only when the film is positive."),

    T("mrdt", "Malaria Rapid Diagnostic Test (mRDT)", "parasitology", [
        P("result", "Result", "select", options=["Positive", "Negative", "Invalid"],
          normal="Negative"),
        P("kit", "Kit used", "text"),
    ], price="1500", specimen="Whole blood", turnaround=1),

    T("stool-microscopy", "Stool Microscopy / Stool Analysis", "parasitology", [
        P("colour", "Colour", "text", group="Physical"),
        P("consistency", "Consistency", "select", group="Physical",
          options=["Formed", "Semi-formed", "Loose", "Watery", "Hard"]),
        P("appearance", "Appearance", "text", group="Physical"),
        P("occult_blood", "Occult blood", "positive_negative", group="Chemical",
          normal="Negative"),
        P("ova", "Ova", "text", group="Microscopy"),
        P("cysts", "Cysts", "text", group="Microscopy"),
        P("parasites", "Parasites", "text", group="Microscopy"),
        P("rbc", "RBC", "text", unit="/hpf", group="Microscopy"),
        P("wbc", "WBC / Pus cells", "text", unit="/hpf", group="Microscopy"),
        P("other", "Other findings", "text", group="Microscopy"),
    ], price="2000", specimen="Stool", container="Sterile stool container", turnaround=4),

    T("stool-mcs", "Stool Culture & Sensitivity (Stool MCS)", "microbiology",
      CULTURE("Stool"), price="6000", specimen="Stool",
      container="Sterile stool container", turnaround=72),

    T("urine-mcs", "Urine Culture & Sensitivity (Urine MCS)", "microbiology",
      CULTURE("Midstream urine"), price="6000", specimen="Midstream urine",
      container="Sterile universal bottle", turnaround=72),

    T("blood-culture", "Blood Culture & Sensitivity", "microbiology",
      CULTURE("Blood"), price="12000", specimen="Blood",
      container="Blood culture bottle", turnaround=120),

    T("wound-swab-mcs", "Wound Swab MCS", "microbiology",
      CULTURE("Wound swab"), price="6000", specimen="Wound swab",
      container="Sterile swab in transport medium", turnaround=72),

    T("hvs-mcs", "High Vaginal Swab (HVS) MCS", "microbiology",
      CULTURE("High vaginal swab"), price="6000", specimen="High vaginal swab",
      container="Sterile swab in transport medium", turnaround=72),

    T("endocervical-swab-mcs", "Endocervical Swab MCS", "microbiology",
      CULTURE("Endocervical swab"), price="6000", specimen="Endocervical swab",
      container="Sterile swab in transport medium", turnaround=72),

    T("urethral-swab-mcs", "Urethral Swab MCS", "microbiology",
      CULTURE("Urethral swab"), price="6000", specimen="Urethral swab",
      container="Sterile swab in transport medium", turnaround=72),

    T("sputum-mcs", "Sputum MCS", "microbiology",
      CULTURE("Sputum"), price="6000", specimen="Sputum",
      container="Sterile sputum container", turnaround=72),

    T("semen-analysis", "Semen Analysis (Seminal Fluid Analysis)", "microbiology", [
        P("abstinence", "Days of abstinence", "text", unit="days", group="Sample"),
        P("volume", "Volume", unit="mL", ref="≥ 1.5", low=1.5, group="Physical"),
        P("colour", "Colour", "text", group="Physical"),
        P("appearance", "Appearance", "text", group="Physical"),
        P("liquefaction", "Liquefaction", "text", ref="within 60 minutes", group="Physical"),
        P("viscosity", "Viscosity", "text", group="Physical"),
        P("ph", "pH", ref="7.2 – 8.0", low=7.2, high=8.0, group="Physical"),
        P("concentration", "Sperm concentration", unit="×10⁶/mL", ref="≥ 15", low=15,
          group="Microscopy"),
        P("total_count", "Total sperm count", unit="×10⁶/ejaculate", ref="≥ 39", low=39,
          group="Microscopy"),
        P("motility", "Total motility", unit="%", ref="≥ 40", low=40, group="Motility"),
        P("progressive", "Progressive motility", unit="%", ref="≥ 32", low=32, group="Motility"),
        P("non_progressive", "Non-progressive motility", unit="%", group="Motility"),
        P("immotile", "Immotile", unit="%", group="Motility"),
        P("morphology", "Normal morphology", unit="%", ref="≥ 4", low=4, group="Microscopy"),
        P("pus_cells", "Pus cells", "text", unit="/hpf", group="Microscopy"),
        P("rbc", "RBC", "text", unit="/hpf", group="Microscopy"),
        P("epithelial_cells", "Epithelial cells", "text", unit="/hpf", group="Microscopy"),
        P("other", "Other findings", "text", group="Microscopy"),
    ], price="7000", specimen="Semen", container="Sterile universal bottle", turnaround=6),

    T("afb", "AFB / TB Microscopy (ZN Stain)", "microbiology", [
        P("result", "AFB", "detected_notdetected", normal="Not detected"),
        P("grade", "Grade", "select", options=["Scanty", "1+", "2+", "3+"]),
        P("specimen_quality", "Specimen quality", "text"),
        P("comment", "Comment", "text"),
    ], price="3000", specimen="Sputum", container="Sterile sputum container", turnaround=24),

    T("fungal-microscopy", "Fungal Microscopy (KOH)", "microbiology", [
        P("result", "Fungal elements", "detected_notdetected", normal="Not detected"),
        P("description", "Description", "text"),
    ], price="3000", specimen="Skin scraping / nail / hair", turnaround=24),

    T("fungal-culture", "Fungal Culture", "microbiology", [
        P("culture_result", "Culture result", "select",
          options=["No growth", "Growth", "Pending"]),
        P("organism", "Organism isolated", "text"),
        P("sensitive", "Sensitive to", "text"),
        P("resistant", "Resistant to", "text"),
        P("comments", "Comments", "text"),
    ], price="8000", specimen="Skin scraping / nail / hair", turnaround=336),
]


# --- 9. Urinalysis --------------------------------------------------------

URINE = [
    T("urinalysis", "Urinalysis (Urine M/C/S — Routine)", "urinalysis", [
        P("colour", "Colour", "text", group="Physical examination"),
        P("appearance", "Appearance", "select", group="Physical examination",
          options=["Clear", "Slightly turbid", "Turbid", "Cloudy"]),
        P("specific_gravity", "Specific Gravity", group="Physical examination",
          ref="1.005 – 1.030", low=1.005, high=1.030),

        P("ph", "pH", group="Chemical examination", ref="4.5 – 8.0", low=4.5, high=8.0),
        P("protein", "Protein", "select", group="Chemical examination",
          options=["Nil", "Trace", "+", "++", "+++", "++++"], normal="Nil"),
        P("glucose", "Glucose", "select", group="Chemical examination",
          options=["Nil", "Trace", "+", "++", "+++", "++++"], normal="Nil"),
        P("ketones", "Ketones", "select", group="Chemical examination",
          options=["Nil", "Trace", "+", "++", "+++"], normal="Nil"),
        P("blood", "Blood", "select", group="Chemical examination",
          options=["Nil", "Trace", "+", "++", "+++"], normal="Nil"),
        P("bilirubin", "Bilirubin", "select", group="Chemical examination",
          options=["Nil", "+", "++", "+++"], normal="Nil"),
        P("urobilinogen", "Urobilinogen", "text", group="Chemical examination",
          ref="Normal"),
        P("nitrite", "Nitrite", "positive_negative", group="Chemical examination",
          normal="Negative"),
        P("leukocytes", "Leukocyte esterase", "select", group="Chemical examination",
          options=["Nil", "Trace", "+", "++", "+++"], normal="Nil"),

        P("rbc", "RBC", "text", unit="/hpf", group="Microscopy", ref="0 – 2"),
        P("wbc", "WBC / Pus cells", "text", unit="/hpf", group="Microscopy", ref="0 – 5"),
        P("epithelial_cells", "Epithelial cells", "text", unit="/hpf", group="Microscopy"),
        P("casts", "Casts", "text", group="Microscopy"),
        P("crystals", "Crystals", "text", group="Microscopy"),
        P("bacteria", "Bacteria", "text", group="Microscopy"),
        P("yeast", "Yeast cells", "text", group="Microscopy"),
        P("parasites", "Parasites", "text", group="Microscopy"),
        P("other", "Other findings", "text", group="Microscopy"),
    ], price="2000", specimen="Midstream urine", container="Sterile universal bottle",
        turnaround=2),
]


# --- 13. Serology / immunology -------------------------------------------

SEROLOGY_TESTS = [
    SEROLOGY("hiv", "HIV 1 & 2 Screening", price="2000",
             extra=[P("confirmatory", "Confirmatory / second kit", "reactive_nonreactive")]),
    SEROLOGY("hbsag", "Hepatitis B Surface Antigen (HBsAg)", price="2500"),
    SEROLOGY("anti-hcv", "Hepatitis C Antibody (Anti-HCV)", price="2500"),
    SEROLOGY("rpr", "Syphilis — RPR", price="2000"),
    SEROLOGY("vdrl", "Syphilis — VDRL", price="2000",
             extra=[P("titre", "Titre", "text")]),

    T("pregnancy-test", "Pregnancy Test (Urine hCG)", "serology", [
        P("result", "Result", "select", options=["Positive", "Negative", "Invalid"],
          normal="Negative"),
    ], price="1500", specimen="Urine", container="Sterile universal bottle", turnaround=1),

    SEROLOGY("h-pylori", "H. pylori Test", result_type="positive_negative",
             normal="Negative", price="4000",
             extra=[P("sample_type", "Sample type", "select",
                      options=["Serum antibody", "Stool antigen", "Urea breath"])]),

    T("widal", "Widal Test (Febrile Agglutination)", "serology", [
        P("styphi_o", "S. typhi O", "text", group="Salmonella typhi", ref="< 1:80"),
        P("styphi_h", "S. typhi H", "text", group="Salmonella typhi", ref="< 1:80"),
        P("paratyphi_ao", "S. paratyphi A (O)", "text", group="Salmonella paratyphi"),
        P("paratyphi_ah", "S. paratyphi A (H)", "text", group="Salmonella paratyphi"),
        P("paratyphi_bo", "S. paratyphi B (O)", "text", group="Salmonella paratyphi"),
        P("paratyphi_bh", "S. paratyphi B (H)", "text", group="Salmonella paratyphi"),
        P("paratyphi_co", "S. paratyphi C (O)", "text", group="Salmonella paratyphi"),
        P("paratyphi_ch", "S. paratyphi C (H)", "text", group="Salmonella paratyphi"),
        P("comment", "Comment", "text"),
    ], price="2500", specimen="Serum", container="Plain (red top)", turnaround=6,
        description="A titre is only meaningful against a local baseline — report the figures, not a diagnosis."),

    T("aso", "ASO Titre (Anti-Streptolysin O)", "serology", [
        P("result", "ASO titre", "text", unit="IU/mL", ref="< 200"),
        P("qualitative", "Qualitative", "positive_negative", normal="Negative"),
    ], price="4000", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("crp", "C-Reactive Protein (CRP)", "serology", [
        P("result", "CRP", unit="mg/L", ref="< 6", high=6),
        P("qualitative", "Qualitative", "positive_negative", normal="Negative"),
    ], price="4000", specimen="Serum", container="Plain (red top)", turnaround=6),

    T("rf", "Rheumatoid Factor (RF)", "serology", [
        P("result", "Rheumatoid factor", "text", unit="IU/mL", ref="< 14"),
        P("qualitative", "Qualitative", "positive_negative", normal="Negative"),
    ], price="4000", specimen="Serum", container="Plain (red top)", turnaround=6),
]


# --- 14. Endocrinology / hormones ----------------------------------------

HORMONES = [
    HORMONE("tsh", "TSH", "mIU/L", ref="0.4 – 4.0", low=0.4, high=4.0),
    HORMONE("t3", "T3 (Total)", "nmol/L", ref="1.3 – 3.1", low=1.3, high=3.1),
    HORMONE("t4", "T4 (Total)", "nmol/L", ref="66 – 181", low=66, high=181),
    HORMONE("ft3", "Free T3", "pmol/L", ref="3.1 – 6.8", low=3.1, high=6.8),
    HORMONE("ft4", "Free T4", "pmol/L", ref="12 – 22", low=12, high=22),
    # Cycle-dependent in women: the range is printed, never flagged.
    HORMONE("fsh", "FSH", "IU/L", ref="Cycle and sex dependent — see report"),
    HORMONE("lh", "LH", "IU/L", ref="Cycle and sex dependent — see report"),
    HORMONE("prolactin", "Prolactin", "ng/mL", ref="M 4 – 15, F 4 – 23"),
    HORMONE("testosterone", "Testosterone", "nmol/L", ref="M 8.6 – 29, F 0.3 – 1.7"),
    HORMONE("progesterone", "Progesterone", "nmol/L", ref="Cycle dependent — see report"),
    HORMONE("oestradiol", "Oestradiol (E2)", "pmol/L", ref="Cycle dependent — see report"),
    T("beta-hcg", "Serum β-hCG", "endocrinology", [
        P("result", "β-hCG", unit="mIU/mL", ref="Non-pregnant < 5"),
        P("gestation", "Stated gestation", "text", unit="weeks"),
        P("comment", "Comment", "text"),
    ], price="6000", specimen="Serum", container="Plain (red top)", turnaround=24),
    HORMONE("psa", "PSA (Prostate Specific Antigen)", "ng/mL", ref="< 4.0", high=4.0,
            price="8000"),
]


CATALOGUE = (HAEMATOLOGY + CHEMISTRY + MICROBIOLOGY + URINE + SEROLOGY_TESTS + HORMONES)


# --- Panels ---------------------------------------------------------------
#
# A panel points at catalogue tests instead of restating them, so the FBC on
# the antenatal profile is the same FBC — one place to edit, one place a
# result can come from.

PANELS = [
    {
        "code": "antenatal-booking",
        "name": "Antenatal Booking Profile",
        "description": "The tests taken at a booking visit. Each is the catalogue's own test — "
                       "run and report only the ones actually done.",
        "tests": ["blood-group", "rhesus", "genotype", "pcv", "fbc", "hiv", "hbsag",
                  "anti-hcv", "vdrl", "urinalysis", "rbg", "mp"],
    },
    {
        "code": "pregnancy-confirmation",
        "name": "Pregnancy Confirmation",
        "description": "Urine hCG, with the serum assay where the urine test is equivocal.",
        "tests": ["pregnancy-test", "beta-hcg"],
    },
    {
        "code": "malaria-screen",
        "name": "Malaria Screen",
        "description": "Rapid test and film, plus the FBC that goes with a febrile patient.",
        "tests": ["mrdt", "mp", "fbc"],
    },
]


def seed_catalogue(*, LabTest, LabParameter, LabPanel, stdout=None):
    """
    Put the starting catalogue in, without ever standing on the hospital's
    own edits.

    A test that already exists is left exactly as it is — its price, its
    name and its parameters are theirs. Only genuinely new rows are written,
    which is what makes this safe to run from a migration *and* from the
    management command when the catalogue is extended later.

    Takes the model classes as arguments so a migration can pass its
    historical versions and the management command can pass the real ones.
    """
    created_tests = created_params = created_panels = 0

    for order, entry in enumerate(CATALOGUE):
        test, made = LabTest.objects.get_or_create(
            code=entry["code"],
            defaults={
                "name": entry["name"], "category": entry["category"],
                "description": entry["description"], "specimen_type": entry["specimen_type"],
                "container": entry["container"], "turnaround_hours": entry["turnaround_hours"],
                "price": entry["price"], "is_active": True, "display_order": order,
            },
        )
        if made:
            created_tests += 1
        for position, param in enumerate(entry["parameters"]):
            _, param_made = LabParameter.objects.get_or_create(
                test=test, code=param["code"],
                defaults={
                    "name": param["name"], "group": param["group"],
                    "result_type": param["result_type"], "unit": param["unit"],
                    "reference_range": param["reference_range"],
                    "ref_low": param["ref_low"], "ref_high": param["ref_high"],
                    "normal_value": param["normal_value"], "options": param["options"],
                    "display_order": position, "is_required": False, "is_active": True,
                },
            )
            if param_made:
                created_params += 1

    for entry in PANELS:
        panel, made = LabPanel.objects.get_or_create(
            code=entry["code"],
            defaults={"name": entry["name"], "description": entry["description"], "is_active": True},
        )
        if made:
            created_panels += 1
            panel.tests.set(LabTest.objects.filter(code__in=entry["tests"]))

    if stdout:
        stdout.write(f"Laboratory catalogue: +{created_tests} tests, "
                     f"+{created_params} parameters, +{created_panels} panels.")
    return created_tests, created_params, created_panels
