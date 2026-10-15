"""
Load the ETIM model CSV export (ETIM-xx-ALL-SECTORS-CSV-METRIC-EI, UTF-16LE,
';'-separated) into the etim10_model_* reference tables. Replaces their
whole content, so it can be re-run for a new ETIM release.

    python -m etim10_export.load_etim_model "C:/Projects/etim_10/schemas/ETIM-10.0-ALL-SECTORS-CSV-METRIC-EI-2024-12-05"
"""
import csv
import sys
from pathlib import Path

from gs1_export.database import get_connection


def _read(folder: Path, name: str) -> list[dict]:
    with open(folder / name, encoding="utf-16-le", newline="") as f:
        return [
            {k.strip().lstrip("\ufeff"): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(f, delimiter=";")
        ]


def load(folder: Path) -> dict[str, int]:
    classes = _read(folder, "ETIMARTCLASS.csv")
    features = _read(folder, "ETIMFEATURE.csv")
    values = _read(folder, "ETIMVALUE.csv")
    units = _read(folder, "ETIMUNIT.csv")
    class_features = _read(folder, "ETIMARTCLASSFEATUREMAP.csv")
    feature_values = _read(folder, "ETIMARTCLASSFEATUREVALUEMAP.csv")

    by_nr = {r["ARTCLASSFEATURENR"]: (r["ARTCLASSID"], r["FEATUREID"]) for r in class_features}
    allowed = {by_nr[r["ARTCLASSFEATURENR"]] + (r["VALUEID"],) for r in feature_values}

    tables = {
        "etim10_model_class": (
            ("class_id", "group_id", "description", "version"),
            [(r["ARTCLASSID"], r["ARTGROUPID"], r["ARTCLASSDESC"], r["ARTCLASSVERSION"]) for r in classes],
        ),
        "etim10_model_feature": (
            ("feature_id", "description"),
            [(r["FEATUREID"], r["FEATUREDESC"]) for r in features],
        ),
        "etim10_model_value": (
            ("value_id", "description"),
            [(r["VALUEID"], r["VALUEDESC"]) for r in values],
        ),
        "etim10_model_unit": (
            ("unit_id", "description"),
            [(r["UNITOFMEASID"], r["UNITDESC"]) for r in units],
        ),
        "etim10_model_class_feature": (
            ("class_id", "feature_id", "feature_type", "unit_id", "sort_nr"),
            [
                (r["ARTCLASSID"], r["FEATUREID"], r["FEATURETYPE"], r["UNITOFMEASID"] or None,
                 int(r["SORTNR"]) if r["SORTNR"] else None)
                for r in class_features
            ],
        ),
        "etim10_model_allowed_value": (
            ("class_id", "feature_id", "value_id"),
            sorted(allowed),
        ),
    }

    counts = {}
    with get_connection() as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            for table, (columns, rows) in tables.items():
                cur.execute(f"TRUNCATE public.{table}")
                with cur.copy(f"COPY public.{table} ({', '.join(columns)}) FROM STDIN") as copy:
                    for row in rows:
                        copy.write_row(row)
                counts[table] = len(rows)
        conn.commit()
    return counts


if __name__ == "__main__":
    for table, n in load(Path(sys.argv[1])).items():
        print(f"{table}: {n}")
