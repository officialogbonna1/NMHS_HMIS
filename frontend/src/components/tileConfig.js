// One config entry per health-record tile. Drives GenericTileModal so we
// don't hand-write 9 nearly-identical modals — each entry describes its
// fields, what a saved row should read like, and which values are worth
// suggesting. Add a tile by adding an entry here.
//
// Field types the modal renders: text, number, date, textarea, select,
// chips (multi-select), toggle (yes/no). `required: true` blocks Save until
// filled; `summary(item)` builds the secondary line shown on the tile row.
export const TILE_CONFIGS = {
  allergies: {
    title: "Allergy",
    endpoint: "allergies",
    nameField: "name",
    nameLabel: "Allergen",
    namePlaceholder: "e.g. Penicillin, peanuts, latex",
    nameSuggestions: ["Penicillin", "Sulfa drugs", "Aspirin", "Ibuprofen", "Peanuts", "Shellfish", "Eggs", "Latex", "Dust", "Pollen"],
    summary: (item) => (item.reactions ?? []).join(", "),
    isDanger: (item) => item.is_dangerous,
    fields: [
      {
        name: "reactions",
        label: "Reaction(s)",
        type: "chips",
        options: [
          "Hives", "Coughing or wheezing", "Flushed skin or rash",
          "Dizziness and/or lightheadedness", "Tingling or itchy sensation in the mouth",
          "Swelling of the throat and vocal cords", "Face, tongue, or lip swelling",
          "Difficulty breathing", "Vomiting and/or diarrhea", "Loss of consciousness",
          "Abdominal cramps",
        ],
        dangerousOptions: ["Swelling of the throat and vocal cords", "Difficulty breathing", "Loss of consciousness"],
        help: "Tick everything the patient has reacted with. Airway reactions flag the allergy as severe.",
      },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
  medications: {
    title: "Medication",
    endpoint: "medications",
    nameField: "name",
    nameLabel: "Medication",
    namePlaceholder: "e.g. Metformin",
    summary: (item) => [item.strength, item.dose_frequency].filter(Boolean).join(" · "),
    fields: [
      { name: "strength", label: "Strength", type: "text", placeholder: "e.g. 500 mg", width: "half" },
      {
        name: "consumption_type", label: "Route", type: "select", width: "half",
        options: ["Oral", "Injection", "Topical", "Inhaled", "Drops", "Suppository"],
      },
      {
        name: "dose_frequency", label: "Frequency", type: "select", width: "half",
        options: ["Once daily", "Twice daily", "Three times daily", "Four times daily", "Every other day", "Weekly", "As needed"],
      },
      { name: "dose_schedule", label: "Schedule", type: "text", placeholder: "e.g. morning and night", width: "half" },
      { name: "time_frame_days", label: "Course length (days)", type: "number", min: 1, width: "half" },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
  conditions: {
    title: "Medical Condition",
    endpoint: "conditions",
    nameField: "name",
    nameLabel: "Condition",
    namePlaceholder: "e.g. Type 2 diabetes",
    nameSuggestions: ["Hypertension", "Type 2 diabetes", "Asthma", "Sickle cell disease", "Peptic ulcer disease", "HIV", "Epilepsy", "Arthritis"],
    summary: (item) => (item.date_diagnosed ? `Diagnosed ${item.date_diagnosed}` : ""),
    fields: [
      { name: "date_diagnosed", label: "Date diagnosed", type: "date", max: "today", width: "half" },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
  devices: {
    title: "Medical Device",
    endpoint: "devices",
    nameField: "name",
    nameLabel: "Device",
    namePlaceholder: "e.g. Pacemaker",
    summary: (item) => [item.make, item.model].filter(Boolean).join(" "),
    fields: [
      { name: "make", label: "Make", type: "text", width: "half" },
      { name: "model", label: "Model", type: "text", width: "half" },
      { name: "device_id", label: "Device ID", type: "text", width: "half" },
      { name: "date_acquired", label: "Date acquired", type: "date", max: "today", width: "half" },
      { name: "next_update", label: "Next check", type: "date", width: "half" },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
  "surgical-history": {
    title: "Surgery",
    endpoint: "surgical-history",
    nameField: "name",
    nameLabel: "Procedure",
    namePlaceholder: "e.g. Appendectomy",
    summary: (item) => (item.surgery_date ? new Date(item.surgery_date).getFullYear().toString() : ""),
    fields: [
      { name: "surgery_date", label: "Date of surgery", type: "date", max: "today", width: "half" },
      { name: "description", label: "Description", type: "textarea" },
    ],
  },
  vaccinations: {
    title: "Vaccination",
    endpoint: "vaccinations",
    nameField: "name",
    nameLabel: "Vaccine",
    namePlaceholder: "e.g. Tetanus toxoid",
    nameSuggestions: ["BCG", "Hepatitis B", "Polio (OPV)", "Measles", "Yellow fever", "Tetanus toxoid", "COVID-19", "Influenza", "Meningitis"],
    summary: (item) => [item.date_administered && `Given ${item.date_administered}`, item.next_due_date && `next ${item.next_due_date}`].filter(Boolean).join(" · "),
    fields: [
      { name: "date_administered", label: "Date given", type: "date", max: "today", width: "half" },
      { name: "next_due_date", label: "Next due", type: "date", width: "half" },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
  "family-history": {
    title: "Family History",
    endpoint: "family-history",
    nameField: "relationship",
    nameLabel: "Relationship",
    nameOptions: ["Mother", "Father", "Sister", "Brother", "Grandmother", "Grandfather", "Aunt", "Uncle", "Child"],
    summary: (item) => [(item.conditions ?? []).join(", "), item.is_deceased && "deceased"].filter(Boolean).join(" · "),
    fields: [
      { name: "is_deceased", label: "Deceased", type: "toggle", width: "half" },
      {
        name: "conditions", label: "Medical conditions", type: "chips",
        options: ["Diabetes", "Heart Disease", "Cancer", "Allergies and Allergic Reactions", "High Blood Pressure", "Stroke", "Asthma", "Sickle cell", "Mental illness"],
      },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
  "medical-tests": {
    title: "Test Result",
    endpoint: "medical-tests",
    nameField: "title",
    nameLabel: "Test",
    namePlaceholder: "e.g. Full blood count, Chest X-ray",
    nameSuggestions: [
      "Full blood count", "Malaria parasite", "Widal test", "Urinalysis",
      "Blood sugar (fasting)", "Liver function test", "Kidney function test",
      "Chest X-ray", "Abdominal ultrasound", "ECG", "HIV screening", "Hepatitis B screening",
    ],
    summary: (item) => [item.test_type_label ?? item.test_type, item.test_date, item.file_name && "📎 document"]
      .filter(Boolean).join(" · "),
    // A result is a document, a typed finding, or both — never neither.
    // MedicalTestSerializer.validate refuses the same shape server-side.
    validate: (v) => (v.file instanceof File || (v.impressions ?? "").trim())
      ? null
      : "Attach the result document, or type the findings — one or the other.",
    fields: [
      {
        name: "test_type", label: "Type", type: "select", required: true, width: "half",
        options: [
          { value: "labs", label: "Labs" },
          { value: "xray", label: "X-ray" },
          { value: "mri", label: "MRI" },
          { value: "ct", label: "CT Scan" },
          { value: "ultrasound", label: "Ultrasound" },
          { value: "other", label: "Other" },
        ],
      },
      { name: "test_date", label: "Date of test", type: "date", required: true, max: "today", width: "half" },
      {
        name: "file", label: "Result document", type: "file",
        accept: "image/*,application/pdf,.doc,.docx,.txt,.csv,.rtf,.odt,.xls,.xlsx",
        help: "A photo of the printout, a scan, a PDF, or a document. Leave empty if you are typing the result instead.",
      },
      {
        name: "impressions", label: "Findings / result", type: "textarea",
        placeholder: "Type the result here if there is no document to attach",
        help: "What the test showed. This is what the doctor reads on the chart.",
      },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
  "social-history": {
    title: "Social History",
    endpoint: "social-history",
    nameField: "category",
    nameLabel: "Category",
    nameOptions: ["Smoking", "Drinking", "Recreational drugs", "Exercise", "Diet", "Occupation"],
    summary: (item) => [item.is_active ? "Current" : "Not current", item.frequency, item.amount].filter(Boolean).join(" · "),
    fields: [
      { name: "is_active", label: "Currently active?", type: "toggle", width: "half" },
      { name: "started_year", label: "Year started", type: "number", min: 1900, max: new Date().getFullYear(), width: "half" },
      {
        name: "frequency", label: "Frequency", type: "select", width: "half",
        options: ["Daily", "Most days", "Weekly", "Occasionally", "Rarely", "Former, stopped"],
      },
      { name: "amount", label: "Amount", type: "text", placeholder: "e.g. 10 sticks a day", width: "half" },
      { name: "notes", label: "Notes", type: "textarea" },
    ],
  },
};
