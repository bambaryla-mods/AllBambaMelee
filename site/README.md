# Player site

The site at https://bambaryla-mods.github.io/AllBambaMelee/ shows players what the mod adds. It is not part of the mod. It uses the same generator and look as the [Beyond Our Reach site](https://bambaryla-mods.github.io/RimworldBeyondOurReach/), trimmed down to melee weapons, shields and research.

## How it works

`build.py` reads the mod the way RimWorld does. It loads the defs from the `<v1.7>` folders in `LoadFolders.xml`, applies the patches, resolves inheritance, honors `MayRequire`, and applies the English DefInjected text. It does this twice:

| Set | Active mods | Used for |
|---|---|---|
| vanilla | all DLCs and Beyond Our Reach | vanilla stats |
| ce | the same plus Combat Extended | Combat Extended stats and shields |

Beyond Our Reach counts as loaded in both, so its tiers show; the site marks them with a badge. Every stat, icon, recipe and research cost comes from the defs, so the tables stay correct when you rebalance. The written text lives in `content/` and has to be updated by hand when a mechanic changes.

Vanilla's and Combat Extended's own defs aren't in the repo, but the mod inherits from some of them (`BaseWeapon`, CE's `ShieldBase`). `gen/external_parents.xml` holds copies of those base defs. After a game or CE update, refresh it on a machine with the game installed and commit the result:

```
python site/build.py --refresh-parents
```

Beyond Our Reach's defs aren't loaded either. The labels of its benches, materials and research that recipes name are in `EXTERNAL_LABELS` in `gen/extract.py`; the build tells you if one is missing.

## Building and previewing

```
pip install -r site/requirements.txt
python site/build.py --serve
```

Then open http://localhost:8000. The build lists problems it found and screenshots that are still missing. It only ever deletes an output folder it created itself.

Pushing to `main` rebuilds and publishes the site through `.github/workflows/pages.yml`. One-time setup on GitHub: **Settings > Pages > Source: GitHub Actions**. The workflow builds with `--strict`, so a problem fails the run and the live site stays as it was.

## Where things live

| Path | What |
|---|---|
| `content/*.md` | One Markdown file per page. Front matter sets the home page card: `summary`, and `card` (texture paths for its icons). |
| `build.py` | The top bar and page order (`NAV`), the helpers pages can call, and the page rendering. |
| `gen/defs.py` | Loading, patching, inheritance and `MayRequire`. |
| `gen/external.py`, `gen/external_parents.xml` | The vanilla/CE base defs the mod inherits from (see above). |
| `gen/extract.py` | Turns defs into table rows. Tiers, stat labels and outside labels are at the top. |
| `templates/` | Page layout (`base.html`, `page.html`, `home.html`) and the tables (`macros.html`). |
| `static/` | CSS, the Vanilla/CE switch script and the Audiowide font (OFL). |
| `screenshots/` | Your in-game screenshots. |

## Helpers a page can call

```
{{ melee_tables() }}                        every melee weapon, by tier, vanilla and CE
{{ shield_tables() }}                       the CE shields
{{ research_tables() }}                     every research project, vanilla and CE
{{ tier_icons("solar", "kwazaari") }}       a row of weapon icons for the given tiers (all tiers if none given)
{{ screenshot("name", "caption") }}         screenshots/name.png
```

## Screenshots

Drop a PNG, JPG or WebP into `screenshots/` and call `screenshot("name", "caption")` on a page. Until the file exists, nothing shows and the build lists it as missing.
