
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";

import { AuthProvider } from "./auth/AuthContext.jsx";
import RequireAuth from "./auth/RequireAuth.jsx";
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
import DepartmentsAdmin from "./pages/DepartmentsAdmin.jsx";
import UsersAdmin from "./pages/UsersAdmin.jsx";
import Billing from "./pages/Billing.jsx";
import TransactionHistory from "./pages/TransactionHistory.jsx";
import BillingItemsAdmin from "./pages/BillingItemsAdmin.jsx";
import Appointments from "./pages/Appointments.jsx";
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
            <Route path="/patients" element={<PatientsList />} />

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
                <RequireAuth>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/record"
              element={
                <RequireAuth>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/notes"
              element={
                <RequireAuth>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/vitals"
              element={
                <RequireAuth>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/billing"
              element={
                <RequireAuth roles={["cashier", "accountant", "reception"]}>
                  <PatientDetail />
                </RequireAuth>
              }
            />

            <Route
              path="/patients/:id/prescribe"
              element={
                <RequireAuth roles={["doctor"]}>
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
              element={
                <RequireAuth roles={["pharmacist", "inventory_manager"]}>
                  <InventoryDashboard />
                </RequireAuth>
              }
            />

            <Route path="/notifications" element={<Notifications />} />

            <Route
              path="/billing"
              element={
                <RequireAuth roles={["cashier", "accountant", "reception"]}>
                  <Billing />
                </RequireAuth>
              }
            />

            <Route
              path="/transactions"
              element={
                <RequireAuth roles={["cashier", "accountant", "reception"]}>
                  <TransactionHistory />
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
                <RequireAuth roles={["doctor", "admin", "hospital_admin"]}>
                  <ReferPatient />
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
                <RequireAuth roles={["reception", "doctor", "nurse", "laboratory", "radiology", "optometrist", "ophthalmologist"]}>
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
