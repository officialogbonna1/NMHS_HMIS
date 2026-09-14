import { naira } from "./ui.jsx";

const money = (value) => naira(Number(value ?? 0));

/**
 * A POS register's reconciliation — what it took, by method, and the cash the
 * drawer should hold. Every figure comes from `sales.services.register_summary`,
 * which reads the payments and refunds already recorded against the register's
 * sales: closing a register reconciles money, it never creates any.
 *
 * Used by the till's Close register dialog and by POS Sales → Registers, so
 * the operator and the accountant read the same table.
 */
export default function PosRegisterSummary({ summary }) {
  if (!summary) return <p className="text-sm text-slate-600">Loading the register…</p>;
  const methods = summary.methods ?? [];
  return (
    <div className="space-y-3 text-sm">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
        <dt className="text-slate-600">Opening float</dt>
        <dd className="text-right tabular-nums text-slate-900">{money(summary.opening_float)}</dd>
        <dt className="text-slate-600">Completed sales</dt>
        <dd className="text-right tabular-nums text-slate-900">{summary.sales_count}</dd>
        <dt className="text-slate-600">Discounts given</dt>
        <dd className="text-right tabular-nums text-slate-900">{money(summary.discounts)}</dd>
      </dl>

      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <table className="w-full min-w-[20rem]">
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
              <th className="px-3 py-2 font-semibold">Method</th>
              <th className="px-3 py-2 text-right font-semibold">Received</th>
              <th className="px-3 py-2 text-right font-semibold">Refunded</th>
              <th className="px-3 py-2 text-right font-semibold">Net</th>
            </tr>
          </thead>
          <tbody>
            {methods.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-3 text-slate-600">No money taken yet.</td></tr>
            )}
            {methods.map((row) => (
              <tr key={row.key} className="border-b border-slate-100 last:border-0">
                <td className="px-3 py-2 text-slate-800">{row.label}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-900">{money(row.received)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-900">{money(row.refunded)}</td>
                <td className="px-3 py-2 text-right tabular-nums font-medium text-slate-900">{money(row.net)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-slate-200 font-semibold">
              <td className="px-3 py-2 text-slate-900">Total</td>
              <td className="px-3 py-2 text-right tabular-nums">{money(summary.takings)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{money(summary.refunds)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{money(summary.net_takings)}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      <p className="flex justify-between gap-4 rounded-lg bg-slate-50 px-3 py-2 font-medium text-slate-900">
        <span>Cash expected in the drawer</span>
        <span className="tabular-nums">{money(summary.expected_cash)}</span>
      </p>
      <p className="text-xs text-slate-600">
        Opening float + cash sales − cash refunds. Card, transfer and insurance takings are not in
        the drawer.
      </p>
      {summary.held_sales > 0 && (
        <p className="text-amber-800">
          {summary.held_sales} held sale{summary.held_sales === 1 ? "" : "s"} on this register carry no
          money and moved no stock.
        </p>
      )}
    </div>
  );
}
