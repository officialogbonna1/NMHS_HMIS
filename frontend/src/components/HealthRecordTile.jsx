// Generic tile matching the manual's Allergies/Medications/Conditions
// pattern: title, Add button, list of items, delete per row. Each row is
// clickable to edit, and carries a secondary line (the config's `summary`)
// so the tile is readable at a glance instead of being a list of bare names.
export default function HealthRecordTile({ title, icon, items, onAdd, onDelete, onSelect, renderItem, renderSummary, isDanger }) {
  const count = items?.length ?? 0;

  return (
    <div className="flex flex-col rounded-xl border bg-white transition hover:border-brand-200 hover:shadow-sm">
      <div className="flex items-center justify-between border-b px-4 py-3">
        <div className="flex items-center gap-2 font-medium">
          <span>{icon}</span>
          <span>{title}</span>
          {count > 0 && (
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-semibold text-slate-600">{count}</span>
          )}
        </div>
        <button
          onClick={onAdd}
          className="rounded-full px-2.5 py-1 text-sm font-medium text-brand-600 hover:bg-brand-50"
        >
          + Add
        </button>
      </div>

      <div className="divide-y">
        {count ? (
          items.map((item) => {
            const summary = renderSummary?.(item);
            return (
              <div key={item.id} className="group flex items-start justify-between gap-2 px-4 py-2.5">
                <button onClick={() => onSelect?.(item)} className="min-w-0 flex-1 text-left">
                  <div className="flex items-center gap-1.5">
                    {isDanger?.(item) && <span title="Severe reaction recorded">⚠️</span>}
                    <span className="truncate text-sm font-medium">{renderItem ? renderItem(item) : item.name}</span>
                  </div>
                  {summary && <p className="mt-0.5 truncate text-xs text-slate-600">{summary}</p>}
                </button>
                <button
                  onClick={() => onDelete?.(item)}
                  aria-label={`Remove ${renderItem ? renderItem(item) : item.name}`}
                  className="shrink-0 rounded p-1 text-slate-400 opacity-0 transition hover:bg-red-50 hover:text-red-500 focus:opacity-100 group-hover:opacity-100"
                >
                  🗑
                </button>
              </div>
            );
          })
        ) : (
          <button onClick={onAdd} className="w-full px-4 py-6 text-center text-sm text-slate-500 hover:text-brand-600">
            None recorded — add the first
          </button>
        )}
      </div>
    </div>
  );
}
