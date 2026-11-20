"""Build the Melee Weapons For All player site.

    python site/build.py            # writes site/_build
    python site/build.py --serve    # builds, then serves it on http://localhost:8000
    python site/build.py --out DIR  # writes somewhere else (CI uses this)

Pages are the Markdown files in site/content. They can call the helpers in
Helpers below, e.g. {{ melee_tables() }} or {{ screenshot("name", "caption") }},
to pull generated tables and pictures into the text.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import re
import shutil
import sys
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from lxml import etree
from markupsafe import Markup, escape

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
sys.path.insert(0, str(SITE))

from gen.defs import DefDB, make_scenarios, read_load_folders  # noqa: E402
from gen.external import find_ce, refresh  # noqa: E402
from gen.extract import TIERS, Extractor  # noqa: E402
from gen.textures import Textures  # noqa: E402

GEN = SITE / "gen"
EXTERNAL_PARENTS = GEN / "external_parents.xml"
BUILD_MARKER = ".melee-site-build"  # written into the output so a rebuild only ever deletes its own folder

# Top bar, in reading order: (slug, title).
NAV = [
    ("weapons", "Weapons"),
    ("shields", "Shields"),
    ("research", "Research"),
    ("beyond-our-reach", "With Beyond Our Reach"),
    ("credits", "Credits"),
]

GITHUB = "https://github.com/bambaryla-mods/AllBambaMelee"
BOR_SITE = "https://bambaryla-mods.github.io/RimworldBeyondOurReach/"
SITE_URL = "https://bambaryla-mods.github.io/AllBambaMelee/"  # for link previews
SCREENSHOT_EXTS = (".png", ".jpg", ".jpeg", ".webp")
FAVICON = "Textures/Bamba_Melee/Weapons/Gladius_Solar.png"


def fmt_num(v, digits: int = 2) -> str:
    """1234.50 -> "1,234.5", 10000 with digits=0 -> "10,000", -0.3 -> "−0.3"."""
    if v is None:
        return "—"
    s = f"{v:,.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.replace("-", "−")


def fmt_pct(v) -> str:
    return "—" if v is None else f"{v * 100:,.0f}%"


_RICH_TAG = re.compile(r"</?(color|b|i|size)(=[^>]*)?>", re.I)


def clean_desc(text: str | None) -> str:
    """Game descriptions use literal \\n and Unity rich-text tags."""
    if not text:
        return ""
    return _RICH_TAG.sub("", text.replace("\\n", "\n")).strip()


def fmt_desc(text: str | None) -> Markup:
    paras = [p.strip() for p in re.split(r"\n\s*\n", clean_desc(text)) if p.strip()]
    return Markup("<br><br>").join(Markup("<br>").join(escape(x) for x in p.split("\n")) for p in paras)


def fmt_desc_plain(text: str | None) -> str:
    return " ".join(clean_desc(text).split())


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def split_front_matter(text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        for line in head.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
        return meta, body
    return meta, text


class Helpers:
    """Functions the Markdown pages can call. Each returns a placeholder that
    is swapped for real HTML after Markdown runs, so Markdown never mangles it."""

    def __init__(self, builder: "Builder"):
        self.b = builder
        self.uses_modes = False

    def _stash(self, html: str) -> Markup:
        key = f"MELEEBLOCK{len(self.b.all_blocks)}X"
        self.b.all_blocks[key] = html
        return Markup(f"\n\n{key}\n\n")

    def _macro(self, name: str, **kw) -> Markup:
        return self._stash(str(getattr(self.b.macros, name)(**kw)))

    def melee_tables(self):
        self.uses_modes = True
        return self._macro("melee_tables", groups=self.b.data("melee"))

    def shield_tables(self):
        return self._macro("shield_tables", groups=self.b.data("shields"))

    def research_tables(self):
        self.uses_modes = True
        return self._macro("research_tables", vanilla=self.b.data("research"), ce=self.b.data("research_ce"))

    def tier_icons(self, *tiers: str):
        """One icon per weapon family for the given tiers, e.g. on the BOR page."""
        items = [it for g in self.b.data("melee") if not tiers or g.tier in tiers for it in g.items]
        return self._macro("icon_strip", items=items)

    def screenshot(self, name: str, caption: str = ""):
        for ext in SCREENSHOT_EXTS:
            src = SITE / "screenshots" / f"{name}{ext}"
            if src.is_file():
                url = f"assets/screenshots/{name}{ext}"
                self.b.copy_asset(src, url)
                return self._macro("screenshot", src=url, caption=caption)
        self.b.missing_shots.append((self.b.current_page, name, caption))
        return Markup("")


class Builder:
    def __init__(self, out: Path):
        self.out = out
        self.problems: list[str] = []
        self.missing_shots: list[tuple[str, str, str]] = []
        self.all_blocks: dict[str, str] = {}
        self.current_page = ""
        self._data: dict[str, object] = {}

        folders = read_load_folders(ROOT)
        scenarios = make_scenarios()
        if not EXTERNAL_PARENTS.is_file():
            self.problems.append(f"{EXTERNAL_PARENTS.relative_to(ROOT)} is missing; run with --refresh-parents "
                                 "where the game is installed, or stats inherited from vanilla/CE will be blank")
        dbs = {name: DefDB(ROOT, folders, scenarios[name], EXTERNAL_PARENTS) for name in ("vanilla", "ce")}
        for db in dbs.values():
            self.problems += [f"[{db.scenario.name}] {w}" for w in db.warnings]
        self.tex = Textures(ROOT, [f.path for f in folders], out / "assets" / "icons")
        self.ex = Extractor(dbs, folders, self.tex)

        self.env = Environment(
            loader=FileSystemLoader(str(SITE / "templates")),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters.update(num=fmt_num, pct=fmt_pct, desc=fmt_desc, desc_plain=fmt_desc_plain, slug=slugify)
        self.env.globals.update(tiers=TIERS)
        self.macros = self.env.get_template("macros.html").module
        self.md_env = Environment(autoescape=False, undefined=StrictUndefined)

    def data(self, kind: str):
        if kind not in self._data:
            self._data[kind] = {"melee": self.ex.melee_weapons, "shields": self.ex.shields,
                                "research": lambda: self.ex.research("vanilla"),
                                "research_ce": lambda: self.ex.research("ce")}[kind]()
        return self._data[kind]

    def about(self) -> dict:
        about = etree.parse(str(ROOT / "About" / "About.xml")).getroot()
        pid = (ROOT / "About" / "PublishedFileId.txt").read_text(encoding="utf-8").strip()
        return {
            "name": (about.findtext("name") or "").strip(),
            "versions": [(li.text or "").strip() for li in about.findall("supportedVersions/li")],
            "workshop": f"https://steamcommunity.com/sharedfiles/filedetails/?id={pid}",
        }

    def copy_asset(self, src: Path, rel: str):
        dst = self.out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)

    def render_markdown(self, slug: str) -> tuple[dict, str, list, bool]:
        self.current_page = slug
        meta, body = split_front_matter((SITE / "content" / f"{slug}.md").read_text(encoding="utf-8"))
        helpers = Helpers(self)
        g = {name: getattr(helpers, name) for name in dir(helpers)
             if not name.startswith("_") and callable(getattr(helpers, name))}
        text = self.md_env.from_string(body).render(**g)
        md = markdown.Markdown(extensions=["toc", "tables", "attr_list", "md_in_html"],
                               extension_configs={"toc": {"toc_depth": "2-3"}})
        html = md.convert(text)
        for key, block in self.all_blocks.items():
            html = html.replace(f"<p>{key}</p>", block).replace(key, block)
        toc = [{"id": t["id"], "name": t["name"]} for t in md.toc_tokens if t["level"] == 2]
        return meta, html, toc, helpers.uses_modes

    def prepare_out(self):
        """Empty the output folder, but only if this builder made it."""
        out = self.out
        if out in (ROOT, SITE) or out in ROOT.parents:
            raise SystemExit(f"Refusing to build into {out}: pick an empty or new folder.")
        if out.exists():
            if not (out / BUILD_MARKER).is_file() and any(out.iterdir()):
                raise SystemExit(f"Refusing to delete {out}: it isn't empty and wasn't made by this builder.")
            shutil.rmtree(out)
        (out / "assets").mkdir(parents=True)
        (out / BUILD_MARKER).write_text("Output of site/build.py. Safe to delete.\n", encoding="utf-8")

    def build(self):
        self.prepare_out()
        shutil.copytree(SITE / "static", self.out / "assets", dirs_exist_ok=True)
        self.copy_asset(ROOT / "About" / "Preview.png", "assets/preview.png")
        self.copy_asset(ROOT / FAVICON, "assets/favicon.png")

        about = self.about()
        common = {"nav": NAV, "mod": about, "workshop": about["workshop"], "github": GITHUB,
                  "bor_site": BOR_SITE, "site_url": SITE_URL}
        cards = []
        for i, (slug, title) in enumerate(NAV):
            meta, html, toc, uses_modes = self.render_markdown(slug)
            page = {"slug": slug, "title": title}
            out_html = self.env.get_template("page.html").render(
                **common, page=page, meta=meta, content=Markup(html), toc=toc, active=slug, uses_modes=uses_modes,
                prev=dict(zip(("slug", "title"), NAV[i - 1])) if i > 0 else None,
                next=dict(zip(("slug", "title"), NAV[i + 1])) if i + 1 < len(NAV) else None,
            )
            (self.out / f"{slug}.html").write_text(out_html, encoding="utf-8")
            cards.append({**page, "summary": meta.get("summary", ""), "icons": self.card_icons(meta.get("card", ""))})

        meta, html, _, _ = self.render_markdown("index")
        index = self.env.get_template("home.html").render(**common, meta=meta, content=Markup(html), cards=cards,
                                                          active="")
        (self.out / "index.html").write_text(index, encoding="utf-8")
        (self.out / ".nojekyll").write_text("", encoding="utf-8")

    def card_icons(self, spec: str) -> list[str]:
        """Front matter "card:" lists texture paths for the home page thumbnail."""
        srcs = [self.tex.export(self.tex.find(p.strip())) for p in spec.split(",") if p.strip()]
        if not all(srcs):
            self.problems.append(f"{self.current_page}: a card texture in {spec!r} was not found")
        return [s for s in srcs if s]

    def report(self) -> int:
        """Print problems and missing screenshots; return the problem count."""
        notes = self.problems + _uniq_list(self.ex.notes)
        if notes:
            print(f"{len(notes)} problem(s):")
            for n in notes:
                print("  -", n)
        if self.missing_shots:
            print(f"{len(self.missing_shots)} screenshot(s) not added yet (put them in site/screenshots/):")
            for page, name, caption in self.missing_shots:
                print(f"  - {name}.png  ({page}: {caption})")
        print(f"Built {self.out}")
        return len(notes)


def _uniq_list(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


def serve(out: Path, port: int):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Serving on http://localhost:{port}/ (Ctrl+C to stop)")
        httpd.serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=SITE / "_build")
    ap.add_argument("--serve", nargs="?", const=8000, type=int, metavar="PORT")
    ap.add_argument("--strict", action="store_true", help="exit with an error if the build reports problems (CI)")
    ap.add_argument("--refresh-parents", action="store_true",
                    help="re-copy the vanilla/DLC/CE base defs into gen/external_parents.xml (needs the game installed)")
    ap.add_argument("--ce-dir", type=Path, help="Combat Extended folder for --refresh-parents (found automatically)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.refresh_parents:
        game = ROOT.parents[1]
        if not (game / "Data" / "Core" / "Defs").is_dir():
            raise SystemExit(f"No RimWorld install at {game}; --refresh-parents needs the game's Data folder.")
        ce = args.ce_dir or find_ce(ROOT.parents[3])
        count, missing = refresh(ROOT, read_load_folders(ROOT), game, ce, EXTERNAL_PARENTS)
        print(f"Wrote {count} base defs to {EXTERNAL_PARENTS.relative_to(ROOT)} (Combat Extended: {ce or 'not found'})")
        if missing:
            print(f"{len(missing)} parent(s) come from other mods and stay unresolved: {', '.join(missing)}")
    b = Builder(args.out.resolve())
    b.build()
    problems = b.report()
    if args.strict and problems:
        raise SystemExit(f"--strict: {problems} problem(s)")
    if args.serve:
        serve(args.out.resolve(), args.serve)


if __name__ == "__main__":
    main()
