#!/usr/bin/env python3
r"""Build the FA / VO Helper's shipped tables (data\fa\) on the maintainer's machine. Users never run this: OpenLauncher
only needs E-Sys, their psdzdata and these tables.

    python ocm\esys.py run esys\fa_dump.js <dumpDir>      (decrypted FA->FP code lists + KIS, from psdzdata)
    python ocm\fa_tables.py --ista <DiagDocDb.sqlite> --dump <dumpDir>

Writes (deterministic gzip, so an unchanged rebuild gives identical files):
  data\fa\typekeys.tsv.gz  type key -> main series, E-series, model, body, market, drive, engine, KIS group, option list
  data\fa\options.tsv.gz   option lists: list id, code, flag, from, to (year+month)
                            flag S = standard, O = optional, T = comes with the type key (ISTA);
                            C = allowed for the type key in its class (KIS), V = available in that window (KIS)
  data\fa\codes.tsv.gz     code -> kind (SA / E / HO), title, classes (KIS group:class:strict|soft)
  data\fa\SOURCES.txt      the ISTA and psdzdata versions the tables were built from
A class is "strict" when no type key fits two of its options as standard at the same time (ISTA), else "soft".
"""
import argparse
import collections
import glob
import gzip
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

OUT = os.path.join(config.DATA, "fa")
NC_TYPEKEY, NC_SERIES, NC_MODEL, NC_BODY, NC_MARKET, NC_DRIVE, NC_ENGINE, NC_GROUP = (
    40135042, 40128130, 40122114, 40133634, 40129538, 40124162, 40132226, 99999999951)


def ym(s):
    """'2022-07-01 00:00:00' -> '202207' ('' for none)."""
    s = (s or "").strip()
    return s[:4] + s[5:7] if len(s) >= 7 else ""


def write_gz(name, header, rows):
    os.makedirs(OUT, exist_ok=True)
    with gzip.GzipFile(os.path.join(OUT, name), "wb", compresslevel=9, mtime=0) as f:
        f.write(("\n".join(["\t".join(header)] + ["\t".join(r) for r in rows]) + "\n").encode("utf-8"))


def read_dump(dump):
    """FA->FP code lists and KIS groups from esys\\fa_dump.js."""
    fafp = collections.defaultdict(set)            # (series, selector) -> codes
    for line in open(os.path.join(dump, "fafp_codes.tsv"), encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        if len(f) >= 3 and f[0] != "series":
            fafp[(f[0], f[1])].add(f[2])
    kis = {}
    for p in sorted(glob.glob(os.path.join(dump, "kis_*.tsv"))):
        g = os.path.basename(p)[4:-4]
        k = kis[g] = {"version": "", "types": {}, "class": [], "window": []}
        for line in open(p, encoding="utf-8"):
            f = line.rstrip("\n").split("\t")
            if f[0] == "version":
                k["version"] = f[1]
            elif f[0] == "type" and len(f) >= 2:
                k["types"][f[1]] = dict(kv.split("=", 1) for kv in (f[2] if len(f) > 2 else "").split(";") if "=" in kv)
            elif f[0] == "class" and len(f) >= 4:
                k["class"].append((f[1], f[2], f[3]))
            elif f[0] == "window" and len(f) >= 5:
                k["window"].append((f[1], f[2], ym(f[3]), ym(f[4])))
    psdz = open(os.path.join(dump, "psdzdata.txt"), encoding="utf-8").read().strip()
    return fafp, kis, psdz


def read_ista(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    version = (db.execute("SELECT VERSION FROM RG_VERSION WHERE SUBSYSTEM = 'DATABASE'").fetchone() or ["?"])[0]
    chars = {i: (nc, n) for i, nc, n in db.execute("SELECT ID, NODECLASS, NAME FROM XEP_CHARACTERISTICS")}
    salapa = {}                                     # row id -> code; titles: car title first (PRODUCT_TYPE P)
    titles = {}
    for i, code, en, de, pt in db.execute("SELECT ID, NAME, TITLE_ENGB, TITLE_DEDE, PRODUCT_TYPE FROM XEP_SALAPAS "
                                          "ORDER BY PRODUCT_TYPE <> 'P', ID"):
        salapa[i] = code
        if code and code not in titles and (en or de):
            titles[code] = (en or de).strip()
    veh = collections.defaultdict(set)
    for tk, ch in db.execute("SELECT TYPEKEYID, CHARACTERISTICID FROM XEP_VEHICLES"):
        veh[tk].add(ch)
    types = {}                                      # type key code -> {attrs, options}
    for tk, so, verb, sop, eop in db.execute("SELECT VEHICLETYPE_ID, SALAPA_OID, VERB, VBA_SOP, VBA_EOP "
                                             "FROM STD_TYPEKEY_SA_LINK"):
        nc, name = chars.get(tk, (0, ""))
        if nc != NC_TYPEKEY or so not in salapa:
            continue                                # orphan rows (no type key in this database)
        t = types.get(name)
        if t is None:
            def attr(c, ids=veh.get(tk, ())):
                vals = sorted(chars[i][1] for i in ids if chars.get(i, (0,))[0] == c)
                return vals[0] if vals else ""
            t = types[name] = {"series": attr(NC_SERIES), "model": attr(NC_MODEL), "body": attr(NC_BODY),
                               "market": attr(NC_MARKET), "drive": attr(NC_DRIVE), "engine": attr(NC_ENGINE),
                               "group": attr(NC_GROUP), "options": set()}
        t["options"].add((salapa[so], verb or "O", ym(sop), ym(eop)))
    db.close()
    return version, titles, types


def grade_classes(kis, ista_types):
    """{(group, class): {codes}} and strictness from ISTA's standard-fit sets."""
    classes = collections.defaultdict(set)
    for g, k in kis.items():
        for _tk, code, cls in k["class"]:
            classes[(g, cls)].add(code)
    classes = {key: codes for key, codes in classes.items() if len(codes) > 1}
    by_code = collections.defaultdict(list)
    for key, codes in classes.items():
        for c in codes:
            by_code[c].append(key)
    clash = collections.Counter()
    for t in ista_types.values():
        std = [o for o in t["options"] if o[1] in ("S", "T")]
        for i in range(len(std)):
            for j in range(i + 1, len(std)):
                a, b = std[i], std[j]
                if a[0] == b[0] or not ((a[2] or "0") <= (b[3] or "9") and (b[2] or "0") <= (a[3] or "9")):
                    continue
                for key in set(by_code.get(a[0], ())) & set(by_code.get(b[0], ())):
                    if not t["group"] or key[0] == t["group"]:
                        clash[key] += 1
    return classes, clash


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ista", required=True, help="decrypted ISTA DiagDocDb .sqlite (maintainer only)")
    ap.add_argument("--dump", required=True, help="folder written by esys\\fa_dump.js")
    a = ap.parse_args()
    fafp, kis, psdz = read_dump(a.dump)
    ista_version, titles, ista_types = read_ista(a.ista)

    # main series per type key, from the FA->FP mappings (T = type key)
    main_series = collections.defaultdict(set)
    for (series, sel), codes in fafp.items():
        if sel == "T":
            for c in codes:
                main_series[c].add(series)

    # option lists: ISTA's where it has one; otherwise KIS class lists + availability windows
    kis_opts = collections.defaultdict(set)
    kis_attr = {}
    for g, k in kis.items():
        for tk, attrs in k["types"].items():
            kis_attr.setdefault(tk, (g, attrs))
        for tk, code, _cls in k["class"]:
            kis_opts[tk].add((code, "C", "", ""))
        for tk, code, frm, to in k["window"]:
            kis_opts[tk].add((code, "V", frm, to))
    all_tks = sorted(set(ista_types) | set(kis_attr) | set(main_series))
    lists, list_rows, tk_rows = {}, [], []
    for tk in all_tks:
        it = ista_types.get(tk)
        opts = it["options"] if it else kis_opts.get(tk, set())
        lid = ""
        if opts:
            key = tuple(sorted(opts))
            if key not in lists:
                lists[key] = str(len(lists) + 1)
                list_rows += [[lists[key], c, f, frm, to] for c, f, frm, to in key]
            lid = lists[key]
        g, kattrs = kis_attr.get(tk, ("", {}))
        src = ("I" if it else "") + ("K" if tk in kis_attr else "") + ("F" if tk in main_series else "")
        tk_rows.append([tk, ",".join(sorted(main_series.get(tk, ()))),
                        (it or {}).get("series") or kattrs.get("EBR", ""), (it or {}).get("model", ""),
                        (it or {}).get("body") or kattrs.get("KFORM", ""), (it or {}).get("market", ""),
                        (it or {}).get("drive", ""), (it or {}).get("engine", ""),
                        (it or {}).get("group") or g, lid, src])

    classes, clash = grade_classes(kis, ista_types)
    kinds = collections.defaultdict(set)
    for (_series, sel), codes in fafp.items():
        for c in codes:
            if sel in "WHK":
                kinds[c].add({"W": "SA", "H": "E", "K": "HO"}[sel])
    code_classes = collections.defaultdict(list)
    for (g, cls), codes in classes.items():
        for c in codes:
            code_classes[c].append(f"{g}:{cls}:{'soft' if clash[(g, cls)] else 'strict'}")
    used = {r[1] for r in list_rows} | set(kinds) | set(code_classes)
    code_rows = [[c, "/".join(sorted(kinds.get(c, ()))), titles.get(c, ""), ",".join(sorted(code_classes.get(c, ())))]
                 for c in sorted(used)]

    write_gz("typekeys.tsv.gz", ["typekey", "main_series", "series", "model", "body", "market", "drive", "engine",
                                 "kis_group", "options", "sources"], tk_rows)
    write_gz("options.tsv.gz", ["list", "code", "flag", "from", "to"], list_rows)
    write_gz("codes.tsv.gz", ["code", "kind", "title", "classes"], code_rows)
    with open(os.path.join(OUT, "SOURCES.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("FA / VO Helper tables (built by ocm/fa_tables.py)\n"
                f"ISTA database {ista_version}\npsdzdata {psdz}\n"
                + "".join(f"KIS {g} {k['version']}\n" for g, k in sorted(kis.items())))
    soft = sorted(f"{g}:{c}" for (g, c) in classes if clash[(g, c)])
    print(f"type keys {len(tk_rows)} (ISTA option lists {len(ista_types)}, KIS {len(kis_attr)}, "
          f"in FA->FP mappings {len(main_series)}); option lists {len(lists)} ({len(list_rows)} rows); "
          f"codes {len(code_rows)}; classes {len(classes)} ({len(soft)} soft: {' '.join(soft[:12])})")
    for n in ("typekeys.tsv.gz", "options.tsv.gz", "codes.tsv.gz"):
        print(f"  {n}: {os.path.getsize(os.path.join(OUT, n)) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
