
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";

import { AuthProvider } from "./auth/AuthContext.jsx";
import RequireAuth from "./auth/RequireAuth.jsx";
import {
  BILLING_ROLES, CHART_ROLES, CLINICAL_ROLES, PATIENT_LOOKUP_ROLES,
  QUEUE_ROLES, WARD_ROLES,
} from "./auth/roles.js";
import Login from "./pages/Login.jsx";
import NotFound from "./pages/NotFound.jsx";
import PatientsList from "./pages/PatientsList.jsx";
import PatientsNew from "./pages/PatientsNew.jsx";
import PatientDetail from "./pages/PatientDetail.jsx";
import PrescribeDrug from "./pages/PrescribeDrug.jsx";
import InventoryDashboard from "./pages/InventoryDashboard.jsx";
import Pharmacy from "./pages/Pharmacy.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Notifications from "./pages/Notifications.jsx";
import PatientQueue from "./pages/PatientQueue.jsx";
import VitalsStation from "./pages/VitalsStation.jsx";
import SendToConsultation from "./pages/SendToConsultation.jsx";
import ReferPatient from "./pages/ReferPatient.jsx";
import DepartmentStation from "./pages/DepartmentStation.jsx";
import LabCatalogue from "./pages/LabCatalogue.jsx";
import Admissions from "./pages/Admissions.jsx";
import DepartmentsAdmin from "./pages/DepartmentsAdmin.jsx";
import UsersAdmin from "./pages/UsersAdmin.jsx";
import Billing from "./pages/Billing.jsx";
import TransactionHistory from "./pages/TransactionHistory.jsx";
import Outstanding from "./pages/Outstanding.jsx";
import Waivers from "./pages/Waivers.jsx";
import BillingItemsAdmin from "./pages/BillingItemsAdmin.jsx";
import Appointments from "./pages/Appointments.jsx";
import AdminHome from "./pages/admin/AdminHome.jsx";
import ConfigResource from "./pages/admin/ConfigResource.jsx";
import HospitalSettingsPage, { NotificationSettingsPage }
  from "./pages/admin/HospitalSettingsPage.jsx";
import AppShell from "./components/AppShell.jsx";
import { ToastProvider } from "./components/Toaster.jsx";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<RequireAuth><AppShell /></RequireAuth>}>
            <Route path="/" element={<Dashboard />} />
            {/* Looking a patient up is not reading their chart, but it is
                still not for every role: the API refuses the rest, so a
                bare guard here only produced a page of errors. */}
            <Route
              path="/patients"
              element={
                <RequireAuth roles={PATIENT_LOOKUP_ROLES}>
                  <PatientsList />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/new"
              element={
                <RequireAuth roles={["reception"]}>
                  <PatientsNew />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id"
              element={
                <RequireAuth roles={CHART_ROLES}>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/record"
              element={
                <RequireAuth roles={CHART_ROLES}>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/notes"
              element={
                <RequireAuth roles={CHART_ROLES}>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/vitals"
              element={
                <RequireAuth roles={CHART_ROLES}>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            {/* Everything a department has sent back, each addressable so a
                notification or a bookmark can land straight on it. Same
                guard as the chart itself — PatientDetail decides which tabs
                a role actually gets. */}
            {["lab", "ultrasound", "eye", "procedure", "admission", "pharmacy"].map((section) => (
              <Route
                key={section}
                path={`/patients/:id/${section}`}
                element={
                  <RequireAuth roles={CHART_ROLES}>
                    <PatientDetail />
                  </RequireAuth>
                }
              />
            ))}

            <Route
              path="/patients/:id/billing"
              element={
                <RequireAuth roles={BILLING_ROLES}>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/prescribe"
              element={
                <RequireAuth roles={CLINICAL_ROLES}>
                  <PrescribeDrug />
                </RequireAuth>
              }
            />

            <Route
              path="/pharmacy"
              element={
                <RequireAuth roles={["pharmacist"]}>
                  <Pharmacy />
                </RequireAuth>
              }
            />

            <Route
              path="/inventory"
              // Administration's stock desk: every location, receipts into the
              // store, the whole ledger. A pharmacist works their own shelf
              // from /pharmacy instead.
              element={
                <RequireAuth roles={["inventory_manager"]}>
                  <InventoryDashboard />
                </RequireAuth>
              }
            />

            <Route path="/notifications" element={<Notifications />} />

            <Route
              path="/billing"
              element={
                <RequireAuth roles={BILLING_ROLES}>
                  <Billing />
                </RequireAuth>
              }
            />

            <Route
              path="/transactions"
              element={
                <RequireAuth roles={BILLING_ROLES}>
                  <TransactionHistory />
                </RequireAuth>
              }
            />

            <Route
              path="/outstanding"
              element={
                <RequireAuth roles={BILLING_ROLES}>
                  <Outstanding />
                </RequireAuth>
              }
            />

            <Route
              path="/waivers"
              element={
                <RequireAuth roles={BILLING_ROLES}>
                  <Waivers />
                </RequireAuth>
              }
            />

            <Route
              path="/billing-items"
              element={
                <RequireAuth roles={["admin", "cashier", "accountant"]}>
                  <BillingItemsAdmin />
                </RequireAuth>
              }
            />

            <Route
              path="/appointments"
              element={
                <RequireAuth roles={["reception", "doctor"]}>
                  <Appointments />
                </RequireAuth>
              }
            />

            <Route
              path="/vitals"
              element={
                <RequireAuth roles={["nurse"]}>
                  <VitalsStation />
                </RequireAuth>
              }
            />

            <Route
              path="/send-to-doctor"
              element={
                <RequireAuth roles={["nurse"]}>
                  <SendToConsultation />
                </RequireAuth>
              }
            />

            <Route
              path="/refer"
              element={
                <RequireAuth roles={CLINICAL_ROLES}>
                  <ReferPatient />
                </RequireAuth>
              }
            />

            <Route
              path="/admissions"
              element={
                <RequireAuth roles={WARD_ROLES}>
                  <Admissions />
                </RequireAuth>
              }
            />

            {/* Each referral unit works from its own station — the same page
                driven by the purpose it handles. Admin roles reach all three. */}
            <Route
              path="/laboratory"
              element={
                <RequireAuth roles={["laboratory", "admin", "hospital_admin"]}>
                  <DepartmentStation station="laboratory" />
                </RequireAuth>
              }
            />
            {/* The catalogue behind the laboratory station: the lab's own
                tests, parameters, units and ranges, edited by the lab. */}
            <Route
              path="/lab-catalogue"
              element={
                <RequireAuth roles={["laboratory", "admin", "hospital_admin"]}>
                  <LabCatalogue />
                </RequireAuth>
              }
            />
            <Route
              path="/ultrasound"
              element={
                <RequireAuth roles={["radiology", "admin", "hospital_admin"]}>
                  <DepartmentStation station="ultrasound" />
                </RequireAuth>
              }
            />
            <Route
              path="/eye"
              element={
                <RequireAuth roles={["optometrist", "ophthalmologist", "admin", "hospital_admin"]}>
                  <DepartmentStation station="eye" />
                </RequireAuth>
              }
            />

            {/* Notifications raised before the rename still link here. */}
            <Route path="/nursing" element={<Navigate to="/vitals" replace />} />

            <Route
              path="/queue"
              element={
                <RequireAuth roles={QUEUE_ROLES}>
                  <PatientQueue />
                </RequireAuth>
              }
            />

            <Route
              path="/departments"
              element={
                <RequireAuth roles={["admin"]}>
                  <DepartmentsAdmin />
                </RequireAuth>
              }
            />

            <Route
              path="/users"
              element={
                <RequireAuth roles={["admin"]}>
                  <UsersAdmin />
                </RequireAuth>
              }
            />

            {/* Administration: the hospital's own setup, editing the same
                Django models Django admin edits.

                Admin only — configuring the catalogue is not pharmacy work,
                and the API says the same thing (writes on products,
                categories and units are admin-only), so removing the link is
                housekeeping rather than the control. */}
            <Route
              path="/admin"
              element={
                <RequireAuth roles={["admin"]}>
                  <AdminHome />
                </RequireAuth>
              }
            />
            <Route
              path="/admin/settings"
              element={
                <RequireAuth roles={["admin"]}>
                  <HospitalSettingsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/admin/notifications"
              element={
                <RequireAuth roles={["admin"]}>
                  <NotificationSettingsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/admin/:resource"
              element={
                <RequireAuth roles={["admin"]}>
                  <ConfigResource />
                </RequireAuth>
              }
            />
            {/* Any address that matches nothing above — an old bookmark, a
                stale notification link — lands here inside the shell, so the
                nav still works instead of the page going blank. */}
            <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
