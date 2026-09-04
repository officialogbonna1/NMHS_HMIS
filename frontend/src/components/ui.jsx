/**
 * The shared vocabulary the whole HMIS is drawn in.
 *
 * This exists because the same button had grown five slightly different
 * spellings, page containers had six different max-widths, and every table
 * re-invented its own header row. One primitive per idea, so a screen a nurse
 * has never opened still behaves like the one she uses all day.
 *
 * Rules these primitives encode, so no page has to remember them:
 *
 *  - **Touch first.** Controls are >=44px tall on a phone and tighten to ~36px
 *    from `sm` up, where there is a mouse. Nothing is smaller than 32px.
 *  - **16px inputs on mobile.** iOS zooms the page on focus for anything
 *    smaller, which is what made forms jump about; `sm:text-sm` brings it back
 *    down on a desk.
 *  - **`min-w-0` everywhere a flex or grid child can hold wide content.**
 *    Flex/grid children default to `min-width:auto`, so one long table or chip
 *    strip silently widens the whole page. That is the single cause of most
 *    of the horizontal scrolling in this app.
 *  - **Contrast rungs** (per CLAUDE.md): slate-500 meta, slate-600 supporting,
 *    slate-700 instructions, slate-800/900 content. slate-400 is only for
 *    genuinely de-emphasised things.
 */
import { createContext, useContext, useEffect, useId, useRef } from "react";
import { Link } from "react-router-dom";
import { Icon } from "./icons.jsx";

/* ============================================================ page scaffold */

const WIDTHS = {
  narrow: "max-w-3xl",   // a single form or a focused task
  default: "max-w-5xl",  // most working pages
  wide: "max-w-7xl",     // dashboards, master–detail, dense tables
  full: "max-w-none",
};

/**
 * Every page's outer container. One set of gutters — 16px on a phone, where
 * the old `p-6` spent 15% of a 320px screen on margin, opening out on wider
 * screens.
 */
export function Page({ width = "default", className = "", children }) {
  return (
    <div className={`mx-auto w-full ${WIDTHS[width] ?? WIDTHS.default} px-3 py-4 sm:px-6 sm:py-6 lg:px-8 ${className}`}>
      {children}
    </div>
  );
}

/**
 * The masthead of a navigation destination.
 *
 * The shape is the same everywhere so the application reads as one product —
 * a contextual icon, the title, one line saying what the page is for, an
 * optional strip of live figures, the primary actions, and an optional
 * toolbar row for search and filters:
 *
 *     [icon]  Admissions                                   [Admit patient]
 *             Manage admitted patients, wards and beds.
 *             24 of 30 beds occupied · 6 free
 *     ┌──────────────────────────────────────────────────────────────────┐
 *     │ [search…]  [ward ▾]  [status ▾]                                   │
 *     └──────────────────────────────────────────────────────────────────┘
 *
 * It is restrained on purpose: no gradient, no oversized type, one hairline
 * ring around the icon. The modern feel is meant to come from the hierarchy
 * and the spacing, not from decoration — this is a hospital record system.
 *
 * Every slot is optional, so a page that genuinely differs can take the title
 * alone and arrange the rest itself.
 */
export function PageHeader({
  icon, title, subtitle, meta, actions, toolbar, breadcrumb, className = "",
}) {
  return (
    <header className={`mb-4 sm:mb-5 ${className}`}>
      {breadcrumb}

      {/* The masthead is a panel in its own right — a 10px-rounded surface
          holding the title, what the page is for, and its figures. Loose on
          the page background it read as a document heading; contained, it
          reads as the head of an application screen, and it gives the stat
          strip and the toolbar something to belong to. */}
      <div className="rounded-[10px] border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
          {/* One left-aligned block at every width: icon and title on a line,
              the description under it, the figures under that. Centring this
              on a phone split it away from the content it heads. */}
          <div className="min-w-0 sm:flex-1">
            {/* The icon shares a row with the title alone, centred against it.
                It used to sit in a column beside the whole text block under
                `items-start`, which aligned a 36px disc to the top of a 28px
                line box — so it read as sagging below the title, and got worse
                at each breakpoint as the disc grew to 40px while the line box
                stayed 28px. Centring it on the title line is correct by
                construction at every width, with no per-breakpoint nudges. */}
            <div className="flex min-w-0 items-center gap-2.5">
              {icon && (
                <span
                  aria-hidden="true"
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-100"
                >
                  <Icon name={icon} className="h-[18px] w-[18px]" />
                </span>
              )}
              <h1 className="min-w-0 text-lg font-semibold tracking-tight text-slate-900 sm:text-xl lg:text-[1.6rem] lg:leading-8">
                {title}
              </h1>
            </div>

            {/* Below the icon, not indented under it: on a 320px phone that
                indent cost the description a third of its line. */}
            {subtitle && <p className="mt-1.5 max-w-2xl text-sm text-slate-600">{subtitle}</p>}

            {/* The figures read as a strip of their own on a phone — separated
                by hairlines and set on a quiet ground, the way an app puts its
                counts under a title. It has to survive whatever a page passes:
                some send a Badge or a plain date rather than a MetaStat, so
                this stays a wrapping flex row and never a fixed grid. */}
            {meta && (
              <div className="mt-3 flex min-w-0 flex-wrap items-center gap-x-0 gap-y-2 rounded-[10px] bg-slate-50 px-3 py-2.5 ring-1 ring-inset ring-slate-200/70 sm:mt-2 sm:gap-x-4 sm:rounded-none sm:bg-transparent sm:px-0 sm:py-0 sm:ring-0">
                {meta}
              </div>
            )}
          </div>

          {actions && (
            /* Right-aligned on a phone, where they sit on their own line under
               the title block; back to natural order beside it from `sm`. They
               used to be stretched edge-to-edge by `[&>*]:flex-1`, which is
               defensible for a lone primary call to action and looks wrong for
               everything else — a full-bleed "Refresh" reads as the most
               important thing on screen. */
            <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 sm:justify-start">
              {actions}
            </div>
          )}
        </div>

        {/* Search and filters sit on their own line, under the title rather
            than beside it: on a phone there is no beside, and on a desktop a
            filter bar reads as belonging to the list it filters. */}
        {toolbar && (
          <div className="mt-4 flex min-w-0 flex-col gap-2 border-t border-slate-200/80 pt-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-2.5">
            {toolbar}
          </div>
        )}
      </div>
    </header>
  );
}

const META_TONES = {
  neutral: "text-slate-900",
  brand: "text-brand-700",
  positive: "text-emerald-700",
  warning: "text-amber-700",
  danger: "text-red-700",
};

/**
 * One live figure in a page header's meta strip — "24 of 30 beds", "6 waiting".
 * The number carries the weight and the word stays quiet, so a glance reads
 * the figures and not the labels.
 *
 * On a phone it stacks — figure over label, divided from its neighbour by a
 * hairline — which is how an application shows a row of counts. From `sm` it
 * lays back down into an inline "6 waiting", because a wide header has room
 * for the sentence and a row of stacked columns would look like a dashboard
 * bolted to the title.
 */
export function MetaStat({ value, label, tone = "neutral" }) {
  return (
    <span
      className="flex min-w-0 flex-col items-center gap-0 border-slate-200 px-2.5 text-center
        first:border-l-0 first:pl-0 last:pr-0 [&:not(:first-child)]:border-l
        sm:flex-row sm:items-baseline sm:gap-1.5 sm:border-l-0 sm:px-0 sm:text-left"
    >
      <span className={`text-[15px] font-semibold leading-tight tabular-nums sm:text-sm ${META_TONES[tone] ?? META_TONES.neutral}`}>
        {value}
      </span>
      <span className="mt-0.5 max-w-full truncate text-xs text-slate-600 sm:mt-0 sm:text-sm">{label}</span>
    </span>
  );
}

export function Breadcrumb({ items = [] }) {
  return (
    <nav aria-label="Breadcrumb" className="mb-2">
      <ol className="flex flex-wrap items-center gap-1 text-sm text-slate-600">
        {items.map((item, i) => (
          <li key={`${item.label}-${i}`} className="flex items-center gap-1">
            {i > 0 && <span aria-hidden="true" className="text-slate-400">/</span>}
            {item.to
              ? <Link to={item.to} className="rounded hover:text-brand-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">{item.label}</Link>
              : <span className="font-medium text-slate-800" aria-current="page">{item.label}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** A titled block within a page. */
export function Section({ title, description, actions, className = "", children }) {
  return (
    <section className={className}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            {title && <h2 className="font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-0.5 max-w-2xl text-sm text-slate-600">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

/* ====================================================================== card */

export function Card({ as: As = "section", className = "", children, ...rest }) {
  return (
    <As className={`overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm ${className}`} {...rest}>
      {children}
    </As>
  );
}

export function CardHeader({ title, description, actions, className = "", children }) {
  return (
    <div className={`flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-slate-100 px-4 py-3 sm:px-5 ${className}`}>
      {children ?? (
        <>
          <div className="min-w-0">
            {title && <h2 className="font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-0.5 max-w-xl text-sm text-slate-600">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
        </>
      )}
    </div>
  );
}

export function CardBody({ className = "", children }) {
  return <div className={`px-4 py-4 sm:px-5 ${className}`}>{children}</div>;
}

/** The action bar at the foot of a form card. Stacks on a phone. */
export function CardFooter({ className = "", children }) {
  return (
    <div className={`flex flex-col gap-2 border-t border-slate-100 bg-slate-50 px-4 py-3 sm:flex-row sm:flex-wrap sm:items-center sm:px-5 ${className}`}>
      {children}
    </div>
  );
}

/* =================================================================== buttons */

const BUTTON_VARIANTS = {
  primary: "bg-brand-600 text-white shadow-sm hover:bg-brand-700 focus-visible:ring-brand-500",
  secondary: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 hover:border-slate-400 focus-visible:ring-brand-500",
  ghost: "text-slate-700 hover:bg-slate-100 focus-visible:ring-brand-500",
  danger: "bg-red-600 text-white shadow-sm hover:bg-red-700 focus-visible:ring-red-500",
  dangerOutline: "border border-red-300 bg-white text-red-700 hover:bg-red-50 focus-visible:ring-red-500",
  success: "bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 focus-visible:ring-emerald-500",
  successOutline: "border border-emerald-300 bg-white text-emerald-700 hover:bg-emerald-50 focus-visible:ring-emerald-500",
  // A quiet tinted action. `secondary` is a white box with a grey border,
  // which beside a white card reads as nothing at all — this is what a
  // recurring action like Refresh should look like: clearly a control,
  // clearly not the primary one.
  soft: "bg-brand-50 text-brand-700 ring-1 ring-inset ring-brand-200 hover:bg-brand-100 hover:text-brand-800 focus-visible:ring-brand-500",
  // A text action that still has a body. These read as links but are laid out
  // as controls, so a thumb has something to land on — the audit found ~36
  // hand-spelled copies of this, each a different size.
  link: "text-brand-700 hover:bg-brand-50 hover:text-brand-800 focus-visible:ring-brand-500",
  linkDanger: "text-red-700 hover:bg-red-50 hover:text-red-800 focus-visible:ring-red-500",
  linkMuted: "text-slate-700 hover:bg-slate-100 hover:text-slate-900 focus-visible:ring-brand-500",
};

// Comfortable under a thumb, tighter under a cursor.
const BUTTON_SIZES = {
  // For a text action sitting tight beside others; still 36px under a thumb.
  xs: "min-h-[36px] px-2 py-1 text-sm gap-1.5",
  sm: "min-h-[36px] px-3 py-1.5 text-sm gap-1.5",
  md: "min-h-[44px] sm:min-h-[38px] px-4 py-2.5 sm:py-2 text-sm gap-2",
  lg: "min-h-[48px] sm:min-h-[42px] px-5 py-3 sm:py-2.5 text-sm sm:text-base gap-2",
};

const buttonClass = (variant, size, block) =>
  `inline-flex items-center justify-center rounded-lg font-medium transition ` +
  `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 ` +
  `disabled:cursor-not-allowed disabled:opacity-50 ` +
  `${BUTTON_SIZES[size] ?? BUTTON_SIZES.md} ${BUTTON_VARIANTS[variant] ?? BUTTON_VARIANTS.primary} ` +
  `${block ? "w-full" : ""}`;

export function Button({
  variant = "primary", size = "md", block = false, as, to, className = "",
  loading = false, loadingText, children, ...rest
}) {
  const cls = `${buttonClass(variant, size, block)} ${className}`;
  if (to) return <Link to={to} className={cls} {...rest}>{children}</Link>;
  const As = as ?? "button";
  return (
    <As type={As === "button" ? (rest.type ?? "button") : undefined}
        className={cls} aria-busy={loading || undefined} {...rest}>
      {loading ? (loadingText ?? children) : children}
    </As>
  );
}

/** A square button carrying only an icon — `label` is required, it is the name. */
export function IconButton({ label, variant = "ghost", className = "", children, ...rest }) {
  return (
    <button
      type="button" aria-label={label} title={label}
      className={`inline-grid h-11 w-11 shrink-0 place-items-center rounded-lg transition sm:h-9 sm:w-9
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1
        disabled:cursor-not-allowed disabled:opacity-40
        ${BUTTON_VARIANTS[variant] ?? BUTTON_VARIANTS.ghost} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

/**
 * A link inside a sentence. Deliberately *not* a 36px box: forcing one into
 * flowing prose breaks the line box and pushes the text apart. It earns its
 * hit area from the line-height instead, and pays for the smaller target with
 * an underline, so it is identifiable without relying on colour alone.
 *
 * For a standalone action, use `<Button variant="link">` — that one is a box.
 */
export function TextLink({ to, href, className = "", children, ...rest }) {
  const cls = `rounded font-medium text-brand-700 underline underline-offset-2 transition
    hover:text-brand-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${className}`;
  if (to) return <Link to={to} className={cls} {...rest}>{children}</Link>;
  return <a href={href} className={cls} {...rest}>{children}</a>;
}

/* ==================================================================== inputs */

// 16px on a phone so iOS does not zoom on focus; 14px from `sm` up.
export const controlClass =
  "w-full min-h-[44px] sm:min-h-[38px] rounded-lg border border-slate-300 bg-white px-3 py-2.5 sm:py-2 " +
  "text-base sm:text-sm text-slate-900 outline-none transition placeholder:text-slate-500 " +
  "focus:border-brand-500 focus:ring-2 focus:ring-brand-100 " +
  "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-600";

const FieldContext = createContext(null);

/**
 * A labelled control. Wires the label, the hint and the error to the input by
 * id, so a screen reader announces all three and clicking the label focuses
 * the field.
 */
export function Field({ label, hint, error, required, className = "", children }) {
  const id = useId();
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;
  return (
    <FieldContext.Provider value={{ id, describedBy, invalid: Boolean(error) }}>
      <div className={`min-w-0 ${className}`}>
        {label && (
          <label htmlFor={id} className="mb-1 block text-sm font-medium text-slate-700">
            {label}
            {required && <span className="ml-0.5 text-red-600" aria-hidden="true">*</span>}
            {required && <span className="sr-only"> (required)</span>}
          </label>
        )}
        {children}
        {hint && !error && <p id={`${id}-hint`} className="mt-1 text-sm text-slate-600">{hint}</p>}
        {error && <p id={`${id}-error`} className="mt-1 text-sm text-red-700">{error}</p>}
      </div>
    </FieldContext.Provider>
  );
}

// Inside a <Field> these pick up its id/aria wiring; outside one they are
// plain controls, so they drop into existing markup unchanged.
function useFieldProps(props) {
  const field = useContext(FieldContext);
  if (!field) return props;
  return {
    id: props.id ?? field.id,
    "aria-describedby": props["aria-describedby"] ?? field.describedBy,
    "aria-invalid": props["aria-invalid"] ?? (field.invalid || undefined),
    ...props,
  };
}

export function Input({ className = "", ...props }) {
  return <input className={`${controlClass} ${className}`} {...useFieldProps(props)} />;
}

export function Textarea({ className = "", rows = 3, ...props }) {
  return <textarea rows={rows} className={`${controlClass} ${className}`} {...useFieldProps(props)} />;
}

export function Select({ className = "", children, ...props }) {
  return (
    <select className={`${controlClass} appearance-none bg-[length:16px] bg-[right_0.6rem_center] bg-no-repeat pr-9 ${className}`}
            style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='%2364748b'%3E%3Cpath d='M5.5 7.5 10 12l4.5-4.5z'/%3E%3C/svg%3E\")" }}
            {...useFieldProps(props)}>
      {children}
    </select>
  );
}

/** A search box with its icon and a clear button. */
export function SearchInput({ value, onChange, placeholder = "Search…", label = "Search", className = "" }) {
  return (
    <div className={`relative min-w-0 ${className}`}>
      <span aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500">
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-4 w-4">
          <circle cx="9" cy="9" r="6" /><path d="m13.5 13.5 3.5 3.5" strokeLinecap="round" />
        </svg>
      </span>
      <input
        type="search" value={value} aria-label={label} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={`${controlClass} pl-9 ${value ? "pr-10" : ""} [&::-webkit-search-cancel-button]:hidden`}
      />
      {value && (
        <button type="button" onClick={() => onChange("")} aria-label="Clear search"
                className="absolute right-1.5 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500">
          <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4"><path d="M6.7 5.3a1 1 0 00-1.4 1.4L8.6 10l-3.3 3.3a1 1 0 101.4 1.4L10 11.4l3.3 3.3a1 1 0 001.4-1.4L11.4 10l3.3-3.3a1 1 0 00-1.4-1.4L10 8.6 6.7 5.3z" /></svg>
        </button>
      )}
    </div>
  );
}

/** A responsive form grid: one column on a phone, two from `sm`. */
export function FormGrid({ columns = 2, className = "", children }) {
  const cols = columns === 3 ? "sm:grid-cols-2 lg:grid-cols-3" : columns === 1 ? "" : "sm:grid-cols-2";
  return <div className={`grid gap-4 ${cols} ${className}`}>{children}</div>;
}

/* ==================================================== badges & status pills */

const BADGE_TONES = {
  neutral: "bg-slate-100 text-slate-700 ring-slate-200",
  brand: "bg-brand-50 text-brand-700 ring-brand-200",
  success: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  danger: "bg-red-50 text-red-700 ring-red-200",
  info: "bg-sky-50 text-sky-700 ring-sky-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
};

export function Badge({ tone = "neutral", className = "", children }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${BADGE_TONES[tone] ?? BADGE_TONES.neutral} ${className}`}>
      {children}
    </span>
  );
}

/** A badge with a dot — for a live state rather than a label. */
export function StatusDot({ tone = "neutral", children }) {
  const dot = { neutral: "bg-slate-400", brand: "bg-brand-500", success: "bg-emerald-500",
                warning: "bg-amber-500", danger: "bg-red-500", info: "bg-sky-500", violet: "bg-violet-500" };
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${BADGE_TONES[tone] ?? BADGE_TONES.neutral}`}>
      <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${dot[tone] ?? dot.neutral}`} />
      {children}
    </span>
  );
}

/* ==================================================================== tables */

/**
 * The scroll container every table needs.
 *
 * `min-w-0` on the wrapper is the load-bearing part: without it a wide table
 * inside a flex or grid child stretches its parent and scrolls the *page*
 * sideways instead of itself.
 */
export function TableWrap({ className = "", children }) {
  return (
    <div className={`w-full min-w-0 overflow-x-auto ${className}`}>
      {children}
    </div>
  );
}

export function Table({ className = "", children }) {
  return <table className={`w-full text-sm ${className}`}>{children}</table>;
}

export function Th({ className = "", children, ...rest }) {
  return (
    <th scope="col" className={`whitespace-nowrap px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-600 ${className}`} {...rest}>
      {children}
    </th>
  );
}

export function Td({ className = "", children, ...rest }) {
  return <td className={`px-3 py-2.5 align-top text-slate-800 ${className}`} {...rest}>{children}</td>;
}

export function THead({ children }) {
  return <thead className="border-b border-slate-200 bg-slate-50">{children}</thead>;
}

export function Tr({ className = "", children, ...rest }) {
  return <tr className={`border-b border-slate-100 last:border-0 ${className}`} {...rest}>{children}</tr>;
}

/* ============================================ loading / empty / error states */

export function Skeleton({ className = "" }) {
  return <div aria-hidden="true" className={`animate-pulse rounded bg-slate-200 ${className}`} />;
}

/** Rows of skeleton text, for a list that is still loading. */
export function SkeletonRows({ rows = 5, className = "" }) {
  return (
    <div className={`space-y-3 ${className}`} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

/**
 * Nothing here yet — said properly.
 *
 * `icon` is a name from the icon set, drawn in the same quiet disc the page
 * headers use. It used to render whatever it was given at `text-3xl`, which
 * in practice meant an emoji: the one thing this design language does not do.
 */
export function EmptyState({ icon, title, description, action, className = "" }) {
  return (
    <div className={`px-6 py-12 text-center ${className}`}>
      {icon && (
        <span
          aria-hidden="true"
          className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200"
        >
          <Icon name={icon} className="h-5 w-5" />
        </span>
      )}
      <p className="font-medium text-slate-800">{title}</p>
      {description && <p className="mx-auto mt-1 max-w-sm text-sm text-slate-600">{description}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", description, onRetry, className = "" }) {
  return (
    <div className={`rounded-xl border border-red-200 bg-red-50 px-4 py-5 sm:px-5 ${className}`} role="alert">
      <p className="font-medium text-red-800">{title}</p>
      {description && <p className="mt-1 text-sm text-red-700">{description}</p>}
      {onRetry && <Button variant="secondary" size="sm" className="mt-3" onClick={onRetry}>Try again</Button>}
    </div>
  );
}

const ALERT_TONES = {
  info: "border-sky-200 bg-sky-50 text-slate-800",
  warning: "border-amber-200 bg-amber-50 text-slate-800",
  danger: "border-red-200 bg-red-50 text-red-800",
  success: "border-emerald-200 bg-emerald-50 text-slate-800",
};

export function Alert({ tone = "info", title, className = "", children }) {
  return (
    <div role={tone === "danger" ? "alert" : "status"}
         className={`rounded-lg border px-4 py-3 text-sm ${ALERT_TONES[tone] ?? ALERT_TONES.info} ${className}`}>
      {title && <p className="font-semibold">{title}</p>}
      {children && <div className={title ? "mt-0.5" : ""}>{children}</div>}
    </div>
  );
}

/* ==================================================================== modal */

/**
 * A dialog that is a centred panel on a desk and a bottom sheet on a phone.
 *
 * The body scrolls, never the page behind it, and the footer stays reachable —
 * a dialog whose Save button is below the fold on a 320px screen is a dialog
 * nobody can finish. Escape closes; focus moves in on open and back on close.
 */
export function Modal({ open, onClose, title, description, footer, size = "md", children }) {
  const panelRef = useRef(null);
  const returnFocusRef = useRef(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return undefined;
    returnFocusRef.current = document.activeElement;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    document.addEventListener("keydown", onKey);
    // Stop the page behind from scrolling under the sheet.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
      returnFocusRef.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  const width = { sm: "sm:max-w-md", md: "sm:max-w-lg", lg: "sm:max-w-2xl", xl: "sm:max-w-4xl" }[size] ?? "sm:max-w-lg";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/50 sm:items-center sm:p-4"
         onMouseDown={onClose}>
      <div
        ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        className={`flex max-h-[92dvh] w-full flex-col rounded-t-2xl bg-white shadow-xl focus:outline-none sm:max-h-[85vh] sm:rounded-2xl ${width}`}
      >
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2 id={titleId} className="font-semibold text-slate-900">{title}</h2>
            {description && <p className="mt-0.5 text-sm text-slate-600">{description}</p>}
          </div>
          <IconButton label="Close" onClick={onClose} className="-mr-1 shrink-0">
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-5 w-5"><path d="M6.7 5.3a1 1 0 00-1.4 1.4L8.6 10l-3.3 3.3a1 1 0 101.4 1.4L10 11.4l3.3 3.3a1 1 0 001.4-1.4L11.4 10l3.3-3.3a1 1 0 00-1.4-1.4L10 8.6 6.7 5.3z" /></svg>
          </IconButton>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-5">{children}</div>
        {footer && (
          <footer className="flex flex-col-reverse gap-2 border-t border-slate-100 bg-slate-50 px-4 py-3 sm:flex-row sm:justify-end sm:px-5">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}

/* ===================================================================== tabs */

/**
 * A scrollable tab strip. On a phone the tabs scroll sideways inside their own
 * rail rather than wrapping into three rows or pushing the page wide.
 */
export function TabBar({ className = "", children, label = "Sections" }) {
  return (
    <div className={`-mx-3 mb-4 min-w-0 border-b border-slate-200 px-3 scroll-rail sm:mx-0 sm:mb-5 sm:px-0 ${className}`}>
      <nav aria-label={label} className="flex gap-1">
        {children}
      </nav>
    </div>
  );
}

export function Tab({ active, className = "", children, ...rest }) {
  const cls = `shrink-0 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition
    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1
    ${active ? "border-brand-600 text-brand-700" : "border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-900"} ${className}`;
  if (rest.to) return <Link className={cls} aria-current={active ? "page" : undefined} {...rest}>{children}</Link>;
  return <button type="button" className={cls} aria-current={active ? "page" : undefined} {...rest}>{children}</button>;
}

/* ================================================================ utilities */

/**
 * How long somebody has been waiting, in words. Two identical copies of this
 * lived in the vitals and department stations; the queue would have been a
 * third.
 */
export function waitedFor(since) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(since)) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} hr ${minutes % 60} min` : `${Math.floor(hours / 24)} day(s)`;
}

/** Money, one way, everywhere. */
export const naira = (value) =>
  `₦${Number(value ?? 0).toLocaleString("en-NG", { maximumFractionDigits: 2 })}`;

/** A definition row — label beside value on a desk, stacked on a phone. */
export function DataRow({ label, children, className = "" }) {
  return (
    <div className={`flex flex-col gap-0.5 py-1.5 sm:flex-row sm:gap-3 ${className}`}>
      <dt className="shrink-0 text-sm text-slate-600 sm:w-40">{label}</dt>
      <dd className="min-w-0 text-sm text-slate-800">{children}</dd>
    </div>
  );
}
