"""
Regression check: compute every article in produkte_etim10_produkt with the
rule engine and compare against what the Lobster profile wrote to
produkte_etim10_features (raw rule output, before the ETIM model check).

    python -m etim10_export.compare_with_lobster report.txt [limit]

Only articles Lobster computed on/after SINCE are compared - older rows in
produkte_etim10_features come from an earlier profile version.
"""
import sys
from collections import Counter, defaultdict

from gs1_export import pim_reader
from gs1_export.database import get_connection

from .feature_engine import EMPTY_VALUES, RuleSet, _format_number, compute_features, compute_raw

CHUNK = 300
SINCE = "2026-07-13"


def _norm(value) -> str:
    text = (value or "").strip()
    return "" if text in EMPTY_VALUES else _format_number(text)


def main(report_path: str, limit: int | None = None) -> None:
    conn = get_connection()
    rules = RuleSet.load(conn)
    matnrs = [r[0] for r in conn.execute(
        "SELECT matnr FROM public.produkte_etim10_produkt WHERE updated >= %s ORDER BY matnr", (SINCE,))]
    lobster_class = dict(conn.execute("SELECT matnr, classid FROM public.produkte_etim10_produkt"))
    if limit:
        matnrs = matnrs[:limit]

    stats = defaultdict(Counter)       # (class, feature) -> Counter(match/diff/only_lobster/only_app)
    examples = defaultdict(list)
    class_stats = Counter()
    class_examples = []
    file_stats = Counter()            # after the ETIM model check, i.e. what ends up in the file
    file_examples = defaultdict(list)
    file_counts = Counter()

    for start in range(0, len(matnrs), CHUNK):
        chunk = matnrs[start:start + CHUNK]
        products = dict(conn.execute(
            """
            SELECT DISTINCT ON (matnr) matnr, "json" FROM public.pim_egloakeneo_product
            WHERE matnr = ANY(%s) AND enabled = TRUE ORDER BY matnr, updated DESC
            """, (chunk,)))
        lobster = defaultdict(lambda: defaultdict(list))
        for matnr, fid, v, v2 in conn.execute(
                "SELECT matnr, featureid, value, value2 FROM public.produkte_etim10_features WHERE matnr = ANY(%s)",
                (chunk,)):
            lobster[matnr][fid].append((_norm(v), _norm(v2)))

        for matnr in chunk:
            product = products.get(matnr)
            if product is None:
                class_stats["no_pim_product"] += 1
                continue
            zztyp = pim_reader.get_zztypen_code(product) or ""
            class_id = rules.class_by_zztyp.get(zztyp, "")
            lob_class = lobster_class.get(matnr) or ""
            if class_id != lob_class:
                class_stats["class_diff"] += 1
                if len(class_examples) < 20:
                    class_examples.append(f"{matnr}: {zztyp} app={class_id or '-'} lobster={lob_class or '-'}")
                continue
            class_stats["class_match"] += 1
            if not class_id:
                continue
            app = {f: (_norm(s.get("value")), _norm(s.get("value2"))) for f, s in compute_raw(product, class_id, rules).items()}
            validated, _ = compute_features(product, class_id, rules, matnr)
            app_file = {f["feature_id"]: _norm(f["values"][0]) for f in validated}
            lob_file = {fid: {r[0] for r in rows if r[0]} for fid, rows in lobster[matnr].items()}
            for fid in set(app_file) | {f for f, v in lob_file.items() if v}:
                a, lv = app_file.get(fid, ""), lob_file.get(fid, set())
                kind = ("match" if a in lv else "only_lobster" if not a else "only_app" if not lv else "diff")
                file_stats[kind] += 1
                file_counts[(class_id, fid, kind)] += 1
                if kind != "match" and len(file_examples[(class_id, fid, kind)]) < 3:
                    file_examples[(class_id, fid, kind)].append(f"{matnr}: app={a} lobster={sorted(lv)}")
            for fid in set(app) | set(lobster[matnr]):
                a = app.get(fid, ("", ""))
                lob_rows = [r for r in lobster[matnr].get(fid, []) if r != ("", "")] or [("", "")]
                key = (class_id, fid)
                if a == ("", "") and lob_rows == [("", "")]:
                    continue
                if a in lob_rows:
                    stats[key]["match"] += 1
                    continue
                kind = "only_lobster" if a == ("", "") else "only_app" if lob_rows == [("", "")] else "diff"
                stats[key][kind] += 1
                if len(examples[key]) < 4:
                    examples[key].append(f"{matnr}: app={a} lobster={lob_rows}")
        print(f"{min(start + CHUNK, len(matnrs))}/{len(matnrs)}", flush=True)

    total = Counter()
    for c in stats.values():
        total.update(c)
    lines = [f"Artikel: {len(matnrs)}  Klassen: {dict(class_stats)}",
             f"Feature-Werte gesamt: {dict(total)}", ""]
    lines.append(f"Im BMEcat (nach ETIM-Modellpruefung): {dict(file_stats)}")
    lines.append("")
    lines += ["Klassen-Abweichungen:"] + [f"  {e}" for e in class_examples] + [""]
    bad = sorted(((k, c) for k, c in stats.items() if c["diff"] or c["only_lobster"] or c["only_app"]),
                 key=lambda kc: -(kc[1]["diff"] + kc[1]["only_lobster"] + kc[1]["only_app"]))
    lines.append("Abweichungen pro Klasse/Feature (match / diff / nur Lobster / nur App):")
    for (cls, fid), c in bad:
        lines.append(f"{cls} {fid} {rules.feature_names.get(fid, '')}: "
                     f"{c['match']} / {c['diff']} / {c['only_lobster']} / {c['only_app']}")
        lines += [f"    {e}" for e in examples[(cls, fid)]]
    lines += ["", "Datei-Ebene (nach ETIM-Pruefung) pro Klasse/Feature/Art:"]
    for (cls, fid, kind), ex in sorted(file_examples.items(), key=lambda kv: -file_counts[kv[0]]):
        lines.append(f"{cls} {fid} {rules.feature_names.get(fid, '')} [{kind}] {file_counts[(cls, fid, kind)]}x")
        lines += [f"    {e}" for e in ex]
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:3]))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
