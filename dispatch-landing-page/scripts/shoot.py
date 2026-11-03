import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:4173/"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
OUT = Path(__file__).resolve().parent.parent / "shots"
OUT.mkdir(exist_ok=True)

VIEWPORTS = [
    ("desktop-1280", 1280, 800),
    ("desktop-1440", 1440, 900),
    ("desktop-1920", 1920, 1080),
    ("mobile-390", 390, 844),
    ("mobile-320", 320, 720),
]


def run():
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        for name, width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            errors = []
            page.on("console", lambda msg: errors.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(f"pageerror:{exc}"))
            failed = []
            page.on("requestfailed", lambda req: failed.append(f"{req.url} :: {req.failure}"))
            page.goto(URL, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(900)

            data = page.evaluate(
                """() => {
                    const rect = (el) => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height,b:r.bottom,r:r.right}; };
                    const vw = innerWidth, vh = innerHeight;
                    const h1 = document.querySelector('h1');
                    const lede = document.querySelector('.hero-lede');
                    const primary = document.querySelector('.hero-actions .button--primary');
                    const heroCopy = document.querySelector('.hero-copy');
                    const heroSpec = document.querySelector('.hero-spec');
                    const hero = document.querySelector('.hero');
                    const routes = document.querySelector('.routes');
                    const imgs = [...document.images].map(i => ({src:i.currentSrc||i.src, w:i.naturalWidth, h:i.naturalHeight, alt:i.alt}));
                    const smallTargets = [...document.querySelectorAll('a,button,input')].map(el => {
                        const r = el.getBoundingClientRect();
                        return {tag: el.tagName, text: (el.textContent||'').trim().slice(0,40), w: r.width, h: r.height};
                    }).filter(x => x.w > 0 && x.h > 0 && (x.h < 44 || x.w < 24));
                    const allText = document.body.innerText;
                    const weights = [...document.querySelectorAll('h1,h2,h3,p,a,button,span,dd,dt,th,td')].map(el => getComputedStyle(el).fontWeight);
                    const uniqueWeights = [...new Set(weights)];
                    const families = [...new Set([...document.querySelectorAll('h1,h2,h3,p,a,button,span,dd,dt,th,td')].map(el => getComputedStyle(el).fontFamily))];
                    const h2s = [...document.querySelectorAll('main h2')].map(el => ({text: el.textContent.trim(), size: getComputedStyle(el).fontSize, rect: rect(el)}));
                    const sectionTops = [...document.querySelectorAll('main > section')].map(el => ({cls: el.className, top: el.getBoundingClientRect().top + scrollY, h: el.getBoundingClientRect().height}));
                    return {
                        title: document.title,
                        lang: document.documentElement.lang,
                        description: document.querySelector('meta[name=description]')?.content,
                        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                        scrollWidth: document.documentElement.scrollWidth,
                        clientWidth: document.documentElement.clientWidth,
                        h1: {text: h1.innerText, rect: rect(h1), size: getComputedStyle(h1).fontSize, family: getComputedStyle(h1).fontFamily, weight: getComputedStyle(h1).fontWeight},
                        lede: rect(lede),
                        primary: rect(primary),
                        heroCopy: rect(heroCopy),
                        heroSpec: rect(heroSpec),
                        hero: rect(hero),
                        heroBottomInside: rect(hero).b <= vh + 0.5,
                        h1Visible: rect(h1).b <= vh,
                        ledeVisible: rect(lede).b <= vh,
                        primaryVisible: rect(primary).b <= vh,
                        copyVisible: rect(heroCopy).b <= vh + 0.5,
                        specVisible: rect(heroSpec).b <= vh + 0.5,
                        fonts: {
                            fraunces: document.fonts.check('16px "Fraunces Variable"'),
                            manrope: document.fonts.check('16px "Manrope Variable"'),
                        },
                        imgs,
                        smallTargets,
                        uniqueWeights,
                        families,
                        h2s,
                        sectionTops,
                        wordCount: allText.split(/\\s+/).filter(Boolean).length,
                        bodyFontSize: getComputedStyle(document.body).fontSize,
                        routes,
                        pageHeight: document.body.scrollHeight,
                    };
                }"""
            )
            data["consoleErrors"] = errors
            data["failedRequests"] = failed
            report[name] = data
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
            page.screenshot(path=str(OUT / f"{name}-full.png"), full_page=True)
            page.close()
        browser.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk in ("overflow", "scrollWidth", "clientWidth", "h1", "heroBottomInside", "h1Visible", "ledeVisible", "primaryVisible", "copyVisible", "specVisible", "fonts", "consoleErrors", "failedRequests", "uniqueWeights", "families", "bodyFontSize", "pageHeight")} for k, v in report.items()}, indent=2))


if __name__ == "__main__":
    sys.exit(run())
