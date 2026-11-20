"""Turn resolved defs into the rows the site renders.

Every number shown on the site comes from here, read out of the same XML the
game loads. Two def sets are used:
- vanilla: the mod with the DLCs and Beyond Our Reach, Combat Extended not loaded
- ce: the same with Combat Extended loaded
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .defs import Def, DefDB, LoadFolder
from .textures import Textures

# Tech tiers in the order a colony climbs them when Beyond Our Reach is loaded.
TIERS = {
    "industrial": "Industrial",
    "spacer": "Spacer",
    "solar": "Solar",
    "ultra": "Ultra",
    "kwazaari": "Kwazaari",
    "tenebral": "Tenebral",
    "other": "General",
}
BOR_TIERS = frozenset({"solar", "kwazaari", "tenebral"})
BOR_NAME = "Beyond Our Reach"

MOD_NAMES = {
    "bambaryla.bor.redux": BOR_NAME,
    "bambaryla.bor": BOR_NAME,
    "ceteam.combatextended": "Combat Extended",
    "oskarpotocki.vanillafactionsexpanded.core": "Vanilla Expanded Framework",
}

# Defs from the game and Beyond Our Reach that the mod's recipes and research name.
# Their own defs aren't in this repo, so their in-game labels are copied here.
EXTERNAL_LABELS = {
    "Steel": "steel", "Plasteel": "plasteel", "Silver": "silver", "Gold": "gold",
    "ComponentIndustrial": "components", "ComponentSpacer": "advanced components",
    "Synthread": "synthread", "Hyperweave": "hyperweave",
    "FueledSmithy": "fueled smithy", "ElectricSmithy": "electric smithy", "FabricationBench": "fabrication bench",
    "Smithing": "smithing", "Machining": "machining", "MultiAnalyzer": "multi-analyzer",
    "Fabrication": "fabrication", "HiTechResearchBench": "hi-tech research bench", "AdvancedFabrication": "advanced fabrication",
    "BOR_Bench_I": "sunsteel shaper", "BOR_Bench_II": "Kwazifab Kwazaari Fabricator",
    "BOR_Bench_IV": "umbral fabricator",
    "BOR_Sunsteel": "sunsteel", "BOR_Sunweave": "sunweave",
    "BOR_QuasarAlloy": "kwazaari alloy", "BOR_QuasarAlloyWeave": "kwazaari weave",
    "BOR_ComponentII": "kwazaari universal part", "BOR_ComponentIII": "crystalline joints",
    "BOR_Tenebrite": "tenebrite crystals",
    "BOR_Research_Weapons_I": "solar weaponry", "BOR_Research_Weapons_II": "kwazaari weaponry",
}

# Name tokens first, then the file path.
_TIER_RULES = [
    ("tenebral", re.compile(r"tenebr")),
    ("kwazaari", re.compile(r"quasar|kwazaar")),
    ("solar", re.compile(r"solar")),
    ("ultra", re.compile(r"ultra")),
    ("spacer", re.compile(r"spacer")),
    ("industrial", re.compile(r"industrial")),
]

STAT_LABELS = {
    "ShootingAccuracyPawn": ("shooting accuracy", "abs"),
    "MeleeHitChance": ("melee hit chance", "abs"),
    "MeleeDodgeChance": ("melee dodge chance", "abs"),
    "MoveSpeed": ("move speed", "abs"),
    "Suppressability": ("suppressability", "pct"),
    "ReloadSpeed": ("reload speed", "pct"),
    "AimingAccuracy": ("aiming accuracy", "pct"),
    "MeleeCritChance": ("melee crit chance", "pct"),
    "MeleeParryChance": ("melee parry chance", "pct"),
    "MeleeDodgeChanceCE": ("melee dodge chance", "pct"),
}


def tier_of(d: Def) -> str:
    name = f"{d.def_name} {d.label}".lower()
    for tier, rx in _TIER_RULES:
        if rx.search(name):
            return tier
    path = d.file.lower()
    for tier, rx in _TIER_RULES:
        if rx.search(path):
            return tier
    return "other"


def cap(s: str | None) -> str:
    """Capitalize like the game's UI, and fix acronyms the defs write in lower case."""
    if not s:
        return ""
    s = re.sub(r"\bemp\b", "EMP", s)
    return s[:1].upper() + s[1:]


def humanize(name: str) -> str:
    name = re.sub(r"^BOR_", "", name)
    name = name.replace("_", " ")
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return name.strip().lower()


def _f(el, path: str, default: float | None = None) -> float | None:
    if el is None:
        return default
    v = el.findtext(path)
    if v is None or not v.strip():
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


@dataclass
class Group:
    tier: str
    items: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return TIERS.get(self.tier, cap(self.tier))

    @property
    def bor(self) -> bool:
        return self.tier in BOR_TIERS


def group_by_tier(items: list[dict]) -> list[Group]:
    order = list(TIERS)
    groups: dict[str, Group] = {}
    for it in items:
        groups.setdefault(it["tier"], Group(it["tier"])).items.append(it)
    return sorted(groups.values(), key=lambda g: order.index(g.tier) if g.tier in order else len(order))


class Extractor:
    def __init__(self, dbs: dict[str, DefDB], folders: list[LoadFolder], tex: Textures):
        self.v = dbs["vanilla"]
        self.ce = dbs["ce"]
        self.folders = {f.path: f for f in folders}
        self.tex = tex
        self._recipe_index: dict[int, dict[str, list[Def]]] = {}
        self.notes: list[str] = []

    # ---------- shared helpers ----------

    def _label(self, db: DefDB, tag: str, name: str) -> str:
        d = db.get(tag, name) or self.v.get(tag, name) or self.ce.get(tag, name)
        if d is not None:
            return cap(d.label)
        if name not in EXTERNAL_LABELS:
            self.notes.append(f"no label for {tag} {name}; add it to EXTERNAL_LABELS in gen/extract.py")
        return cap(EXTERNAL_LABELS.get(name) or humanize(name))

    def requires(self, d: Def, tier: str) -> list[str]:
        """Badges for the other mods an item needs. Combat Extended is left out: the CE tables already say so."""
        ids = [i.strip().lower() for attr in ("MayRequire", "MayRequireAnyOf") for i in (d.el.get(attr) or "").split(",")]
        out = _uniq(MOD_NAMES.get(i, i) for i in ids if i and i != "ceteam.combatextended")
        if tier in BOR_TIERS and BOR_NAME not in out:
            out.insert(0, BOR_NAME)
        return out

    def _recipes(self, db: DefDB) -> dict[str, list[Def]]:
        key = id(db)
        if key not in self._recipe_index:
            by_product: dict[str, list[Def]] = {}
            for r in db.all("RecipeDef"):
                products = r.node("products")
                for p in (products if products is not None else []):
                    if isinstance(p.tag, str):
                        by_product.setdefault(p.tag, []).append(r)
            self._recipe_index[key] = by_product
        return self._recipe_index[key]

    def _amounts(self, db: DefDB, pairs) -> list[str]:
        return [f"{n:g} {self._label(db, 'ThingDef', name).lower()}" for name, n in pairs]

    def making(self, d: Def, db: DefDB) -> dict:
        """Research, benches and ingredients, from the item's recipeMaker or a RecipeDef that makes it."""
        research: list[str] = []
        benches: list[str] = []
        cost: list[tuple[str, float]] = []
        if d.node("recipeMaker") is not None:
            research += [d.text("recipeMaker/researchPrerequisite")] + d.items("recipeMaker/researchPrerequisites")
            benches += d.items("recipeMaker/recipeUsers")
            node = d.node("costList")
            for c in (node if node is not None else []):
                if isinstance(c.tag, str) and (c.text or "").strip():
                    cost.append((c.tag, float(c.text)))
        for r in self._recipes(db).get(d.def_name, []):
            research += [r.text("researchPrerequisite")] + r.items("researchPrerequisites")
            benches += r.items("recipeUsers")
            node = r.node("ingredients")
            for li in (node if node is not None else []):
                things = [(x.text or "").strip() for x in li.findall("filter/thingDefs/li")]
                n = _f(li, "count")
                if things and n:
                    cost.append((things[0], n))
        return {
            "research": [self._label(db, "ResearchProjectDef", x) for x in _uniq(research)],
            "benches": [self._label(db, "ThingDef", x) for x in _uniq(benches)],
            "cost": self._amounts(db, cost),
        }

    def base_item(self, d: Def) -> dict:
        tier = tier_of(d)
        return {
            "def_name": d.def_name,
            "label": cap(d.label),
            "description": d.description,
            "icon": self.tex.icon(d),
            "tier": tier,
            "requires": self.requires(d, tier),
        }

    def stat_effects(self, d: Def, db: DefDB, path: str = "equippedStatOffsets") -> list[str]:
        node = d.node(path)
        out = []
        for st in (node if node is not None else []):
            if not isinstance(st.tag, str):
                continue
            try:
                val = float((st.text or "").strip())
            except ValueError:
                continue
            if val == 0:
                continue
            label, kind = STAT_LABELS.get(st.tag, (None, None))
            if label is None:
                sd = db.get("StatDef", st.tag)
                label = sd.label if sd else humanize(st.tag)
                kind = "pct" if sd is not None and (sd.text("toStringStyle") or "").startswith("Percent") else "abs"
            sign = "+" if val > 0 else "−"
            if kind == "pct":
                out.append(f"{sign}{abs(val) * 100:g}% {label}")
            else:
                out.append(f"{sign}{abs(val):g} {label}")
        return out

    # ---------- melee weapons ----------

    @staticmethod
    def is_melee(d: Def) -> bool:
        if d.tag != "ThingDef":
            return False
        if any(d.node(x) is not None for x in ("apparel", "building", "race", "projectile", "verbs")):
            return False
        if d.node("tools") is None:
            return False
        return "WeaponsMelee" in d.items("thingCategories") or any("Melee" in c for c in d.chain)

    def _tools(self, d: Def) -> list[dict]:
        node = d.node("tools")
        out = []
        for li in (node if node is not None else []):
            if li.tag != "li":
                continue
            caps = [(c.text or "").strip() for c in (li.find("capacities") if li.find("capacities") is not None else [])]
            power = _f(li, "power")
            ap = _f(li, "armorPenetration")
            if ap is None and power is not None:
                ap = power * 0.015  # the game's default for melee attacks
            out.append({
                "label": (li.findtext("label") or "").strip(),
                "kind": ", ".join(c.lower() for c in caps if c),
                "power": power,
                "cooldown": _f(li, "cooldownTime"),
                "ap": ap,
                "ap_sharp": _f(li, "armorPenetrationSharp"),
                "ap_blunt": _f(li, "armorPenetrationBlunt"),
                "extra": [f"+{_f(x, 'amount', 0):g} {(x.findtext('def') or '').strip()}"
                          for x in li.findall("extraMeleeDamages/li")],
            })
        return out

    def melee_weapons(self) -> list[Group]:
        names = _uniq([d.def_name for d in self.v.all("ThingDef") if self.is_melee(d)]
                      + [d.def_name for d in self.ce.all("ThingDef") if self.is_melee(d)])
        rows = []
        for name in names:
            vd = self.v.get("ThingDef", name)
            cd = self.ce.get("ThingDef", name)
            d = vd or cd
            row = self.base_item(d)
            row["vanilla"] = {"tools": self._tools(vd), "mass": vd.num("statBases/Mass"),
                              "make": self.making(vd, self.v)} if vd else None
            row["ce"] = {"tools": self._tools(cd), "mass": cd.num("statBases/Mass"),
                         "bulk": cd.num("statBases/Bulk"), "make": self.making(cd, self.ce)} if cd else None
            rows.append(row)
        return group_by_tier(rows)  # file order: knife, gladius, longsword, hammer...

    # ---------- shields (Combat Extended only) ----------

    def shields(self) -> list[Group]:
        rows = []
        for d in self.ce.all("ThingDef"):
            if d.node("apparel") is None:
                continue
            row = self.base_item(d)
            row["make"] = self.making(d, self.ce)
            ext = d.node("modExtensions")
            covers, crouch = [], []
            for li in (ext if ext is not None else []):
                if li.get("Class") == "CombatExtended.ShieldDefExtension":
                    covers = [humanize(x.text or "") for x in li.findall("shieldCoverage/li")]
                    crouch = [humanize(x.text or "") for x in li.findall("crouchCoverage/li")]
            row.update({
                "sharp": d.num("statBases/ArmorRating_Sharp"),
                "blunt": d.num("statBases/ArmorRating_Blunt"),
                "hp": d.num("statBases/MaxHitPoints"),
                "bulk": d.num("statBases/Bulk"),
                "worn_bulk": d.num("statBases/WornBulk"),
                "mass": d.num("statBases/Mass"),
                "covers": covers,
                "crouch": crouch,
                "effects": self.stat_effects(d, self.ce),
            })
            rows.append(row)
        return group_by_tier(rows)

    # ---------- research ----------

    def research(self, mode: str) -> list[Group]:
        db = self.ce if mode == "ce" else self.v
        rows = []
        for d in db.all("ResearchProjectDef"):
            prereq = [self._label(db, "ResearchProjectDef", x)
                      for x in d.items("prerequisites") + d.items("hiddenPrerequisites")]
            tier = tier_of(d)
            rows.append({
                "def_name": d.def_name,
                "label": cap(d.label),
                "description": d.description,
                "tier": tier,
                "cost": d.num("baseCost"),
                "prerequisites": prereq,
                "requires": self.requires(d, tier),
                "building": self._label(db, "ThingDef", d.text("requiredResearchBuilding"))
                if d.text("requiredResearchBuilding") else None,
                "facilities": [self._label(db, "ThingDef", x) for x in d.items("requiredResearchFacilities")],
            })
        return group_by_tier(rows)
