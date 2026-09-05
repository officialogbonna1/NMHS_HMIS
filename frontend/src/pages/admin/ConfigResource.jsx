import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../../api/client";
import { readError } from "../../api/errors";
import { useToast } from "../../components/Toaster.jsx";
import {
  Alert, Badge, Button, EmptyState, Field, Input, Page, PageHeader, SearchInput, Select,
  Table, TableWrap, Td, Th, THead, Tr, naira,
} from "../../components/ui.jsx";
import NotFound from "../NotFound.jsx";
import { CONFIG_RESOURCES } from "./configResources.js";

// One page for every configuration list the hospital keeps.
//
// It edits the **same Django models** Django admin edits, over the same API —
// a category added here is the `inventory.ItemCategory` row that appears in
// Django admin on the next refresh, and vice versa. Nothing here holds its own
// copy of anything; there is one database and two front doors onto it.
//
// Two rules it puts in front of the person using it:
//
// 1. **Delete what was never used; deactivate what history points at.** The
//    server refuses a destructive delete with 409 and says why; this page
//    hides the button ahead of that where it can already tell, so the refusal
//    is the backstop rather than the first the person hears of it.
// 2. **Retired rows do not disappear.** The default list is what is in use;
//    "Show inactive" is how you find something to bring back, because a row
//    that vanished when it was deactivated is a row somebody creates twice.

export default function ConfigResource() {
  const { resource } = useParams();
  const config = CONFIG_RESOURCES[resource];
  if (!config) return <NotFound />;
  return <ResourceScreen key={resource} config={config} />;
}

function ResourceScreen({ config }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [editing, setEditing] = useState(null);     // null | "new" | row
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);

  const listKey = [config.endpoint, "config", showInactive];
  const { data, isLoading, isError } = useQuery({
    queryKey: listKey,
    queryFn: () => api.get(`/${config.endpoint}/`, {
      params: { page_size: 500, ...(showInactive ? { all: 1 } : {}) },
    }).then((r) => r.data.results ?? r.data),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: [config.endpoint] });

  const remove = useMutation({
    mutationFn: (row) => api.delete(`/${config.endpoint}/${row.id}/`),
    onSuccess: () => { refresh(); showToast({ title: "Deleted" }); },
    onError: (error) => showToast({
      // The server's 409 explains what is using the row and says to
      // deactivate instead — pass it through rather than inventing wording.
      title: "Not deleted",
      message: readError(error, "This row could not be deleted."),
      tone: "error",
    }),
  });

  const toggleActive = useMutation({
    mutationFn: (row) => api.patch(`/${config.endpoint}/${row.id}/`, { is_active: !row.is_active }),
    onSuccess: (_r, row) => {
      refresh();
      showToast({ title: row.is_active ? "Deactivated" : "Activated" });
    },
    onError: (error) => showToast({
      title: "Could not change this", message: readError(error, "Please try again."), tone: "error",
    }),
  });

  const rows = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return data ?? [];
    return (data ?? []).filter((row) =>
      config.columns.some((column) => String(row[column.key] ?? "").toLowerCase().includes(term)));
  }, [data, search, config.columns]);

  const inactiveCount = (data ?? []).filter((row) => row.is_active === false).length;

  return (
    <Page width="wide">
      <PageHeader
        icon={config.icon}
        title={config.title}
        subtitle={config.blurb}
        actions={
          <Button onClick={() => setEditing("new")}>
            + New {config.title.replace(/s$/, "").toLowerCase()}
          </Button>
        }
        toolbar={
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1 sm:max-w-xs">
              <SearchInput value={search} onChange={setSearch}
                           placeholder={config.searchPlaceholder ?? "Search…"} />
            </div>
            <Button
              variant={showInactive ? "soft" : "secondary"}
              size="sm"
              onClick={() => setShowInactive((v) => !v)}
            >
              {showInactive ? "Hide inactive" : `Show inactive${inactiveCount ? ` (${inactiveCount})` : ""}`}
            </Button>
          </div>
        }
      />

      {config.notes && <Alert tone="info" className="mb-4">{config.notes}</Alert>}

      <Alert tone="info" className="mb-4">
        These are the same records Django admin edits — one database, two ways in.
        A row that history points at is deactivated rather than deleted, so old
        documents keep reading correctly.
      </Alert>

      {editing && (
        <ResourceForm
          config={config}
          row={editing === "new" ? null : editing}
          onDone={() => { setEditing(null); refresh(); }}
          onCancel={() => setEditing(null)}
        />
      )}

      {isLoading && <p className="text-slate-600">Loading…</p>}
      {isError && <p className="text-red-700">Could not load this list.</p>}

      {!isLoading && !isError && rows.length === 0 && (
        <EmptyState
          icon="＋"
          title={search ? "Nothing matches that" : `No ${config.title.toLowerCase()} yet`}
          description={search ? "Clear the search." : "Add the first one."}
        />
      )}

      {rows.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <TableWrap>
            <Table className="min-w-[42rem]">
              <THead>
                <Tr>
                  {config.columns.map((column) => (
                    <Th key={column.key} className={column.align === "right" ? "text-right" : ""}>
                      {column.label}
                    </Th>
                  ))}
                  <Th>Status</Th>
                  <Th className="text-right">Actions</Th>
                </Tr>
              </THead>
              <tbody>
                {rows.map((row) => {
                  const inUse = config.usedWhen ? config.usedWhen(row) : false;
                  return (
                    <Tr key={row.id}>
                      {config.columns.map((column) => (
                        <Td key={column.key}
                            className={`${column.align === "right" ? "text-right tabular-nums" : ""} ${
                              column.strong ? "font-medium text-slate-900" : ""}`}>
                          {format(row[column.key], column)}
                        </Td>
                      ))}
                      <Td>
                        {row.is_active === false
                          ? <Badge tone="neutral">Inactive</Badge>
                          : <Badge tone="success">Active</Badge>}
                      </Td>
                      <Td className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button variant="link" size="xs" onClick={() => setEditing(row)}>
                            Edit
                          </Button>
                          {"is_active" in row && (
                            <Button variant="linkMuted" size="xs"
                                    onClick={() => toggleActive.mutate(row)}>
                              {row.is_active ? "Deactivate" : "Activate"}
                            </Button>
                          )}
                          {/* Offered only where nothing points at the row.
                              The server refuses the rest with 409 — this just
                              means nobody has to find that out by pressing. */}
                          {!inUse && (
                            <Button
                              variant="linkDanger" size="xs"
                              onClick={() => confirm(`Delete ${rowLabel(row, config)}? This cannot be undone.`)
                                && remove.mutate(row)}
                            >
                              Delete
                            </Button>
                          )}
                        </div>
                        {inUse && config.usedNote && (
                          <p className="mt-0.5 text-xs text-slate-600">{config.usedNote}</p>
                        )}
                      </Td>
                    </Tr>
                  );
                })}
              </tbody>
            </Table>
          </TableWrap>
        </div>
      )}
    </Page>
  );
}

function ResourceForm({ config, row, onDone, onCancel }) {
  const { showToast } = useToast();
  const [form, setForm] = useState(() => initialValues(config, row));
  const [error, setError] = useState(null);

  const save = useMutation({
    mutationFn: () => {
      const payload = buildPayload(config, form);
      return row
        ? api.patch(`/${config.endpoint}/${row.id}/`, payload)
        : api.post(`/${config.endpoint}/`, payload);
    },
    onSuccess: () => {
      showToast({ title: row ? "Saved" : "Added" });
      onDone();
    },
    onError: (err) => setError(readError(err, "Could not save this.")),
  });

  const set = (name, value) => setForm((f) => ({ ...f, [name]: value }));

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); setError(null); save.mutate(); }}
      className="mb-5 space-y-4 rounded-xl border border-slate-200 bg-white p-5"
    >
      <h2 className="font-semibold text-slate-900">
        {row ? `Edit ${rowLabel(row, config)}` : `New ${config.title.replace(/s$/, "").toLowerCase()}`}
      </h2>

      <div className="grid gap-4 sm:grid-cols-2">
        {config.fields.map((field) => (
          <FormField key={field.name} field={field} value={form[field.name]}
                     onChange={(value) => set(field.name, value)} form={form} />
        ))}
      </div>

      {error && <p className="text-sm text-red-700">{error}</p>}

      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : row ? "Save changes" : "Add"}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function FormField({ field, value, onChange, form }) {
  // A reference field manages a relationship: the options are the other
  // model's rows, fetched from its own endpoint, so nothing is typed by hand.
  const { data: options } = useQuery({
    queryKey: [field.endpoint, "options"],
    queryFn: () => api.get(`/${field.endpoint}/`, { params: { page_size: 500 } })
      .then((r) => r.data.results ?? r.data),
    enabled: field.type === "reference",
  });

  if (field.type === "toggle") {
    return (
      <label className="flex min-h-[44px] items-center gap-2 sm:min-h-[38px]">
        <input type="checkbox" checked={Boolean(value)}
               onChange={(e) => onChange(e.target.checked)}
               className="h-4 w-4 rounded border-slate-300" />
        <span className="text-sm font-medium text-slate-800">{field.label}</span>
      </label>
    );
  }

  return (
    <Field label={field.label} hint={field.hint} required={field.required}>
      {field.type === "reference" ? (
        <Select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
          <option value="">{field.required ? "Select…" : "None"}</option>
          {(options ?? []).map((option) => (
            <option key={option.id} value={option.id}>
              {option.name ?? option.number ?? option.title ?? `#${option.id}`}
            </option>
          ))}
        </Select>
      ) : field.type === "select" ? (
        <Select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
          {field.options.map(([optionValue, label]) => (
            <option key={optionValue} value={optionValue}>{label}</option>
          ))}
        </Select>
      ) : (
        <Input
          type={field.type === "number" || field.type === "money" ? "number" : "text"}
          step={field.type === "money" ? "0.01" : undefined}
          min={field.type === "number" || field.type === "money" ? "0" : undefined}
          required={field.required}
          placeholder={field.placeholder}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => {
            // A code left blank is filled from the name rather than refused:
            // nobody types a slug on purpose.
            if (field.slugFrom && !value && form[field.slugFrom]) {
              onChange(slugify(form[field.slugFrom]));
            }
          }}
        />
      )}
    </Field>
  );
}

function initialValues(config, row) {
  const values = {};
  for (const field of config.fields) {
    if (row) {
      values[field.name] = row[field.name] ?? "";
    } else {
      values[field.name] = field.default ?? (field.type === "toggle" ? false : "");
    }
  }
  return values;
}

function buildPayload(config, form) {
  const payload = {};
  for (const field of config.fields) {
    const value = form[field.name];
    if (field.type === "toggle") {
      payload[field.name] = Boolean(value);
    } else if (field.type === "reference") {
      // An empty relation is null, not "" — the API rejects the empty string.
      payload[field.name] = value === "" || value == null ? null : Number(value);
    } else if (field.type === "number") {
      payload[field.name] = value === "" ? 0 : Number(value);
    } else if (field.name === "code" && field.slugFrom && !value) {
      payload[field.name] = slugify(form[field.slugFrom] ?? "");
    } else {
      payload[field.name] = value;
    }
  }
  return payload;
}

function format(value, column) {
  if (value == null || value === "") return "—";
  if (column.money) return naira(value);
  return String(value);
}

function rowLabel(row, config) {
  const first = config.columns[0]?.key;
  return row.name ?? row[first] ?? `#${row.id}`;
}

function slugify(text) {
  return String(text).toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}
