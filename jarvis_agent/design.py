"""Design system: STRUCTURE (layout mechanics, accessibility, component skeletons) is written by code so it never breaks;
VISUAL LANGUAGE (glass vs flat/editorial, colour, radius, shadow depth, motion) is a set of tokens the planner chooses per
project - a restaurant, a SaaS product and a portfolio get different design languages, not the same glass kit reused.
See VISUAL_STYLES for the choices and PLANNER_GUIDE for how the plan step picks one."""
import colorsys
import hashlib
import os
import re

from . import util

# Each style is a coherent visual language: how much translucency/blur, how bold the radius/shadow, whether gradients
# and motion are used, and the mood they read as. The planner PICKS one (or "auto") per request; nothing here is forced.
VISUAL_STYLES = {
    "glass": {"desc": "glassmorphism: translucent blurred cards, soft glow, layered depth - modern tech/product/nightlife/fitness",
              "surface": "rgba(255,255,255,.07)", "surface-strong": "rgba(255,255,255,.13)", "border": "rgba(255,255,255,.17)",
              "blur": "18px", "radius": "22px", "radius-sm": "12px", "shadow": "0 12px 44px rgba(0,0,0,.38)", "gradient": "1", "glow": "1", "motion": "1", "bg-orbs": "1"},
    "editorial": {"desc": "warm editorial: paper-like surfaces, generous whitespace, serif display type, understated - restaurant/hotel/luxury/publication",
                  "surface": "#fff", "surface-strong": "#fff", "border": "rgba(20,15,10,.1)", "blur": "0px",
                  "radius": "4px", "radius-sm": "2px", "shadow": "0 2px 10px rgba(20,15,10,.06)", "gradient": "0", "glow": "0", "motion": "1", "bg-orbs": "0"},
    "minimal": {"desc": "modern minimal SaaS: flat surfaces, crisp borders, restrained colour, precise grid - tech/SaaS/dev tools/B2B",
                "surface": "var(--bg-2)", "surface-strong": "var(--bg-2)", "border": "rgba(127,127,127,.18)", "blur": "0px",
                "radius": "14px", "radius-sm": "8px", "shadow": "0 1px 3px rgba(0,0,0,.08),0 8px 24px rgba(0,0,0,.06)", "gradient": "1", "glow": "0", "motion": "1", "bg-orbs": "0"},
    "bold": {"desc": "bold energetic: strong saturated colour blocks, big confident type, high contrast - gym/sports/events/youth brand",
             "surface": "var(--bg-2)", "surface-strong": "color-mix(in srgb,var(--accent) 12%,var(--bg-2))", "border": "rgba(127,127,127,.16)", "blur": "0px",
             "radius": "18px", "radius-sm": "10px", "shadow": "0 10px 30px rgba(0,0,0,.22)", "gradient": "1", "glow": "1", "motion": "1", "bg-orbs": "0"},
    "elegant": {"desc": "elegant premium: near-black or cream, thin gold/metal accent line, refined serif, lots of negative space - luxury/jewelry/fashion/real estate",
                "surface": "rgba(255,255,255,.04)", "surface-strong": "rgba(255,255,255,.07)", "border": "rgba(255,255,255,.14)", "blur": "0px",
                "radius": "2px", "radius-sm": "2px", "shadow": "0 20px 60px rgba(0,0,0,.5)", "gradient": "0", "glow": "0", "motion": "1", "bg-orbs": "0"},
}
DEFAULT_STYLE = "minimal"

PLANNER_GUIDE = (
    "Also choose the VISUAL LANGUAGE for this exact brand/audience - do not default to the same look for everything. Options: "
    + " | ".join(f'"{k}": {v["desc"]}' for k, v in VISUAL_STYLES.items())
    + '. Reply this as "style" in the JSON. Pick "auto" only if genuinely unsure (a hash-based fallback is used). '
    "Also choose \"headingFont\": \"serif\"|\"sans\" (serif for editorial/elegant/luxury/publication, sans otherwise) and "
    "\"radius\": \"sharp\"|\"soft\"|\"pill\" (visual weight of corners, independent of the style's base) and \"density\": \"airy\"|\"compact\" (generous vs tight spacing)."
)

DESIGN_CSS = r"""
/* JARVIS design system - structure is fixed; look (surfaces, radius, shadow, blur, motion, orbs) comes from theme.css tokens per project */
:root{
  --accent:#7c5cff; --accent-2:#22d3ee;
  --bg-1:#0a0e1f; --bg-2:#141b38; --text:#eef1ff; --muted:#a7afcb;
  --surface:rgba(255,255,255,.07); --surface-strong:rgba(255,255,255,.13); --surface-border:rgba(255,255,255,.17);
  --shadow:0 12px 44px rgba(0,0,0,.38); --radius:22px; --radius-sm:12px; --blur:18px;
  --font:"Inter","Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif;
  --font-head:var(--font); --gradient:1; --glow:1; --orbs:1; --motion:1;
  --maxw:1200px; --nav-h:72px; --ok:#34d399; --bad:#fb7185;
}
:root[data-theme="light"]{
  --bg-1:#eef2ff; --bg-2:#fdf2f8; --text:#151a2e; --muted:#56618a;
  --surface:rgba(255,255,255,.55); --surface-strong:rgba(255,255,255,.78); --surface-border:rgba(255,255,255,.9);
  --shadow:0 12px 44px rgba(60,70,130,.18);
}
*,*::before,*::after{box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;font-family:var(--font);color:var(--text);line-height:1.65;-webkit-font-smoothing:antialiased;
  background:linear-gradient(135deg,var(--bg-1),var(--bg-2)) fixed;overflow-x:hidden}
body::before,body::after{content:"";position:fixed;z-index:-1;border-radius:50%;filter:blur(90px);opacity:calc(.5 * var(--orbs));pointer-events:none}
body::before{width:52vmax;height:52vmax;left:-14vmax;top:-16vmax;background:radial-gradient(circle,var(--accent),transparent 66%);animation:drift calc(24s / var(--motion)) ease-in-out infinite alternate}
body::after{width:46vmax;height:46vmax;right:-12vmax;bottom:-14vmax;background:radial-gradient(circle,var(--accent-2),transparent 66%);animation:drift calc(28s / var(--motion)) ease-in-out infinite alternate-reverse}
@keyframes drift{to{transform:translate3d(6vmax,4vmax,0) scale(1.12)}}
img,svg,video{max-width:100%;height:auto}
a{color:inherit}
h1,h2,h3,h4{font-family:var(--font-head);line-height:1.15;margin:0 0 .5em;letter-spacing:-.02em}
h1{font-size:clamp(2.4rem,6.2vw,4.6rem);font-weight:800}
h2{font-size:clamp(1.8rem,3.8vw,2.8rem);font-weight:750}
h3{font-size:clamp(1.15rem,2vw,1.4rem);font-weight:650}
p{margin:0 0 1em;color:var(--muted)}
.lead{font-size:clamp(1.05rem,1.8vw,1.3rem);color:var(--muted);max-width:60ch}
.gradient-text{background:linear-gradient(90deg,var(--accent),var(--accent-2));-webkit-background-clip:text;background-clip:text;color:transparent}
[data-gradient="0"] .gradient-text{background:none;color:var(--accent)}
.eyebrow{display:inline-block;font-size:.8rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--accent-2);margin-bottom:.8rem}
.container{width:min(100% - 2rem,var(--maxw));margin-inline:auto}
.section{padding:clamp(3.5rem,8vw,7rem) 0;scroll-margin-top:calc(var(--nav-h) + 8px)}
.section-head{max-width:720px;margin:0 auto clamp(2rem,4vw,3.5rem);text-align:center}
.section-head .lead{margin-inline:auto}
.center{text-align:center}.muted{color:var(--muted)}
.stack>*+*{margin-top:1rem}
.flex{display:flex;align-items:center;gap:1rem;flex-wrap:wrap}.between{justify-content:space-between}.justify-center{justify-content:center}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.skip-link{position:absolute;left:1rem;top:-4rem;background:var(--accent);color:#fff;padding:.6rem 1rem;border-radius:var(--radius-sm);z-index:100}
.skip-link:focus{top:1rem}
:focus-visible{outline:3px solid var(--accent-2);outline-offset:3px;border-radius:6px}

/* surfaces (the design LANGUAGE - glass, flat or editorial - is entirely driven by the tokens theme.css sets; ".surface" is the
   neutral name, ".glass" is kept as an alias so existing copy still works even though most brands are NOT glass) */
.surface,.glass{background:var(--surface);border:1px solid var(--surface-border);border-radius:var(--radius);box-shadow:var(--shadow);
  -webkit-backdrop-filter:blur(var(--blur)) saturate(140%);backdrop-filter:blur(var(--blur)) saturate(140%)}
.surface-strong,.glass-strong{background:var(--surface-strong)}
.card{padding:clamp(1.25rem,2.4vw,2rem);transition:transform .35s cubic-bezier(.2,.8,.2,1),box-shadow .35s,border-color .35s}
.card:hover{transform:translateY(calc(-6px * var(--motion)));border-color:color-mix(in srgb,var(--accent) 55%,transparent);box-shadow:var(--shadow-hover,var(--shadow))}
.card-icon{display:grid;place-items:center;width:52px;height:52px;border-radius:var(--radius-sm);margin-bottom:1rem;color:var(--on-accent,#fff);
  background:linear-gradient(135deg,var(--accent),var(--accent-2-eff,var(--accent-2)));box-shadow:0 8px 24px color-mix(in srgb,var(--accent) calc(45% * var(--glow)),transparent)}
.icon{width:1.25em;height:1.25em;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;vertical-align:-.2em}
.card-icon .icon{width:26px;height:26px}

/* layout grids */
.grid{display:grid;gap:clamp(1rem,2.2vw,1.75rem)}
.grid-2{grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr))}
.grid-3{grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr))}
.grid-4{grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr))}
.split{display:grid;grid-template-columns:1.05fr .95fr;gap:clamp(1.5rem,4vw,4rem);align-items:center}

/* nav */
.navbar{position:sticky;top:0;z-index:50;margin:.75rem auto 0;width:min(100% - 1.5rem,var(--maxw));border-radius:999px;padding:.55rem .8rem .55rem 1.4rem;
  display:flex;align-items:center;justify-content:space-between;gap:1rem;min-height:var(--nav-h);transition:min-height .3s ease,margin .3s ease,box-shadow .3s ease}
.navbar.scrolled{min-height:calc(var(--nav-h) - 14px);margin-top:.35rem;box-shadow:var(--shadow)}
.brand{font-weight:800;font-size:1.2rem;text-decoration:none;letter-spacing:-.02em}
.nav-links{display:flex;align-items:center;gap:.25rem;list-style:none;margin:0;padding:0}
.nav-links a{display:block;padding:.55rem .95rem;border-radius:999px;text-decoration:none;color:var(--muted);font-weight:550;font-size:.95rem;transition:background .25s,color .25s}
.nav-links a:hover,.nav-links a.active,.nav-links a[aria-current="page"]{color:var(--text);background:var(--surface-strong)}
.nav-toggle{display:none;width:46px;height:46px;border-radius:50%;border:1px solid var(--surface-border);background:var(--surface-strong);color:var(--text);cursor:pointer;place-items:center}
.nav-toggle .icon{width:22px;height:22px}
@media (max-width:900px){
  .navbar{border-radius:26px;flex-wrap:wrap}
  .nav-toggle{display:grid}
  .nav-links{display:none;width:100%;flex-direction:column;align-items:stretch;padding:.5rem 0 .6rem}
  .nav-links.open{display:flex}
  .nav-links a{padding:.85rem 1rem}
  .split{grid-template-columns:1fr}
}

/* hero */
.hero{min-height:min(calc(100vh - var(--nav-h) - 2rem),760px);display:grid;align-items:center;padding:clamp(2rem,6vw,5rem) 0}
.hero-inner{display:grid;gap:1.4rem;justify-items:center;text-align:center;max-width:900px;margin-inline:auto}
/* full-bleed photo hero (Orangetheory / boxing-gym style): the photo fills a rounded panel, a dark scrim keeps white text readable */
.hero-cover{position:relative;isolation:isolate;margin:1rem clamp(.5rem,2vw,1.5rem) 0;border-radius:calc(var(--radius) + 6px);overflow:hidden;
  background:#111 var(--hero-img) center/cover no-repeat;min-height:min(calc(100vh - var(--nav-h)),820px)}
.hero-cover::before{content:"";position:absolute;inset:0;z-index:-1;background:linear-gradient(100deg,rgba(0,0,0,.78) 0%,rgba(0,0,0,.5) 55%,rgba(0,0,0,.2) 100%)}
.hero-cover h1,.hero-cover h2,.hero-cover p,.hero-cover .lead{color:#fff}
.hero-cover .gradient-text{background:linear-gradient(90deg,#fff,color-mix(in srgb,var(--accent-2) 55%,#fff));-webkit-background-clip:text;background-clip:text;color:transparent}
.hero-cover .badge,.hero-cover .eyebrow{color:#fff;background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.3)}
.hero-cover .btn:not(.btn-primary){color:#fff;background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.4)}
.hero-cover .hero-visual{display:none}
.hero-left .hero-inner{justify-items:start;text-align:left;margin-inline:0}
.hero-actions{display:flex;gap:.9rem;flex-wrap:wrap;justify-content:center}
.hero-left .hero-actions{justify-content:flex-start}
.hero-visual{aspect-ratio:1/1;max-height:520px;border-radius:var(--radius);position:relative;overflow:hidden;
  background:radial-gradient(circle at 30% 25%,var(--accent),transparent 55%),radial-gradient(circle at 75% 70%,var(--accent-2),transparent 55%),var(--surface)}
.hero-visual img{width:100%;height:100%;object-fit:cover;display:block}          /* safety net: an <img> placed here directly (instead of via .photo-frame) still covers cleanly, never splits the box */

/* real photography, with a safe fallback if the image never loads (offline, blocked, slow) - the gradient is
   ALWAYS underneath, so a failed photo never leaves anything unreadable, and .photo-fallback (set by onerror in the
   HTML pattern) makes that explicit by hiding the broken <img> instead of showing a broken-image icon */
.photo-frame{position:relative;overflow:hidden;border-radius:var(--radius);
  background:radial-gradient(circle at 30% 25%,var(--accent),transparent 55%),radial-gradient(circle at 75% 70%,var(--accent-2),transparent 55%),var(--surface)}
.photo-frame img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .5s ease}
.photo-frame.photo-fallback img{display:none}
.photo-frame:hover img{transform:scale(1.04)}
.photo-frame.full-bleed{border-radius:0}
.hero-photo{aspect-ratio:16/9;max-height:78vh}
.photo-overlay{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.15),rgba(0,0,0,.55));display:flex;flex-direction:column;justify-content:flex-end;padding:clamp(1.5rem,4vw,3.5rem)}
.photo-overlay *{color:#fff}
.photo-overlay .lead{color:rgba(255,255,255,.85)}
.photo-grid img,.photo-frame.tile-photo{aspect-ratio:4/3;object-fit:cover}

/* interaction polish */
.magnetic{transition:transform .25s cubic-bezier(.2,.8,.2,1)}
.tilt{transition:transform .3s ease,box-shadow .3s ease;transform-style:preserve-3d;will-change:transform}
.h-scroll{display:flex;gap:1.25rem;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:1rem;margin:0 calc(-1 * max(0px,(100vw - var(--maxw))/2 + 1rem));padding-inline:max(1rem,(100vw - var(--maxw))/2 + 1rem)}
.h-scroll::-webkit-scrollbar{height:8px}.h-scroll::-webkit-scrollbar-thumb{background:var(--surface-border);border-radius:99px}
.h-scroll>*{scroll-snap-align:start;flex:0 0 min(78vw,340px)}
.sticky-story{display:grid;grid-template-columns:1fr 1fr;gap:3rem;align-items:start}
.sticky-story .sticky-visual{position:sticky;top:calc(var(--nav-h) + 1.5rem);height:calc(100vh - var(--nav-h) - 3rem);border-radius:var(--radius)}
.sticky-story .story-step{min-height:60vh;display:flex;flex-direction:column;justify-content:center;opacity:.35;transition:opacity .4s ease}
.sticky-story .story-step.active{opacity:1}
@media (max-width:900px){.sticky-story{grid-template-columns:1fr}.sticky-story .sticky-visual{position:static;height:280px;margin-bottom:1.5rem}.sticky-story .story-step{min-height:0;opacity:1;padding-block:1rem}}
@media (prefers-reduced-motion:reduce){.magnetic,.tilt{transition:none}}

/* asymmetric / editorial composition - an escape hatch from the plain grid-of-cards look */
.float-card{position:absolute;max-width:min(300px,72%);z-index:2;transition:transform .4s ease}
.float-card:hover{transform:translateY(-4px)}
.float-card.bottom-left{left:-1.5rem;bottom:-1.5rem}.float-card.top-right{right:-1.5rem;top:-1.5rem}.float-card.bottom-right{right:-1.5rem;bottom:-1.5rem}
@media (max-width:700px){.float-card{position:static;max-width:none;margin-top:1rem}}
.split-wide{grid-template-columns:1.6fr 1fr}.split-narrow{grid-template-columns:1fr 1.6fr}
.overlap{position:relative}
.overlap-behind{position:absolute;inset:auto auto 0 0;width:70%;transform:translate(-12%,12%);z-index:0;opacity:.55}
.overlap>*:not(.overlap-behind){position:relative;z-index:1}
.bleed{width:100vw;position:relative;left:50%;right:50%;margin-left:-50vw;margin-right:-50vw}
.section-alt{background:color-mix(in srgb,var(--surface) 55%,transparent)}
.big-number{font-size:clamp(4rem,14vw,10rem);font-weight:800;line-height:.85;letter-spacing:-.03em;color:var(--surface-strong);-webkit-text-stroke:1px var(--surface-border)}
.kicker-row{display:flex;align-items:center;gap:1rem}.kicker-row::before{content:"";width:2.5rem;height:2px;background:var(--accent)}

/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.55rem;padding:.85rem 1.5rem;border-radius:999px;border:1px solid var(--surface-border);
  font:inherit;font-weight:650;text-decoration:none;cursor:pointer;color:var(--text);background:var(--surface-strong);
  transition:transform .25s,box-shadow .25s,background .25s;-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
.btn:hover{transform:translateY(-2px)}
.btn-primary{color:var(--on-accent,#fff);border-color:transparent;background:linear-gradient(135deg,var(--accent),var(--accent-2-eff,var(--accent-2)));box-shadow:0 10px 30px color-mix(in srgb,var(--accent) calc(45% * var(--glow)),transparent)}
.btn-primary:hover{box-shadow:0 16px 40px color-mix(in srgb,var(--accent) calc(60% * var(--glow)),transparent)}
.btn-lg{padding:1rem 2rem;font-size:1.05rem}.btn-sm{padding:.55rem 1.1rem;font-size:.88rem}.btn-block{width:100%}
.btn-ghost{background:transparent;border-color:var(--surface-border)}.btn-ghost:hover{background:var(--surface)}
.btn-danger{color:#fff;border-color:transparent;background:var(--bad)}.btn-danger:hover{box-shadow:0 10px 26px color-mix(in srgb,var(--bad) 45%,transparent)}
.btn[disabled],.btn.is-loading{opacity:.6;cursor:not-allowed;transform:none;pointer-events:none}
.btn.is-loading{color:transparent!important}
.btn.is-loading::after{content:"";position:absolute;width:1.1em;height:1.1em;border-radius:50%;border:2px solid currentColor;border-top-color:transparent;color:var(--text);animation:spin .7s linear infinite}
.btn-primary.is-loading::after{color:var(--on-accent,#fff)}.btn-danger.is-loading::after{color:#fff}
.btn{position:relative}
@keyframes spin{to{transform:rotate(360deg)}}
.badge,.pill{display:inline-flex;align-items:center;gap:.4rem;padding:.3rem .8rem;border-radius:999px;font-size:.8rem;font-weight:650;background:var(--surface-strong);border:1px solid var(--surface-border)}

/* stats, pricing, testimonials, lists */
.stat{text-align:center;padding:1.4rem 1rem}.stat b{display:block;font-size:clamp(1.8rem,4vw,2.8rem);font-weight:800;background:linear-gradient(90deg,var(--accent),var(--accent-2));-webkit-background-clip:text;background-clip:text;color:transparent}
.stat span{color:var(--muted);font-size:.95rem}
.price{font-size:clamp(2.2rem,4vw,3rem);font-weight:800;color:var(--text)}.price small{font-size:1rem;color:var(--muted);font-weight:500}
.featured{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent),0 18px 60px color-mix(in srgb,var(--accent) 35%,transparent)}
.check-list{list-style:none;margin:1rem 0 1.4rem;padding:0;display:grid;gap:.6rem}
.check-list li{display:flex;gap:.6rem;align-items:flex-start;color:var(--muted)}.check-list .icon{color:var(--ok);flex:none;margin-top:.2em}
.quote{font-size:1.05rem;color:var(--text)}
.avatar{display:grid;place-items:center;width:46px;height:46px;border-radius:50%;font-weight:800;color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent-2))}
.person{display:flex;align-items:center;gap:.8rem;margin-top:1rem}.person small{display:block;color:var(--muted)}
.tile{aspect-ratio:4/3;border-radius:var(--radius-sm);display:grid;place-items:center;font-weight:700;color:#fff;
  background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 80%,#000),color-mix(in srgb,var(--accent-2) 80%,#000));transition:transform .35s}
.tile:hover{transform:scale(1.03)}
.table-wrap{overflow-x:auto;border-radius:var(--radius)}
.table{width:100%;border-collapse:collapse;min-width:520px}
.table th,.table td{padding:.9rem 1.1rem;text-align:left;border-bottom:1px solid var(--surface-border)}
.table th{font-size:.8rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
.table tr:last-child td{border-bottom:0}

/* forms */
.form{display:grid;gap:1.1rem;padding:clamp(1.25rem,3vw,2.25rem)}
.field{display:grid;gap:.4rem}
.field label{font-weight:600;font-size:.92rem}
.field input,.field select,.field textarea{width:100%;font:inherit;color:var(--text);padding:.85rem 1rem;border-radius:var(--radius-sm);
  border:1px solid var(--surface-border);background:var(--surface);transition:border-color .2s,box-shadow .2s}
.field textarea{min-height:130px;resize:vertical}
.field input::placeholder,.field textarea::placeholder{color:var(--muted);opacity:.8}
.field input:focus,.field select:focus,.field textarea:focus{outline:0;border-color:var(--accent-2);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent-2) 25%,transparent)}
.field select option{color:#111}
.field.invalid input,.field.invalid select,.field.invalid textarea{border-color:var(--bad)}
.error{color:var(--bad);font-size:.85rem;min-height:1.1em}
.alert{padding:1rem 1.25rem;border-radius:var(--radius-sm);border:1px solid var(--surface-border);background:var(--surface-strong)}
.alert-success{border-color:var(--ok);color:var(--ok)}.alert-error{border-color:var(--bad);color:var(--bad)}
.skeleton{border-radius:var(--radius-sm);min-height:1.2em;background:linear-gradient(90deg,var(--surface) 25%,var(--surface-strong) 50%,var(--surface) 75%);background-size:200% 100%;animation:shine 1.4s linear infinite}
@keyframes shine{to{background-position:-200% 0}}
.empty-state{text-align:center;padding:3rem 1rem;color:var(--muted)}
.footer{margin:3rem auto 1.25rem;width:min(100% - 1.5rem,var(--maxw));padding:2rem clamp(1.25rem,3vw,2.5rem)}
.footer-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1.5rem}
.footer a{color:var(--muted);text-decoration:none}.footer a:hover{color:var(--text)}
.footer ul{list-style:none;padding:0;margin:0;display:grid;gap:.5rem}
.copyright{margin-top:1.5rem;padding-top:1.25rem;border-top:1px solid var(--surface-border);font-size:.9rem;color:var(--muted)}

/* accordion (FAQ) */
.accordion{display:grid;gap:.75rem}
.accordion-item{overflow:hidden}
.accordion-item summary{list-style:none;cursor:pointer;padding:1.1rem 1.4rem;display:flex;align-items:center;justify-content:space-between;gap:1rem;font-weight:650}
.accordion-item summary::-webkit-details-marker{display:none}
.accordion-item summary .icon{flex:none;transition:transform .25s}
.accordion-item[open] summary .icon{transform:rotate(45deg)}
.accordion-item .accordion-body{padding:0 1.4rem 1.1rem;color:var(--muted)}

/* tabs */
.tabs{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1.5rem}
.tabs [role="tab"]{padding:.6rem 1.2rem;border-radius:999px;border:1px solid var(--surface-border);background:transparent;color:var(--muted);font:inherit;font-weight:600;cursor:pointer}
.tabs [role="tab"][aria-selected="true"]{color:var(--text);background:var(--surface-strong);border-color:var(--surface-border)}
[role="tabpanel"]{display:none}[role="tabpanel"].active{display:block}

/* timeline */
.timeline{position:relative;display:grid;gap:2rem;padding-left:2.5rem}
.timeline::before{content:"";position:absolute;left:.6rem;top:.3rem;bottom:.3rem;width:2px;background:var(--surface-border)}
.timeline-item{position:relative}
.timeline-item::before{content:"";position:absolute;left:-2.5rem;top:.2rem;width:14px;height:14px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 25%,transparent)}

/* comparison */
.compare{border-collapse:collapse;width:100%}
.compare th,.compare td{padding:.9rem 1.1rem;text-align:center;border-bottom:1px solid var(--surface-border)}
.compare th:first-child,.compare td:first-child{text-align:left}
.compare .yes{color:var(--ok)}.compare .no{color:var(--muted);opacity:.5}

/* motion (data-motion="0": a calm brand can opt out of scroll-reveal entirely; reveal() below skips observing then) */
.reveal{opacity:0;transform:translateY(24px);transition:opacity .7s ease,transform .7s cubic-bezier(.2,.8,.2,1)}
.reveal.in-view{opacity:1;transform:none}
.no-js .reveal,[data-motion="0"] .reveal{opacity:1;transform:none}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}.reveal{opacity:1;transform:none}}
@media (max-width:600px){.btn-lg{width:100%}.hero-actions .btn{width:100%}.section{padding:3rem 0}}
@media (prefers-reduced-transparency:reduce){.glass{-webkit-backdrop-filter:none;backdrop-filter:none;background:var(--bg-2)}}
"""

DESIGN_JS = r"""
/* JARVIS design system behaviour: mobile nav, scroll reveal, active link, theme toggle, form validation. No dependencies. */
(function () {
  'use strict';
  var doc = document;
  doc.documentElement.classList.remove('no-js');
  function safeStore(k, v) { try { if (v === undefined) { return localStorage.getItem(k); } localStorage.setItem(k, v); } catch (e) { return null; } }
  var saved = safeStore('theme');
  if (saved === 'light' || saved === 'dark') { doc.documentElement.setAttribute('data-theme', saved); }

  var toggle = doc.querySelector('.nav-toggle');
  var links = doc.querySelector('.nav-links');
  if (toggle && links) {
    toggle.addEventListener('click', function () {
      var open = links.classList.toggle('open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    links.addEventListener('click', function (e) {
      if (e.target.closest('a')) { links.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); }
    });
  }

  var nav = doc.querySelector('.navbar');
  if (nav) {
    var setScrolled = function () { nav.classList.toggle('scrolled', window.scrollY > 24); };
    setScrolled();
    doc.addEventListener('scroll', setScrolled, { passive: true });
  }

  [].forEach.call(doc.querySelectorAll('.tabs'), function (tabs) {
    var tabEls = [].slice.call(tabs.querySelectorAll('[role="tab"]'));
    tabEls.forEach(function (t) {
      t.addEventListener('click', function () {
        tabEls.forEach(function (o) { o.setAttribute('aria-selected', 'false'); });
        t.setAttribute('aria-selected', 'true');
        var panels = doc.querySelectorAll('[role="tabpanel"]');
        [].forEach.call(panels, function (p) { p.classList.toggle('active', p.id === t.getAttribute('aria-controls')); });
      });
    });
  });

  var counted = new WeakSet();
  var reduceMotion = doc.body.getAttribute('data-motion') === '0' || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  function runCounter(el) {
    if (counted.has(el)) { return; }
    counted.add(el);
    if (reduceMotion) { return; }                                  // text is already the final value in the HTML - nothing to do
    var text = el.textContent || '';
    var m = text.match(/^([^\d]*)(\d[\d,.]*)(.*)$/);
    if (!m) { return; }
    var prefix = m[1], target = parseFloat(m[2].replace(/,/g, '')), suffix = m[3], decimals = (m[2].split('.')[1] || '').length;
    var dur = 1200, t0 = performance.now();
    function tick(t) {
      var p = Math.min(1, (t - t0) / dur);
      var v = target * (1 - Math.pow(1 - p, 3));
      el.textContent = prefix + v.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + suffix;
      if (p < 1) { requestAnimationFrame(tick); }
    }
    requestAnimationFrame(tick);
  }
  var counters = doc.querySelectorAll('[data-counter]');
  if ('IntersectionObserver' in window && counters.length) {
    var co = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { runCounter(en.target); co.unobserve(en.target); } });
    }, { threshold: 0.4 });
    [].forEach.call(counters, function (el) { co.observe(el); });
  } else {
    [].forEach.call(counters, runCounter);
  }

  doc.addEventListener('click', function (e) {                    // demo buttons only: data-demo-loading shows the spinner state, never a real submit's own handling
    var btn = e.target.closest('[data-demo-loading]');
    if (!btn || btn.classList.contains('is-loading')) { return; }
    btn.classList.add('is-loading');
    setTimeout(function () { btn.classList.remove('is-loading'); }, 900);
  });

  var finePointer = window.matchMedia && window.matchMedia('(pointer: fine)').matches && !reduceMotion;
  if (finePointer) {
    [].forEach.call(doc.querySelectorAll('.magnetic'), function (el) {
      el.addEventListener('mousemove', function (e) {
        var r = el.getBoundingClientRect();
        el.style.transform = 'translate(' + ((e.clientX - r.left - r.width / 2) * .25) + 'px,' + ((e.clientY - r.top - r.height / 2) * .25) + 'px)';
      });
      el.addEventListener('mouseleave', function () { el.style.transform = ''; });
    });
    [].forEach.call(doc.querySelectorAll('.tilt'), function (el) {
      el.addEventListener('mousemove', function (e) {
        var r = el.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width - .5, py = (e.clientY - r.top) / r.height - .5;
        el.style.transform = 'perspective(700px) rotateX(' + (py * -6) + 'deg) rotateY(' + (px * 6) + 'deg) translateZ(4px)';
      });
      el.addEventListener('mouseleave', function () { el.style.transform = ''; });
    });
  }

  [].forEach.call(doc.querySelectorAll('.sticky-story'), function (story) {
    var steps = [].slice.call(story.querySelectorAll('.story-step'));
    if (!steps.length || !('IntersectionObserver' in window)) { steps.forEach(function (s) { s.classList.add('active'); }); return; }
    var so2 = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { steps.forEach(function (s) { s.classList.toggle('active', s === en.target); }); } });
    }, { rootMargin: '-40% 0px -40% 0px' });
    steps.forEach(function (s) { so2.observe(s); });
  });

  var themeBtn = doc.querySelector('[data-theme-toggle]');
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var next = doc.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      doc.documentElement.setAttribute('data-theme', next);
      safeStore('theme', next);
    });
  }

  var reveals = doc.body.getAttribute('data-motion') === '0' ? [] : doc.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window && reveals.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add('in-view'); io.unobserve(en.target); } });
    }, { threshold: 0.12 });
    reveals.forEach(function (el) { io.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add('in-view'); });
  }

  (function () {                                            // multi-page site: highlight the link of the page we are ON (works for a shared Jinja base.html too)
    var here = location.pathname.replace(/\/index\.html?$/, '/').replace(/\.html?$/, '').replace(/\/$/, '') || '/';
    [].forEach.call(doc.querySelectorAll('.nav-links a[href]'), function (a) {
      var h = a.getAttribute('href');
      if (!h || h.charAt(0) === '#' || /^[a-z]+:/i.test(h)) { return; }
      var p = new URL(h, location.href).pathname.replace(/\/index\.html?$/, '/').replace(/\.html?$/, '').replace(/\/$/, '') || '/';
      if (p === here) { a.setAttribute('aria-current', 'page'); a.classList.add('active'); }
    });
  })();

  var sections = [].slice.call(doc.querySelectorAll('main section[id], section[id]'));
  var navAnchors = [].slice.call(doc.querySelectorAll('.nav-links a[href^="#"]'));
  if (sections.length && navAnchors.length && 'IntersectionObserver' in window) {
    var so = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          navAnchors.forEach(function (a) { a.classList.toggle('active', a.getAttribute('href') === '#' + en.target.id); });
        }
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    sections.forEach(function (s) { so.observe(s); });
  }

  [].forEach.call(doc.querySelectorAll('[data-year]'), function (el) { el.textContent = new Date().getFullYear(); });

  doc.addEventListener('error', function (e) {                    // a photo that fails to load (offline/blocked/slow) never leaves broken-image icons or unreadable overlay text
    var img = e.target;
    if (img.tagName === 'IMG') {
      var frame = img.closest('.photo-frame');
      if (frame) { frame.classList.add('photo-fallback'); }
    }
  }, true);

  var emailRx = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  function fieldError(field, msg) {
    var wrap = field.closest('.field') || field.parentElement;
    var box = wrap.querySelector('.error');
    if (!box) { box = doc.createElement('div'); box.className = 'error'; box.setAttribute('role', 'alert'); wrap.appendChild(box); }
    box.textContent = msg || '';
    wrap.classList.toggle('invalid', !!msg);
    field.setAttribute('aria-invalid', msg ? 'true' : 'false');
  }
  function check(field) {
    var v = (field.value || '').trim();
    if (field.required && !v) { return 'This field is required.'; }
    if (v && field.type === 'email' && !emailRx.test(v)) { return 'Please enter a valid email address.'; }
    if (v && field.type === 'tel' && !/^[+()\d\s-]{7,}$/.test(v)) { return 'Please enter a valid phone number.'; }
    if (v && field.minLength > 0 && v.length < field.minLength) { return 'Please enter at least ' + field.minLength + ' characters.'; }
    return '';
  }
  [].forEach.call(doc.querySelectorAll('form'), function (form) {
    form.setAttribute('novalidate', '');
    form.addEventListener('blur', function (e) { if (e.target.matches && e.target.matches('input,select,textarea')) { fieldError(e.target, check(e.target)); } }, true);
    form.addEventListener('submit', function (e) {
      var bad = null;
      [].forEach.call(form.querySelectorAll('input,select,textarea'), function (f) {
        var msg = check(f); fieldError(f, msg); if (msg && !bad) { bad = f; }
      });
      if (bad) { e.preventDefault(); bad.focus(); return; }
      var api = form.getAttribute('data-api');
      var okMsg = form.getAttribute('data-success');
      if (api) {                                  /* real backend: POST as JSON, show the SERVER's own message/errors - never fake a result */
        e.preventDefault();
        var btn = form.querySelector('[type="submit"]');
        var note = function (cls, text) {
          [].forEach.call(form.querySelectorAll('.alert'), function (n) { n.remove(); });
          var n = doc.createElement('div'); n.className = 'alert ' + cls; n.setAttribute('role', 'status'); n.textContent = text; form.appendChild(n);
        };
        var data = {};
        [].forEach.call(form.querySelectorAll('input[name],select[name],textarea[name]'), function (f) { data[f.name] = f.value; });
        if (btn) { btn.classList.add('is-loading'); btn.disabled = true; }
        fetch(api, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (body) { return { ok: r.ok, body: body }; }); })
          .then(function (res) {
            if (res.ok) { note('alert-success', (res.body && res.body.message) || 'Thank you! We will be in touch shortly.'); form.reset(); }
            else { note('alert-error', (res.body && res.body.message) || 'Something went wrong, please try again.'); }
          })
          .catch(function () { note('alert-error', 'Could not reach the server. Please check your connection and try again.'); })
          .finally(function () { if (btn) { btn.classList.remove('is-loading'); btn.disabled = false; } });
      } else if (okMsg !== null) {                 /* a form with nowhere real to go (e.g. a pure newsletter widget): confirm instead of a dead submit */
        e.preventDefault();
        var note2 = form.querySelector('.alert-success') || doc.createElement('div');
        note2.className = 'alert alert-success'; note2.setAttribute('role', 'status'); note2.textContent = okMsg || 'Thank you! We will be in touch shortly.';
        form.appendChild(note2); form.reset();
      }
    });
  });
})();
"""

_ICONS = {
    "menu": '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "arrow-right": '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "mail": '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>',
    "map-pin": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "heart": '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "cart": '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "chart": '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
    "award": '<circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>',
    "coffee": '<path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8z"/><line x1="6" y1="1" x2="6" y2="4"/><line x1="10" y1="1" x2="10" y2="4"/><line x1="14" y1="1" x2="14" y2="4"/>',
    "activity": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "package": '<line x1="16.5" y1="9.4" x2="7.5" y2="4.21"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
    "code": '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    "dumbbell": '<path d="M6.5 6.5v11"/><path d="M17.5 6.5v11"/><path d="M3 9.5v5"/><path d="M21 9.5v5"/><line x1="6.5" y1="12" x2="17.5" y2="12"/><line x1="3" y1="12" x2="6.5" y2="12"/><line x1="17.5" y1="12" x2="21" y2="12"/>',
    "flame": '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>',
    "trophy": '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2z"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "leaf": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>',
    "smile": '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>',
    "camera": '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
    "gift": '<polyline points="20 12 20 22 4 22 4 12"/><rect x="2" y="7" width="20" height="5"/><line x1="12" y1="22" x2="12" y2="7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/>',
    "utensils": '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3zm0 0v7"/>',
    "facebook": '<path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"/>',
    "instagram": '<rect x="2" y="2" width="20" height="20" rx="5" ry="5"/><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/><line x1="17.5" y1="6.5" x2="17.51" y2="6.5"/>',
    "twitter": '<path d="M23 3a10.9 10.9 0 0 1-3.14 1.53 4.48 4.48 0 0 0-7.86 3v1A10.66 10.66 0 0 1 3 4s-4 9 5 13a11.64 11.64 0 0 1-7 2c9 5 20 0 20-11.5a4.5 4.5 0 0 0-.08-.83A7.72 7.72 0 0 0 23 3z"/>',
}
ICON_ALIASES = {                                   # names models commonly invent -> the closest icon that really exists
    "weight": "dumbbell", "weights": "dumbbell", "gym": "dumbbell", "fitness": "dumbbell", "strength": "dumbbell", "bootcamp": "flame",
    "fire": "flame", "hiit": "flame", "cardio": "activity", "run": "activity", "yoga": "leaf", "meditation": "leaf", "wellness": "leaf",
    "spa": "leaf", "dance": "music", "trainer": "user", "coach": "user", "team": "users", "community": "users", "medal": "award",
    "trophy-cup": "trophy", "time": "clock", "schedule": "calendar", "location": "map-pin", "email": "mail", "call": "phone",
    "food": "utensils", "menu-food": "utensils", "cup": "coffee", "photo": "camera", "gallery": "camera", "x": "twitter",
    "security": "shield", "safe": "shield", "price": "package", "offer": "gift", "happy": "smile", "love": "heart", "rating": "star",
}
ICON_NAMES = sorted(_ICONS)


def icons_svg():
    body = "".join(f'<symbol id="i-{n}" viewBox="0 0 24 24">{p}</symbol>' for n, p in _ICONS.items())
    return f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>\n'


def _hex_ok(v):
    return isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v.strip()) is not None


def _hsl_hex(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def _lum(hx):
    c = [int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable(color, bg, minimum=3.0):
    """Keep the model's hue but move lightness until it is readable on the page background. Real bug seen live: a light
    theme got accent2 ~#f5f0d0, so gradient headings, eyebrows and gradient buttons faded into the background."""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    step = -0.03 if _lum(bg) > 0.4 else 0.03
    out = color
    for _ in range(34):
        if contrast(out, bg) >= minimum:
            return out
        l = min(1.0, max(0.0, l + step))
        rr, gg, bb = colorsys.hls_to_rgb(h, l, max(s, 0.55))
        out = "#{:02x}{:02x}{:02x}".format(int(rr * 255), int(gg * 255), int(bb * 255))
    return out


_RADIUS_SCALE = {"sharp": {"radius": "6px", "radius-sm": "3px"}, "soft": {"radius": "20px", "radius-sm": "10px"}, "pill": {"radius": "28px", "radius-sm": "16px"}}
_SERIF = '"Fraunces","Georgia",serif'
_SANS = '"Inter","Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif'


def resolve_style(plan_name, theme):
    """The plan's chosen visual language, or a stable (never random) fallback derived from the project name."""
    theme = theme if isinstance(theme, dict) else {}
    key = str(theme.get("style") or "").lower()
    if key not in VISUAL_STYLES:
        h = int(hashlib.sha1((plan_name or "x").encode()).hexdigest()[4:8], 16)
        key = list(VISUAL_STYLES)[h % len(VISUAL_STYLES)] if not key else DEFAULT_STYLE
    return key, VISUAL_STYLES[key]


def theme_css(name, theme):
    """Full design-language token sheet: brand colours (model-chosen or a stable hash-derived hue) + the chosen visual
    style's surfaces/radius/shadow/motion + optional heading-font/radius/density overrides. This is where a gym, a
    restaurant and a SaaS product genuinely diverge - not just in colour."""
    theme = theme if isinstance(theme, dict) else {}
    style_key, sv = resolve_style(name, theme)
    h = int(hashlib.sha1((name or "x").encode()).hexdigest()[:4], 16) % 360
    a1 = theme.get("accent") if _hex_ok(theme.get("accent")) else _hsl_hex(h, 0.85, 0.62)
    a2 = theme.get("accent2") if _hex_ok(theme.get("accent2")) else _hsl_hex((h + 55) % 360, 0.85, 0.58)
    mode = str(theme.get("mode", "")).lower() or ("light" if style_key == "editorial" else "dark")
    head_font = _SERIF if str(theme.get("headingFont", "")).lower() == "serif" or style_key in ("editorial", "elegant") else _SANS
    radius = dict(_RADIUS_SCALE.get(str(theme.get("radius", "")).lower(), {"radius": sv["radius"], "radius-sm": sv["radius-sm"]}))
    density = 1.0 if str(theme.get("density", "")).lower() != "compact" else 0.72
    if style_key == "elegant":
        bg = "#0c0b09"
    elif mode == "light":
        bg = "#f7f5f2" if style_key == "editorial" else "#eef2ff"
    else:
        bg = "#0a0e1f"
    if contrast(a2, bg) < 1.6:                                                  # accent2 is basically the background colour (e.g. #f1faee on light): its hue means
        r, g, b = (int(a1[i:i + 2], 16) / 255 for i in (1, 3, 5))              # nothing, so derive a harmonious neighbour of accent1 instead of a clashing one
        hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
        a2 = _hsl_hex((hh * 360 + 28) % 360, max(ss, 0.7), ll)
    a1, a2 = readable(a1, bg), readable(a2, bg)                                 # both ends of every gradient must stay visible on this background

    tokens = {"accent": a1, "accent-2": a2, "surface": sv["surface"], "surface-strong": sv["surface-strong"], "surface-border": sv["border"],
              "blur": sv["blur"], "shadow": sv["shadow"], "shadow-hover": sv["shadow"], "gradient": sv["gradient"], "glow": sv["glow"],
              "motion": sv["motion"], "orbs": sv["bg-orbs"], "font-head": head_font, **radius,
              "on-accent": "#fff" if min(contrast("#ffffff", a1), contrast("#ffffff", a2)) >= 2.6 else "#12131a"}   # button/icon text readable on BOTH gradient ends
    if sv["gradient"] == "0":
        tokens["accent-2-eff"] = a1                                             # flat styles: solid accent instead of a two-colour gradient
    css = ":root{" + "".join(f"--{k}:{v};" for k, v in tokens.items()) + "}\n"
    css += f'body{{--section-scale:{density}}}\n.section{{padding-block:calc(clamp(3.5rem,8vw,7rem) * var(--section-scale))}}\n'
    if mode == "light":
        css += (':root{--bg-1:#f7f5f2;--bg-2:#fdfbf8;--text:#1c1710;--muted:#6b6155;}\n' if style_key == "editorial" else
                ':root{--bg-1:#eef2ff;--bg-2:#fdf2f8;--text:#151a2e;--muted:#56618a;}\n')
        if style_key not in ("editorial",):
            css += ':root{--surface:rgba(255,255,255,.55);--surface-strong:rgba(255,255,255,.78);--surface-border:rgba(255,255,255,.9);--shadow:0 12px 44px rgba(60,70,130,.18);}\n'
    if style_key == "elegant":
        css += ':root{--bg-1:#0c0b09;--bg-2:#17140f;--text:#f3efe6;--muted:#a79f8d;}\n.brand{letter-spacing:.06em;text-transform:uppercase;font-size:1.05rem}\n'
    return css, style_key, mode, a1, a2


def install(root, asset_dir, name, theme):
    """Writes design-system.css, app.js, icons.svg, theme.css into root/asset_dir. Returns (written_paths, style_key, mode,
    accent, accent2) so the caller can set <body data-style=.. data-theme=..>, brief the model, and reuse the same
    brand colours for the favicon instead of re-deriving them."""
    d = os.path.join(root, asset_dir)
    os.makedirs(d, exist_ok=True)
    css, style_key, mode, accent, accent2 = theme_css(name, theme)
    files = {"design-system.css": DESIGN_CSS, "app.js": DESIGN_JS, "icons.svg": icons_svg(), "theme.css": css}
    out = []
    for fn, content in files.items():
        util.write_files(root, [{"path": f"{asset_dir}/{fn}", "content": content}])
        out.append(f"{asset_dir}/{fn}")
    return out, style_key, mode, accent, accent2


def guide(asset_base, style_key, mode, seo_guide=""):
    """Class reference given to the model so pages use the design system instead of inventing CSS. The referenced
    classes render differently per project because theme.css sets different tokens - the model does not need to know
    which visual language it is in, except for the body attributes and the tone note below."""
    a = asset_base
    sv = VISUAL_STYLES[style_key]
    body_attrs = f'data-style="{style_key}" data-theme="{mode}" data-gradient="{sv["gradient"]}" data-motion="{sv["motion"]}"'
    return (
        "DESIGN SYSTEM (already written for you - DO NOT rewrite it, do NOT write your own base CSS, do NOT hardcode colours/radius/shadow - always use the CSS variables/classes below so the chosen visual language stays consistent). "
        f"Visual language for this brand: \"{style_key}\" - {sv['desc']}. Write copy, imagery choices and density to match this mood. Every page <head> must contain: "
        f'<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>..</title><meta name="description" content="..">'
        f'<link rel="stylesheet" href="{a}design-system.css"><link rel="stylesheet" href="{a}theme.css"> and before </body>: <script src="{a}app.js" defer></script>. '
        f'<html lang="en" class="no-js">. <body {body_attrs}>, starting with <a class="skip-link" href="#main">Skip to content</a>. '
        'Layout: <header><nav class="navbar surface" aria-label="Main"><a class="brand" href="#top">Name</a><button class="nav-toggle" aria-label="Menu" aria-expanded="false">'
        f'<svg class="icon" aria-hidden="true"><use href="{a}icons.svg#i-menu"></use></svg></button><ul class="nav-links"><li><a href="#about">About</a></li>..</ul></nav></header>'
        '<main id="main"> sections: <section class="section" id="..">. <div class="container"> wraps content. Section intro: <div class="section-head"><span class="eyebrow">..</span><h2>..</h2><p class="lead">..</p></div>. '
        'Hero: <section class="hero"><div class="container"><div class="hero-inner"><span class="badge">..</span><h1>text <span class="gradient-text">key words</span></h1><p class="lead">..</p>'
        '<div class="hero-actions"><a class="btn btn-primary btn-lg" href="#..">..</a><a class="btn btn-lg" href="#..">..</a></div></div></div></section> '
        '(for a two-column hero use class "hero hero-left" + <div class="split"> with the text block and <div class="hero-visual surface"></div> for an ABSTRACT gradient visual - '
        'NEVER put an <img> inside .hero-visual, it has no image styling; for a REAL photo instead use <div class="hero-visual photo-frame"><img ..></div>, see Images below). '
        'AVOID making every section the same "heading + 3 equal cards" layout - vary the composition: a class="split split-wide" or "split-narrow" for an asymmetric two-column section (not always 50/50); '
        'a class="hero-visual" wrapped in class="overlap" with one or two <div class="surface card float-card bottom-left">stat or quote</div> floating on top of it (position: relative parent, the float-card '
        'positions itself absolutely - only inside a container with real height like hero-visual); a large class="big-number" (e.g. "01", "$2.4M", "10K+") beside a stat or step for scale/impact; '
        'class="kicker-row" before an eyebrow/heading for a designed accent instead of plain text; class="section-alt" on alternating sections so the page has rhythm instead of one flat background throughout; '
        'class="bleed" on a full-width band (e.g. a stats strip or a quote) that breaks out of the normal container width. Use 2-3 of these across the page where they genuinely fit the content - not on every section. '
        'Interaction polish, sparingly (only where it adds value, e.g. a hero CTA or a gallery, never on every element): add class="magnetic" to a <button>/<a> for a subtle pull-toward-cursor effect; '
        'add class="tilt" to a card/image container (needs real height, e.g. a card or hero-visual) for a slight 3D tilt on hover - both do nothing on touch devices and are skipped under reduced motion automatically. '
        'A horizontally-scrolling row (e.g. a gallery, menu items, testimonials): <div class="h-scroll"><div class="surface card">..</div>..</div> - each direct child becomes one snapping slide. '
        'A sticky scroll-story (a fixed image/visual on one side while text steps scroll past on the other - use for a process/story with 3-5 real steps, not for every page): '
        '<div class="sticky-story"><div class="sticky-visual surface"></div><div><div class="story-step"><h3>Step</h3><p>..</p></div><div class="story-step">..</div></div></div> '
        '(only the .sticky-visual and the .story-step wrapper are fixed structure - fill both with real content).'
        'Cards: <div class="grid grid-3"><article class="surface card reveal"><div class="card-icon"><svg class="icon" aria-hidden="true">'
        f'<use href="{a}icons.svg#i-zap"></use></svg></div><h3>..</h3><p>..</p></article>..</div> (grid-2 / grid-3 / grid-4). '
        f'Available icon ids (use as #i-NAME): {", ".join(ICON_NAMES)}. Stats: <div class="surface stat"><b>500+</b><span>Members</span></div>. '
        'Pricing: <div class="surface card featured"><h3>Plan</h3><div class="price">$29<small>/mo</small></div><ul class="check-list"><li><svg class="icon"><use href="' + a + 'icons.svg#i-check"></use></svg>Feature</li></ul><a class="btn btn-primary btn-block" href="#contact">Choose</a></div>. '
        'Testimonial: <figure class="surface card"><blockquote class="quote">".."</blockquote><figcaption class="person"><span class="avatar">AB</span><span>Name<small>Role</small></span></figcaption></figure>. '
        'Gallery tiles: <div class="tile">Label</div> for a pure colour tile, or <div class="photo-frame tile-photo"><img src="..." alt="..." loading="lazy"></div> for a photo tile - see Images below. '
        'Table: <div class="table-wrap surface"><table class="table">..</table></div>. '
        'IMAGES: real photography is allowed and encouraged where it fits the business (restaurant, gym, hotel, product, portfolio, travel - most consumer-facing brands). '
        'Source: https://picsum.photos/seed/SEED/1600/900 - SEED is a short unique slug per image (e.g. "beanbliss-hero", "beanbliss-latte", "beanbliss-interior" - vary it per slot so photos '
        'on the same page differ). After generation JARVIS swaps each one for a real on-topic stock photo SEARCHED BY ITS ALT TEXT, so the alt text is the search query: '
        'write a short concrete scene of exactly what that slot should show for THIS business (e.g. "woman lifting dumbbells in a gym", "barista pouring latte art"), '
        'never generic words like "hero image" - and never claim it is literally the owner/staff (say "a coach guiding a client", not "our head coach Alex"). '
        'ALWAYS use this exact structure, never a bare <img>: '
        '<div class="photo-frame hero-photo"><img src="https://picsum.photos/seed/beanbliss-hero/1600/900" alt="Describe the scene" loading="lazy"></div> '
        '(the design system\'s own JS hides the <img> and keeps the gradient behind it if the photo fails to load - this is automatic, do not add onerror yourself). '
        'For a full-bleed photo hero, wrap it in <div class="bleed"><div class="photo-frame hero-photo full-bleed"><img ..><div class="photo-overlay"><h1>..</h1><p class="lead">..</p></div></div></div> - '
        'text MUST be inside .photo-overlay when placed over a photo, never bare on top of it. A photo grid/gallery: <div class="grid grid-3"><div class="photo-frame tile-photo"><img ..></div>..</div>. '
        'Use real, specific alt text describing the actual photo content (not the business name again). Do not overuse photography - a SaaS/dev-tool/dashboard product should stay CSS-only (no photos fit it).'
        'Forms with NO backend planned (e.g. a pure newsletter widget): <form class="surface form" data-success="Thanks! We will reply soon.">.. '
        'Forms that reach the business (reservation/contact/join/order/review - see the backend requirement above) MUST instead use data-api="/api/whatever" (no data-success): '
        '<form class="surface form" data-api="/api/reserve"><div class="field"><label for="n">Name</label><input id="n" name="name" required autocomplete="name"></div>'
        '<button class="btn btn-primary" type="submit">Reserve</button></form> - app.js POSTs it as JSON automatically (field names = each input\'s "name"), shows a loading spinner on the button, and '
        'renders the server\'s own {"message": "..."} on success/error - build the matching Flask route exactly as the "contract" describes. '
        '(also type=email / tel, select, textarea). app.js validates all forms (required/email/phone) client-side first and shows .error messages. '
        'Footer: <footer class="footer surface"><div class="footer-grid">..columns with <ul>..</ul></div><p class="copyright">&copy; <span data-year></span> Name. All rights reserved.</p></footer>. '
        'Trust strip (a compact row of 3-4 short reassurances right after the hero, e.g. "100% Organic", "Free shipping", "24/7 support" - common on consumer/product/service brands): '
        '<section class="section section-alt"><div class="container"><div class="grid grid-3 center">'
        '<div><div class="card-icon" style="margin-inline:auto"><svg class="icon"><use href="' + a + 'icons.svg#i-check"></use></svg></div><h3>Claim</h3><p>One short line.</p></div>..</div></div></section>. '
        'Add class "reveal" to cards/sections for scroll animations - it is disabled automatically for this style if the mood calls for stillness. Loading/empty states: .skeleton, .empty-state. Alerts: .alert .alert-success/.alert-error. '
        'Theme toggle (optional): <button class="btn" data-theme-toggle>. Utilities: .center .muted .flex .between .stack .justify-center .pill. '
        'You MAY add a small page-specific <style> or style.css for extra touches (e.g. a unique hero layout, a decorative divider), but never hardcode a colour/radius/shadow value - always reference '
        '(--accent, --accent-2, --text, --muted, --surface, --surface-border, --radius, --shadow, --font-head). Write REAL, specific, persuasive copy for this exact request '
        '(real-sounding names, numbers, prices, hours, testimonials) - never lorem ipsum. '
        'Accordion/FAQ: <div class="accordion"><details class="surface accordion-item"><summary>Question<svg class="icon"><use href="' + a + 'icons.svg#i-arrow-right"></use></svg></summary><div class="accordion-body">Answer.</div></details></div>. '
        'Tabs: <div class="tabs" role="tablist"><button role="tab" aria-selected="true" aria-controls="t1">One</button><button role="tab" aria-selected="false" aria-controls="t2">Two</button></div>'
        '<div id="t1" role="tabpanel" class="active">..</div><div id="t2" role="tabpanel">..</div>. '
        'Timeline: <div class="timeline"><div class="timeline-item"><h3>2019</h3><p>..</p></div>..</div>. Comparison: <table class="compare"><tr><th></th><th>Basic</th><th>Pro</th></tr>'
        '<tr><td>Feature</td><td><svg class="icon compare-yes"><use href="' + a + 'icons.svg#i-check"></use></svg></td><td class="no">&mdash;</td></tr></table>. '
        'Animated number: <div class="stat"><b data-counter>500+</b><span>Members</span></div> (counts up when scrolled into view - keep the exact text format, e.g. "500+" or "$29"). '
        'Button states: .btn-ghost (secondary, no fill), .btn-danger (destructive actions), .btn-sm/.btn-lg (size). A button that demonstrates a loading state can add data-demo-loading (only for buttons with no real navigation/submit target). '
        + (seo_guide + " " if seo_guide else "")
    )
