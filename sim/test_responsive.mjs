/* Responsive UI, phone-landscape -> ultrawide, in real Chrome:
 *   - the SIMULATOR: no horizontal scroll, no console errors, the WebGL canvas fills the
 *     viewport, window.moxie comes up, and every control is reachable (a working drawer below
 *     900 px, an unclipped side rail above it);
 *   - HUB / SETUP / CLOUD / DOCS: no h-scroll and no console errors at phone and desktop.
 * Portrait phones are test_mobile_layout.mjs's (hit-testing, the composer, the Turnstile
 * challenge); the dock's geometry is test_liveliness.mjs's.
 *
 *   node sim/test_responsive.mjs
 */
import { requireBrowser, serveWeb, launchBrowser, makeChecks, finish, openSim, notable }
  from "./browser_harness.mjs";

const LABEL = "responsive tests";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const site = await serveWeb();
const browser = await launchBrowser(puppeteer, chrome);

const VIEWPORTS = [
  ["phone-landscape",  844, 390],
  ["tablet-portrait",  768, 1024],
  ["tablet-landscape", 1024, 768],
  ["laptop",           1366, 768],
  ["desktop",          1920, 1080],
  ["ultrawide",        2560, 1080],
];
const noHScroll = (p) => p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
const eyes = (label, v) => {
  const left = notable(v.errs, v.aborted);
  eq(left.length, 0, `[${label}] console errors nobody asked for: ${left.slice(0, 2).join(" | ")}`);
};

try {
  for (const [label, w, h] of VIEWPORTS) {
    const v = await openSim(browser, site.url + "/sim.html", { viewport: { width: w, height: h } });
    const { page } = v;
    const s = await page.evaluate(() => {
      const canvas = document.querySelector("#app canvas");
      const offSide = (g) => { const r = g.getBoundingClientRect();
        return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1 || r.bottom < -1); };
      const panel = document.getElementById("panel"), toggle = document.getElementById("rail-toggle");
      return {
        canvasFull: !!canvas && canvas.clientWidth >= window.innerWidth - 2 &&
                    canvas.clientHeight >= window.innerHeight - 2,
        clipped: [...document.querySelectorAll("#rail-scroll .group")].filter(offSide).length,
        toggleShown: !!toggle && getComputedStyle(toggle).display !== "none",
        panelW: panel ? Math.round(panel.getBoundingClientRect().width) : 0,
        innerW: window.innerWidth,
      };
    });
    eyes(`sim ${label}`, v);
    ok(await noHScroll(page), `[sim ${label}] page scrolls horizontally`);
    ok(s.canvasFull, `[sim ${label}] the 3D canvas does not fill the viewport`);
    if (w < 900) {
      // Compact: the rail collapses to a drawer; opening it must reveal every group on screen.
      ok(s.toggleShown, `[sim ${label}] drawer handle not shown (the rail should collapse)`);
      await page.evaluate(() => {
        if (document.getElementById("hud").classList.contains("rail-closed"))
          document.getElementById("rail-toggle").click();
      });
      await page.waitForFunction(() => getComputedStyle(document.getElementById("rail-scroll")).display !== "none",
                                 { timeout: 5000 }).catch(() => {});
      const opened = await page.evaluate(() => ({
        groups: document.querySelectorAll("#rail-scroll .group").length,
        visible: getComputedStyle(document.getElementById("rail-scroll")).display !== "none",
        anyOff: [...document.querySelectorAll("#rail-scroll .group")].some((g) => {
          const r = g.getBoundingClientRect(); return r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1);
        }),
      }));
      ok(opened.visible && opened.groups >= 4 && !opened.anyOff,
         `[sim ${label}] opening the drawer must reveal the controls, none off the side (${JSON.stringify(opened)})`);
    } else {
      // Side rail: a single scrolling column that clips no group and leaves room for the 3D.
      eq(s.clipped, 0, `[sim ${label}] control group(s) off-screen`);
      ok(s.panelW > 0 && s.panelW < s.innerW * 0.7,
         `[sim ${label}] control panel is ${Math.round(s.panelW / s.innerW * 100)}% of the width`);
    }
    await page.close();
  }

  for (const path of ["", "setup.html", "cloud.html", "docs.html"]) {
    for (const [label, w, h] of [["phone", 390, 844], ["desktop", 1440, 900]]) {
      const v = await openSim(browser, `${site.url}/${path}`,
                              { viewport: { width: w, height: h }, settle: false });
      // Pages that fetch their content (docs, cloud) are measured once the network is quiet.
      await v.page.waitForNetworkIdle({ idleTime: 300, timeout: 15000 }).catch(() => {});
      eyes(`${path || "hub"} ${label}`, v);
      ok(await noHScroll(v.page), `[${path || "hub"} ${label}] page scrolls horizontally`);
      await v.page.close();
    }
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
