#!/usr/bin/env python3
"""stats.py - regenerate stats.json for the Data Portal landing page from data.json.

Counts every row in data.json plus the BET-seq flanking-sequence datasets that live in
separate gzipped CSVs (they are not in data.json but count toward the headline number).
paper_meta.json (in the data-portal repo) supplies the one-line descriptions shown in
"Source Publications"; portal_merge.py adds an entry for each newly merged paper.
"""
import collections
import datetime
import json
import re
import sys
from pathlib import Path

from common import CFG, load_json, save_json

BETSEQ = {"study": "Le2018_PNAS", "technology": "BET-seq", "rows_per_type": {"Kd": 2097152, "DDG": 2097152},
          "proteins": ["Cbf1", "Pho4"], "note": "2 x 2,097,152 flanking sequences in Le2018_BETseq_part1/2 csv.gz"}

TYPE_LABELS = [  # display order + labels (mirrors the current page)
    ("Kd", "Dissociation constant (<i>K</i><sub>d</sub>)", "#2563eb"),
    ("DDG", "Relative binding energy (&Delta;&Delta;<i>G</i>)", "#7c3aed"),
    ("kcat/KM", "Catalytic efficiency (<i>k</i><sub>cat</sub>/<i>K</i><sub>M</sub>)", "#059669"),
    ("Ki", "Inhibition constant (<i>K</i><sub>i</sub>)", "#dc2626"),
    ("IC50", "Half-maximal inhibition (IC<sub>50</sub>)", "#f59e0b"),
    ("kcat", "Turnover number (<i>k</i><sub>cat</sub>)", "#0891b2"),
    ("KM", "Michaelis constant (<i>K</i><sub>M</sub>)", "#ca8a04"),
    ("fraction_active", "Fraction active", "#64748b"),
    ("koff/kon", "Kinetic rates (<i>k</i><sub>off</sub>, <i>k</i><sub>on</sub>)", "#ea580c"),
    ("EC50", "Half-maximal effect (EC<sub>50</sub>)", "#10b981"),
    ("fraction_native", "Fraction native", "#8b5cf6"),
    ("DG_native", "Folding stability (&Delta;<i>G</i><sub>native</sub>)", "#a16207"),
    ("k_unfold", "Unfolding rate (<i>k</i><sub>unfold</sub>)", "#be185d"),
]
EXTRA_COLORS = ["#0ea5e9", "#84cc16", "#f43f5e", "#6366f1", "#14b8a6", "#d946ef"]

TECH_DESC = {
    "HT-MEK": "High-Throughput Microfluidic Enzyme Kinetics &mdash; thousands of Michaelis-Menten curves in parallel on a single chip",
    "STAMMP / STAMMPPING": "Simultaneous Transcription factor Affinity Measurements via Microfluidic Protein arrays &mdash; high-throughput TF-DNA binding",
    "MRBLE-pep": "Microspheres with Ratiometric Barcode Lanthanide Encoding + peptides &mdash; multiplexed protein-peptide binding",
    "BET-seq": "Binding Energy Topography by sequencing &mdash; comprehensive TF-DNA binding energy landscapes for all flanking sequences",
    "SPARKfold": "Simultaneous Protein Analysis via Rate of unfolding and Kinetics &mdash; on-chip native proteolysis for 1000+ variants",
    "MITOMI": "Mechanically Induced Trapping Of Molecular Interactions &mdash; microfluidic TF-DNA binding affinities",
}
TYPE_HTML = {"Kd": "<i>K</i><sub>d</sub>", "DDG": "&Delta;&Delta;<i>G</i>", "kcat/KM": "<i>k</i><sub>cat</sub>/<i>K</i><sub>M</sub>",
             "Ki": "<i>K</i><sub>i</sub>", "kcat": "<i>k</i><sub>cat</sub>", "KM": "<i>K</i><sub>M</sub>", "koff": "<i>k</i><sub>off</sub>",
             "kon": "<i>k</i><sub>on</sub>", "IC50": "IC<sub>50</sub>", "EC50": "EC<sub>50</sub>", "k_unfold": "<i>k</i><sub>unfold</sub>",
             "fraction_active": "fraction active", "fraction_native": "fraction native", "DG_native": "&Delta;<i>G</i><sub>native</sub>"}


def tech_family(m):
    m0 = (m or "").split(" (")[0].strip()
    ml = m0.lower()
    if ml.startswith("stammp") or ml.startswith("k-stammp"):
        return "STAMMP / STAMMPPING"
    if "mek" in ml or ml.startswith("wellplate"):
        return "HT-MEK"
    if ml.startswith("mrble"):
        return "MRBLE-pep"
    if ml.startswith("bet-seq"):
        return "BET-seq"
    if ml.startswith("sparkfold"):
        return "SPARKfold"
    if ml.startswith("mitomi"):
        return "MITOMI"
    return m0 or "Other"


def paper_title_from_key(p):
    m = re.match(r"^([A-Za-z\-]+)(\d{4})_([A-Za-z]+)", p)
    if not m:
        return p, "", ""
    return m.group(1) + " et al.", m.group(2), m.group(3)


def compute(rows, paper_meta):
    by_type = collections.Counter()
    for r in rows:
        mt = r.get("mt", "")
        by_type["koff/kon" if mt in ("koff", "kon") else mt] += 1
    for k, v in BETSEQ["rows_per_type"].items():
        by_type[k] += v
    total = len(rows) + sum(BETSEQ["rows_per_type"].values())

    studies = sorted(set(r.get("p", "") for r in rows))
    proteins = sorted(set(r.get("pw", "") for r in rows) | set(BETSEQ["proteins"]))

    tech = {}
    for r in rows:
        fam = tech_family(r.get("m"))
        t = tech.setdefault(fam, {"name": fam, "count": 0, "proteins": set(), "papers": set(), "types": []})
        t["count"] += 1
        t["proteins"].add(r.get("pw", ""))
        t["papers"].add(r.get("p", ""))
        mt = r.get("mt", "")
        if mt and mt not in t["types"]:
            t["types"].append(mt)
    b = tech.setdefault("BET-seq", {"name": "BET-seq", "count": 0, "proteins": set(), "papers": set(), "types": []})
    b["count"] += sum(BETSEQ["rows_per_type"].values())
    b["proteins"] |= set(BETSEQ["proteins"])
    b["papers"].add(BETSEQ["study"])
    for k in BETSEQ["rows_per_type"]:
        if k not in b["types"]:
            b["types"].append(k)

    order = ["HT-MEK", "STAMMP / STAMMPPING", "MRBLE-pep", "BET-seq", "SPARKfold", "MITOMI"]
    techs = []
    for name in order + sorted(k for k in tech if k not in order):
        if name not in tech:
            continue
        t = tech[name]
        techs.append({"name": name, "desc": TECH_DESC.get(name, ""), "count": t["count"], "proteins": len(t["proteins"]),
                      "papers": len(t["papers"]), "types": ", ".join(TYPE_HTML.get(x, x) for x in t["types"])})

    cats = []
    used = set()
    for key, label, color in TYPE_LABELS:
        if by_type.get(key):
            cats.append({"plain": key, "label": label, "count": by_type[key], "color": color})
            used.add(key)
    extra = [k for k in by_type if k not in used and k]
    for i, k in enumerate(sorted(extra)):
        cats.append({"plain": k, "label": TYPE_HTML.get(k, k), "count": by_type[k], "color": EXTRA_COLORS[i % len(EXTRA_COLORS)]})

    per_paper = collections.Counter(r.get("p", "") for r in rows)
    papers = []
    for p in studies:
        meta = paper_meta.get(p, {})
        title, year, venue = paper_title_from_key(p)
        cnt = per_paper[p] + (sum(BETSEQ["rows_per_type"].values()) if p == BETSEQ["study"] else 0)
        papers.append({"p": p, "year": int(meta.get("year") or year or 0), "title": meta.get("title") or title,
                       "journal": meta.get("journal") or venue, "badge": meta.get("badge") or "{:,} measurements".format(cnt),
                       "desc": meta.get("desc") or "", "count": cnt})
    papers.sort(key=lambda x: (-x["year"], x["title"]))

    if total >= 1e6:
        label = "%.1fM+" % (total / 1e6)
    elif total >= 1e3:
        label = "{:,}".format(total)
    else:
        label = str(total)
    return {
        "updated": datetime.date.today().isoformat(),
        "total": total, "total_label": label, "rows_in_master_csv": len(rows),
        "studies": len(studies), "protein_systems": len(proteins), "technologies": len(techs),
        "by_type": cats, "by_technology": techs, "papers": papers,
        "extras": BETSEQ, "study_keys": studies, "protein_names": proteins,
    }


def main():
    portal = Path(sys.argv[1] if len(sys.argv) > 1 else CFG["data_portal_dir"])
    rows = json.load(open(portal / "data.json"))
    meta = load_json(portal / "paper_meta.json", {})
    stats = compute(rows, meta)
    save_json(portal / "stats.json", stats)
    print("stats.json: %s measurements (%s rows + BET-seq), %d studies, %d proteins, %d technologies" % (
        stats["total_label"], "{:,}".format(len(rows)), stats["studies"], stats["protein_systems"], stats["technologies"]))
    for c in stats["by_type"]:
        print("  %-16s %s" % (c["plain"], "{:,}".format(c["count"])))
    for t in stats["by_technology"]:
        print("  tech %-22s %s rows, %d proteins, %d papers" % (t["name"], "{:,}".format(t["count"]), t["proteins"], t["papers"]))


if __name__ == "__main__":
    main()
