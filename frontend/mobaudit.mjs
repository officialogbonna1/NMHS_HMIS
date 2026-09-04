import { chromium, devices } from "playwright";
import fs from "node:fs";
const OUT = process.env.OUT; fs.mkdirSync(OUT, { recursive: true });

// Credentials come from the environment, never the file. This script is in the
// repository; a login typed into it is a login published with it.
//   AUDIT_USER=… AUDIT_PASS=… OUT=/tmp/audit node mobaudit.mjs
const { AUDIT_USER, AUDIT_PASS } = process.env;
if (!AUDIT_USER || !AUDIT_PASS) {
  console.error("Set AUDIT_USER and AUDIT_PASS (a throwaway account on the dev server).");
  process.exit(1);
}

// isMobile:true enables meta-viewport emulation and touch. Without it a
// missing <meta viewport> is invisible to the test — which is exactly how the
// 981px-viewport bug survived the earlier sweeps.
const SIZES = [
  ["320", 320, 640, true], ["360", 360, 800, true], ["375", 375, 667, true],
  ["390", 390, 844, true], ["412", 412, 915, true], ["430", 430, 932, true],
  ["land-667", 667, 375, true],           // phone, landscape
  ["768", 768, 1024, true],               // tablet portrait
  ["1024", 1024, 768, false], ["1280", 1280, 900, false], ["1440", 1440, 900, false],
];
const ROUTES = [
  ["/", "dashboard"], ["/patients", "patients"], ["/patients/new", "patients-new"],
  ["/queue", "queue"], ["/vitals", "vitals"], ["/send-to-doctor", "sendtodoc"],
  ["/refer", "refer"], ["/laboratory", "laboratory"], ["/lab-catalogue", "labcat"],
  ["/ultrasound", "ultrasound"], ["/eye", "eye"], ["/admissions", "admissions"],
  ["/appointments", "appointments"], ["/billing", "billing"], ["/outstanding", "outstanding"],
  ["/waivers", "waivers"], ["/transactions", "transactions"], ["/billing-items", "billingitems"],
  ["/pharmacy", "pharmacy"], ["/inventory", "inventory"], ["/notifications", "notifications"],
  ["/departments", "departments"], ["/users", "users"],
];

const b = await chromium.launch({ executablePath: "/usr/bin/google-chrome" });
const bad = [], tiny = [], errs = [];
let chartOverflow = [];

for (const [name, w, h, mobile] of SIZES) {
  const ctx = await b.newContext({
    viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile,
    deviceScaleFactor: mobile ? 2 : 1,
    ...(mobile ? { userAgent: devices["iPhone 12"].userAgent } : {}),
  });
  const p = await ctx.newPage();
  const pageErrs = []; p.on("pageerror", e => pageErrs.push(`${name} ${e.message}`));
  p.on("console", m => { if (m.type() === "error" && !m.text().includes("404")) pageErrs.push(`${name} console: ${m.text().slice(0,120)}`); });

  await p.goto("http://localhost:5173/login", { waitUntil: "networkidle" });
  await p.fill('input[placeholder="Username"]', AUDIT_USER);
  await p.fill('input[type="password"]', AUDIT_PASS);
  await p.click('button[type="submit"]');
  await p.waitForURL(u => !u.pathname.includes("login"), { timeout: 20000 });

  const all = [...ROUTES];
  for (const [route, slug] of all) {
    await p.goto("http://localhost:5173" + route, { waitUntil: "networkidle" }).catch(()=>{});
    await p.waitForTimeout(450);
    const m = await p.evaluate(() => {
      const de = document.documentElement;
      let worst = null;
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        if (r.width === 0) continue;
        const over = Math.round(r.right - window.innerWidth);
        if (over > 2 && (!worst || over > worst.over))
          worst = { over, tag: el.tagName.toLowerCase(), cls: (el.className?.toString?.()||"").slice(0,70) };
      }
      const small = [...document.querySelectorAll("button,a[href],input,select,[role=button]")]
        .filter(el => { const r = el.getBoundingClientRect();
          if (r.width===0||r.height===0||r.height>=32) return false;
          const cs = getComputedStyle(el);
          if (cs.visibility==="hidden"||cs.clip==="rect(0px, 0px, 0px, 0px)") return false;
          if (el.closest(".sr-only")) return false;
          const hold = el.closest("label,td,th,li");
          if (hold && hold.getBoundingClientRect().height>=32) return false;
          return true; })
        .map(el => `${el.tagName.toLowerCase()}"${(el.textContent||el.ariaLabel||"").trim().slice(0,20)}"`);
      // Smallest rendered text anywhere that holds real words.
      let minFont = 99;
      for (const el of document.querySelectorAll("p,span,td,th,li,a,button,label,h1,h2,h3,div")) {
        if (!el.textContent?.trim() || el.children.length) continue;
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs && fs < minFont) minFont = fs;
      }
      return { over: de.scrollWidth - window.innerWidth, worst, small, minFont,
               cssW: window.innerWidth };
    });
    if (m.over > 2) bad.push(`${name.padEnd(9)} ${route.padEnd(16)} +${m.over}px  ${m.worst?.tag}.${m.worst?.cls}`);
    if (m.small.length) tiny.push(`${name.padEnd(9)} ${route.padEnd(16)} ${m.small.length}: ${m.small.slice(0,3).join(", ")}`);
    if (name === "375" && ["dashboard","patients","billing","labcat","admissions","inventory"].includes(slug))
      await p.screenshot({ path: `${OUT}/${slug}-375.png` });
  }

  // The patient chart, which the route list cannot reach directly.
  await p.goto("http://localhost:5173/patients", { waitUntil: "networkidle" });
  await p.waitForTimeout(600);
  const link = p.locator('a[href^="/patients/"]').first();
  if (await link.count()) {
    await link.click(); await p.waitForTimeout(1100);
    const o = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (o > 2) chartOverflow.push(`${name}: +${o}px`);
    if (name === "375") await p.screenshot({ path: `${OUT}/chart-375.png` });
  }
  errs.push(...pageErrs);
  await ctx.close();
}
await b.close();
console.log("=== HORIZONTAL OVERFLOW ===");     console.log(bad.length ? bad.join("\n") : "none");
console.log("\n=== PATIENT CHART OVERFLOW ==="); console.log(chartOverflow.length ? chartOverflow.join("\n") : "none");
console.log("\n=== SUB-32px TOUCH TARGETS ===");  console.log(tiny.length ? tiny.join("\n") : "none");
console.log("\n=== CONSOLE / PAGE ERRORS ===");   console.log(errs.length ? [...new Set(errs)].join("\n") : "none");
