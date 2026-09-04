/**
 * The icon set, as inline SVG.
 *
 * Emoji were standing in for icons in the navigation. They render differently
 * on every platform, sit on their own baseline, cannot take a colour, and read
 * as informal — which is the wrong note for a hospital record system. These
 * are one consistent 24px stroke grid instead, inheriting `currentColor` so a
 * nav item's active state tints its icon with it.
 *
 * No icon library: a dozen paths weigh nothing next to a dependency.
 */
const PATHS = {
  home: <><path d="M3 10.5 12 3l9 7.5" /><path d="M5.5 9.5V20h13V9.5" /></>,
  users: <><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19.5c0-3 2.5-5 5.5-5s5.5 2 5.5 5" /><path d="M16 11.2A3 3 0 0 0 16 5.4" /><path d="M17.5 19.5c0-2.2-.8-3.6-2-4.5" /></>,
  queue: <><path d="M4 7h11" /><path d="M4 12h11" /><path d="M4 17h7" /><path d="m17 9 3 3-3 3" /></>,
  bell: <><path d="M18 9a6 6 0 1 0-12 0c0 4.5-1.5 6-1.5 6h15S18 13.5 18 9Z" /><path d="M10.2 19.5a2 2 0 0 0 3.6 0" /></>,
  activity: <path d="M3 12h4l2.5-6 4 13L16.5 12H21" />,
  handoff: <><path d="M3 12h13" /><path d="m12 7 5 5-5 5" /><path d="M20 5v14" /></>,
  share: <><circle cx="6" cy="12" r="2.5" /><circle cx="17.5" cy="6.5" r="2.5" /><circle cx="17.5" cy="17.5" r="2.5" /><path d="m8.3 10.8 6.9-3.2" /><path d="m8.3 13.2 6.9 3.2" /></>,
  calendar: <><rect x="3.5" y="5" width="17" height="15.5" rx="2" /><path d="M3.5 10h17" /><path d="M8 3v4" /><path d="M16 3v4" /></>,
  flask: <><path d="M9.5 3v6.2L4.8 17a2.2 2.2 0 0 0 1.9 3.4h10.6a2.2 2.2 0 0 0 1.9-3.4L14.5 9.2V3" /><path d="M8.5 3h7" /><path d="M7.2 14h9.6" /></>,
  list: <><path d="M8.5 6.5h12" /><path d="M8.5 12h12" /><path d="M8.5 17.5h12" /><circle cx="4.2" cy="6.5" r="1.2" fill="currentColor" stroke="none" /><circle cx="4.2" cy="12" r="1.2" fill="currentColor" stroke="none" /><circle cx="4.2" cy="17.5" r="1.2" fill="currentColor" stroke="none" /></>,
  scan: <><path d="M3.5 8V6a2.5 2.5 0 0 1 2.5-2.5h2" /><path d="M16 3.5h2A2.5 2.5 0 0 1 20.5 6v2" /><path d="M20.5 16v2a2.5 2.5 0 0 1-2.5 2.5h-2" /><path d="M8 20.5H6A2.5 2.5 0 0 1 3.5 18v-2" /><path d="M7 12h10" /></>,
  eye: <><path d="M2.5 12S6 6.5 12 6.5 21.5 12 21.5 12 18 17.5 12 17.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="2.6" /></>,
  bed: <><path d="M3.5 19V8" /><path d="M3.5 13h17v6" /><path d="M20.5 13a4 4 0 0 0-4-4H10v4" /><circle cx="7" cy="10.5" r="1.8" /></>,
  cash: <><rect x="2.5" y="6" width="19" height="12" rx="2" /><circle cx="12" cy="12" r="2.6" /><path d="M6 10v4" /><path d="M18 10v4" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 1.8" /></>,
  tag: <><path d="M11 3.5H4.5A1.5 1.5 0 0 0 3 5v6.2a2 2 0 0 0 .6 1.4l7.5 7.5a1.7 1.7 0 0 0 2.4 0l6.1-6.1a1.7 1.7 0 0 0 0-2.4L12 3.9" /><circle cx="7.5" cy="7.5" r="1.3" fill="currentColor" stroke="none" /></>,
  receipt: <><path d="M5 3.5h14v17l-2.3-1.5-2.4 1.5-2.3-1.5-2.4 1.5L7.3 19 5 20.5Z" /><path d="M8.5 8h7" /><path d="M8.5 12h7" /></>,
  price: <><rect x="3" y="5.5" width="18" height="13" rx="2" /><path d="M7 9.5h4" /><path d="M7 13h6" /><path d="M16.5 9.5v5" /></>,
  pill: <><rect x="2.8" y="8.4" width="18.4" height="7.2" rx="3.6" transform="rotate(-45 12 12)" /><path d="m9.2 9.2 5.6 5.6" /></>,
  box: <><path d="M3.5 7.5 12 3.5l8.5 4v9L12 20.5l-8.5-4Z" /><path d="M3.5 7.5 12 11.5l8.5-4" /><path d="M12 11.5v9" /></>,
  building: <><path d="M4 20.5V5a1.5 1.5 0 0 1 1.5-1.5h7A1.5 1.5 0 0 1 14 5v15.5" /><path d="M14 9.5h4.5A1.5 1.5 0 0 1 20 11v9.5" /><path d="M2.5 20.5h19" /><path d="M7 7.5h4" /><path d="M7 11.5h4" /><path d="M7 15.5h4" /></>,
  shield: <><path d="M12 3.2 5 6v5.5c0 4.2 2.9 7.6 7 9.3 4.1-1.7 7-5.1 7-9.3V6Z" /><path d="m9 12 2.2 2.2L15.2 10" /></>,
  menu: <><path d="M4 7h16" /><path d="M4 12h16" /><path d="M4 17h16" /></>,
  close: <><path d="M6 6l12 12" /><path d="M18 6 6 18" /></>,
  logout: <><path d="M14.5 8V5.5a2 2 0 0 0-2-2h-6a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V16" /><path d="M10 12h10.5" /><path d="m17.5 8.5 3.5 3.5-3.5 3.5" /></>,
  back: <><path d="M19 12H5" /><path d="m11 6-6 6 6 6" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 4.5 4.5" /></>,
  plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  chevronDown: <path d="m6 9.5 6 6 6-6" />,
  chevronRight: <path d="m9.5 6 6 6-6 6" />,
  print: <><path d="M7 8.5V3.5h10v5" /><rect x="3.5" y="8.5" width="17" height="8" rx="2" /><path d="M7 13.5h10v7H7Z" /></>,
  check: <path d="m5 12.5 5 5L19 7" />,
  paperclip: <path d="M20 11.5 12.3 19.2a4.6 4.6 0 0 1-6.5-6.5l7.7-7.7a3.1 3.1 0 0 1 4.4 4.4l-7.7 7.7a1.5 1.5 0 0 1-2.2-2.2l7.1-7.1" />,
  alert: <><path d="M12 4 2.8 20h18.4Z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.4" r="1" fill="currentColor" stroke="none" /></>,
};

export function Icon({ name, className = "h-5 w-5", ...rest }) {
  const path = PATHS[name];
  if (!path) return null;
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" strokeLinejoin="round" className={className}
         focusable="false" {...rest}>
      {path}
    </svg>
  );
}
