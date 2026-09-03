
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api/client";

export default function PatientsNew() {
  const navigate = useNavigate();

  const [form, setForm] = useState({
    first_name: "",
    middle_name: "",
    last_name: "",
    sex: "",
    birthdate: "",
    age_years: "",
    email: "",
    phone_number: "",
    short_note: "",
    street_address: "",
    city: "",
  });

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function handleChange(e) {
    const { name, value } = e.target;

    setForm((current) => ({
      ...current,
      [name]: value,
    }));
  }

  async function handleSubmit(e) {
    e.preventDefault();

    setSaving(true);
    setError("");

    try {
      const payload = {
        ...form,

        // Empty optional numeric field should be null
        age_years: form.age_years === "" ? null : Number(form.age_years),

        // Empty optional date should be null
        birthdate: form.birthdate === "" ? null : form.birthdate,
      };

      const { data } = await api.post("/patients/", payload);

      // Patient was successfully created
      navigate(`/patients/${data.id}`);
    } catch (err) {
      console.error("Patient creation failed:", err);

      const responseData = err.response?.data;

      if (responseData) {
        if (typeof responseData === "object") {
          const messages = Object.entries(responseData)
            .map(([field, message]) => {
              const text = Array.isArray(message)
                ? message.join(", ")
                : String(message);

              return `${field}: ${text}`;
            })
            .join("\n");

          setError(messages);
        } else {
          setError(String(responseData));
        }
      } else {
        setError("Unable to create patient. Please try again.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-3xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">
            Add New Patient
          </h1>

          <p className="text-sm text-slate-600 mt-1">
            Enter the patient's registration information.
          </p>
        </div>

        <button
          type="button"
          onClick={() => navigate("/patients")}
          className="border px-4 py-2 rounded-md hover:bg-gray-50"
        >
          Cancel
        </button>
      </div>

      {error && (
        <div className="mb-5 p-4 rounded-md bg-red-50 border border-red-200 text-red-700 whitespace-pre-line">
          {error}
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className="bg-white border rounded-xl p-6 space-y-6"
      >
        {/* Name */}
        <div>
          <h2 className="text-lg font-medium mb-4">
            Patient Name
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">
                First Name *
              </label>

              <input
                type="text"
                name="first_name"
                value={form.first_name}
                onChange={handleChange}
                required
                className="w-full border rounded-md px-3 py-2"
                placeholder="First name"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Middle Name
              </label>

              <input
                type="text"
                name="middle_name"
                value={form.middle_name}
                onChange={handleChange}
                className="w-full border rounded-md px-3 py-2"
                placeholder="Middle name"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Last Name *
              </label>

              <input
                type="text"
                name="last_name"
                value={form.last_name}
                onChange={handleChange}
                required
                className="w-full border rounded-md px-3 py-2"
                placeholder="Last name"
              />
            </div>
          </div>
        </div>

        {/* Demographics */}
        <div>
          <h2 className="text-lg font-medium mb-4">
            Demographics
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">
                Sex *
              </label>

              <select
                name="sex"
                value={form.sex}
                onChange={handleChange}
                required
                className="w-full border rounded-md px-3 py-2"
              >
                <option value="">Select sex</option>
                <option value="M">Male</option>
                <option value="F">Female</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Date of Birth
              </label>

              <input
                type="date"
                name="birthdate"
                value={form.birthdate}
                onChange={handleChange}
                className="w-full border rounded-md px-3 py-2"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Age
              </label>

              <input
                type="number"
                name="age_years"
                value={form.age_years}
                onChange={handleChange}
                min="0"
                className="w-full border rounded-md px-3 py-2"
                placeholder="Age in years"
              />

              <p className="text-xs text-slate-600 mt-1">
                Use this if date of birth is unknown.
              </p>
            </div>
          </div>
        </div>

        {/* Contact */}
        <div>
          <h2 className="text-lg font-medium mb-4">
            Contact Information
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">
                Email
              </label>

              <input
                type="email"
                name="email"
                value={form.email}
                onChange={handleChange}
                className="w-full border rounded-md px-3 py-2"
                placeholder="patient@example.com"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Phone Number
              </label>

              <input
                type="tel"
                name="phone_number"
                value={form.phone_number}
                onChange={handleChange}
                className="w-full border rounded-md px-3 py-2"
                placeholder="Phone number"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Street Address
              </label>

              <input
                type="text"
                name="street_address"
                value={form.street_address}
                onChange={handleChange}
                className="w-full border rounded-md px-3 py-2"
                placeholder="Street address"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                City
              </label>

              <input
                type="text"
                name="city"
                value={form.city}
                onChange={handleChange}
                className="w-full border rounded-md px-3 py-2"
                placeholder="City"
              />
            </div>
          </div>
        </div>

        {/* Notes */}
        <div>
          <h2 className="text-lg font-medium mb-4">
            Notes
          </h2>

          <label className="block text-sm font-medium mb-1">
            Short Note
          </label>

          <textarea
            name="short_note"
            value={form.short_note}
            onChange={handleChange}
            rows={4}
            className="w-full border rounded-md px-3 py-2"
            placeholder="Additional information about the patient..."
          />
        </div>

        {/* Actions */}
        <div className="flex justify-end gap-3 pt-4 border-t">
          <button
            type="button"
            onClick={() => navigate("/patients")}
            className="border px-5 py-2.5 rounded-md hover:bg-gray-50"
          >
            Cancel
          </button>

          <button
            type="submit"
            disabled={saving}
            className="bg-brand-600 text-white px-5 py-2.5 rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {saving ? "Creating Patient..." : "Create Patient"}
          </button>
        </div>
      </form>
    </div>
  );
}

