"""
One-off migration: turn the decoded Lobster profile "PIM_DWH_ETIM10 Features
vorberechnen V2 Step 2" (regeln.json from lobster_profile_decoder.py) plus its
./conf/etim10/*.csv maps into INSERTs for etim10_class_mapping,
etim10_feature_rules and etim10_value_crosswalk.

    python -m etim10_export.import_lobster_rules C:/Projects/etim_10/regeln.json C:/Projects/etim_10/conf out.sql

Lobster semantics reproduced here (checked against produkte_etim10_features):
  - a field's fixed value always wins over its mapping/functions
  - goto/break chains (#105/#223) are code -> value switches; the trailing
    #223 is the default (also applied when the PIM field is empty)
  - #159 looks up the key in a map loaded by #230 in the init node; a map
    name that was never loaded makes the lookup return nothing -> the rule
    is imported inactive
  - #29 'a equals b' yields 'true'/'false'
Rules without any source (empty copy) and fixed '-' are skipped: both end
up as empty/'-' rows that the BMEcat file never contains.
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LOG_IDS = {"-593251360", "-822267925", "130", "-1640537962", "-270239329"}
CSV_HEADER_KEYS = {"Column1", "data", "Wert"}
# Copied 1:1 into unrelated features as placeholders in the profile.
MULTISELECT_PLACEHOLDERS = {"PIM_dimmable_with"}


def _pim_field(ref: str) -> str:
    """'ZZNETZS:data-2652' -> 'ZZNETZS' (first data path of a linked list)."""
    return ref.split(":", 1)[0]


class Importer:
    def __init__(self, rules_json: Path, conf_dir: Path):
        self.profile = json.loads(rules_json.read_text(encoding="utf-8"))
        self.conf_dir = conf_dir
        self.maps: dict[str, tuple[str, int]] = {}  # map name -> (csv file name, key column 1-based)
        self.class_rows: list[tuple] = []
        self.rule_rows: list[dict] = []
        self.crosswalk: dict[str, dict[str, str]] = {}
        self.stats = Counter()
        self._sort = Counter()

    # ---------- crosswalk sets ----------
    def _add_set(self, base: str, mapping: dict[str, str]) -> str:
        mapping = {k: v for k, v in mapping.items() if v != ""}
        n = 1
        while True:
            name = base if n == 1 else f"{base}_{n}"
            if name not in self.crosswalk:
                self.crosswalk[name] = mapping
                return name
            if self.crosswalk[name] == mapping:
                return name
            n += 1

    def _load_map(self, map_name: str, value_col: int) -> dict[str, str] | None:
        if map_name not in self.maps:
            return None
        file_name, key_col = self.maps[map_name]
        path = self.conf_dir / Path(file_name).name
        if not path.exists():
            return None
        out = {}
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
            for row in csv.reader(f, delimiter=";"):
                if len(row) < max(key_col, value_col):
                    continue
                key = row[key_col - 1].strip().lstrip("\ufeff")
                if not key or key in CSV_HEADER_KEYS:
                    continue
                out.setdefault(key, row[value_col - 1].strip())
        return out

    # ---------- function parsing ----------
    @staticmethod
    def _src(arg: dict, select_fields: dict[str, str]) -> str | None:
        t = arg.get("type")
        if t in ("linked", "input"):
            pim = arg["pim"] if isinstance(arg["pim"], list) else [arg["pim"]]
            return _pim_field(pim[0]) if pim else None
        if t == "destination":
            return select_fields.get(arg["value"].split("#")[0], arg["value"])
        return None

    @staticmethod
    def _switch(fns: list[dict]) -> tuple[list[dict], dict[str, str], str | None, dict | None]:
        """Resolve a goto/break chain. Falling through to the empty-value
        macro yields its 'g' argument (the macro returns g): a constant
        becomes the default, a field reference a raw-copy fallback."""
        pos = {i + 1: fn for i, fn in enumerate(fns)}
        cases, default, fallback, srcargs, i, guard = {}, None, None, [], 1, 0
        while i in pos and guard < 1000:
            guard += 1
            fn = pos[i]
            if fn["id"] == "105":
                a, b = fn["args"]["a"], fn["args"]["b"]
                const, src = (a, b) if a["type"] == "constant" else (b, a)
                srcargs.append(src)
                hit = pos.get(int(fn["args"]["c"]["value"] or 0))
                if hit and hit["id"] == "223":
                    cases.setdefault(const["value"], hit["args"]["a"]["value"])
                i = int(fn["args"]["d"]["value"] or 0)
            elif fn["id"] == "223":
                default = fn["args"]["a"]["value"]
                break
            elif fn["id"] == "-593251360":
                g = fn["args"].get("g") or {}
                if g.get("type") == "constant":
                    default = g.get("value") or None
                elif g.get("type") in ("linked", "input"):
                    fallback = g
                break
            else:
                i += 1
        return srcargs, cases, default, fallback

    def _parse_value(self, field: dict, feature: str, select_fields: dict[str, str]) -> list[dict]:
        """Rules for one value/value2 field, in evaluation order (a crosswalk
        can be followed by a raw-copy fallback)."""
        fns = field["functions"]
        core = [f for f in fns if f["id"] not in LOG_IDS]
        ids = [f["id"] for f in core]
        src_pim = [_pim_field(s) for s in field["source_pim"]]
        macro = next((f for f in fns if f["id"] == "-593251360"), None)
        warn = macro is not None

        def fix(value):
            if value in (None, "", "-"):
                self.stats["skip_fix_dash" if value == "-" else "skip_always_empty"] += 1
                return []
            return [dict(source_type="fix", fix_value=value, warn_if_empty=warn)]

        if field["fix_value"] is not None and field["fix_value"] != "":
            return fix(field["fix_value"])

        if set(ids) <= {"105", "223"} and ids:
            srcargs, cases, default, fallback = self._switch(fns)
            pim = next((self._src(a, select_fields) for a in srcargs if self._src(a, select_fields)), None)
            pim = pim or (src_pim[0] if src_pim else None)
            if default is not None:
                cases["*"] = default
            name = self._add_set(f"{pim}_{feature}", cases)
            rules = [dict(source_type="crosswalk", pim_field=pim, crosswalk_set=name, warn_if_empty=warn)]
            if fallback is not None:
                rules.append(dict(source_type="copy", pim_field=self._src(fallback, select_fields), warn_if_empty=warn,
                                  note="Lobster: kein Treffer -> Rohwert des PIM-Felds"))
            return rules

        # Not a switch: the field value is the macro's return value g when present
        # (unless a break (#223) ends execution before the macro is reached).
        if macro is not None and "223" not in ids:
            g = macro["args"].get("g") or {}
            if g.get("type") == "constant":
                return fix(g.get("value"))
            if g.get("type") in ("linked", "input"):
                g_field = self._src(g, select_fields)
                own = {self._src(f["args"]["a"], select_fields) for f in core if "a" in f["args"]} | set(src_pim)
                if g_field not in own:
                    return [dict(source_type="copy", pim_field=g_field, warn_if_empty=warn)]

        rule = self._parse_core(field, core, ids, src_pim, feature, select_fields)
        if rule is None:
            return []
        rule["warn_if_empty"] = warn
        if rule["source_type"] == "copy" and rule.get("pim_field") in MULTISELECT_PLACEHOLDERS:
            rule["active"] = False
            rule["note"] = (f"Platzhalter: Kopie des Mehrfachauswahl-Felds {rule['pim_field']} "
                            f"(Lobster liefert dafuer immer leer)")
            self.stats["inactive_multiselect_copy"] += 1
        return [rule]

    def _parse_core(self, field, core, ids, src_pim, feature, select_fields) -> dict | None:
        warn = False
        if not core:
            if not src_pim:
                self.stats["skip_no_source"] += 1
                return None
            return dict(source_type="copy", pim_field=src_pim[0], warn_if_empty=warn)

        if ids == ["79"]:
            a = core[0]["args"]
            return dict(source_type="copy", pim_field=self._src(a["a"], select_fields) or src_pim[0],
                        transform=f"substring_after:{a['b']['value']}", warn_if_empty=warn)

        if ids == ["2"]:
            pim = self._src(core[0]["args"]["a"], select_fields)
            return dict(source_type="copy", pim_field=pim, warn_if_empty=warn)

        if ids in (["29"], ["2", "29"]):
            a = core[-1]["args"]
            const = a["a"]["value"] if a["a"]["type"] == "constant" else a["b"]["value"]
            pim = self._src(core[0]["args"]["a"] if ids[0] == "2" else (a["b"] if a["a"]["type"] == "constant" else a["a"]),
                            select_fields)
            name = self._add_set(f"{pim}_{feature}", {const: "true", "*": "false"})
            return dict(source_type="crosswalk", pim_field=pim, crosswalk_set=name, warn_if_empty=warn)

        if ids[-1] == "159" or ids[:1] == ["159"]:
            lookup = next(f for f in core if f["id"] == "159")["args"]
            pim = self._src(lookup["a"], select_fields)
            if pim is None and ids[0] == "2":
                pim = self._src(core[0]["args"]["a"], select_fields)
            map_name, col, dflt = lookup["b"]["value"], int(lookup["d"]["value"]), lookup.get("e", {}).get("value", "")
            table = self._load_map(map_name, col)
            if table is None:
                self.stats["inactive_map_not_loaded"] += 1
                return dict(source_type="crosswalk", pim_field=pim,
                            crosswalk_set=self._add_set(f"{pim}_{feature}_UNLOADED", {}),
                            active=False, warn_if_empty=warn,
                            note=f"Lobster: Map '{map_name}' wird nie geladen - Lookup liefert nichts")
            if ids == ["159", "105", "223", "223"]:
                # lookup -> compare with constant -> two breaks (ZZGLASM 'Glas' case)
                cmp_ = core[1]["args"]
                hit, miss = core[2]["args"]["a"]["value"], core[3]["args"]["a"]["value"]
                table = {k: (hit if v == cmp_["b"]["value"] else miss) for k, v in table.items()}
                table["*"] = miss
            elif dflt:
                table["*"] = dflt
            name = self._add_set(f"{pim}_{feature}", table)
            return dict(source_type="crosswalk", pim_field=pim, crosswalk_set=name, warn_if_empty=warn)

        self.stats["unhandled"] += 1
        print(f"UNHANDLED {feature} {ids}", file=sys.stderr)
        return None

    @staticmethod
    def _unit(field: dict) -> str | None:
        if field["fix_value"]:
            return field["fix_value"]
        for fn in field["functions"]:
            if fn["id"] == "93":
                for k in ("c", "d"):
                    v = fn["args"][k].get("value", "")
                    if fn["args"][k]["type"] == "constant" and v:
                        return v
        return None

    # ---------- walk ----------
    def run(self):
        init, product = self.profile["output_tree"][0], self.profile["output_tree"][1]
        for f in init["fields"]:
            for fn in f["functions"]:
                if fn["id"] == "230":
                    a = fn["args"]
                    # key column 0 is invalid in Lobster: the map stays empty
                    if int(a["c"]["value"] or 1) > 0:
                        self.maps[a["b"]["value"]] = (a["a"]["value"], int(a["c"]["value"] or 1))

        seen_zztyp = {}
        for cls in product["children"]:
            m = re.match(r"if_classid_(EC\d{6})", cls["name"])
            if not m:
                continue
            class_id = m.group(1)
            codes = [arg["value"] for c in cls["conditions"] for arg in c["args"].values()
                     if arg.get("type") == "constant" and str(arg.get("value", "")).startswith("ZZTYPEN_")]
            for code in codes:
                if "TODO" in code:
                    continue
                if code in seen_zztyp:
                    print(f"ZZTYPEN {code} in {seen_zztyp[code]} und {class_id}", file=sys.stderr)
                    continue
                seen_zztyp[code] = class_id
                self.class_rows.append((code, class_id))

            select_fields = {}
            for f in cls["fields"]:
                for fn in f["functions"]:
                    if fn["id"] == "98":
                        am = re.search(r"attribute_code = '([^']+)'", fn["args"]["a"]["value"])
                        if am:
                            select_fields[f["name"].split("#")[0]] = am.group(1)

            for node in cls.get("children", []):
                fields = {f["name"].split("#")[0]: f for f in node["fields"]}
                feature = (fields.get("FeatureId") or {}).get("fix_value")
                if not feature:
                    continue
                required = None
                for c in node["conditions"]:
                    if c["id"] == "36":
                        a = c["args"]["a"]
                        required = self._src(a, select_fields) if a["type"] != "constant" else None
                unit = None
                if "UnitID" in fields:
                    unit = self._unit(fields["UnitID"])
                label = (fields.get("FeatureName") or {}).get("fix_value") or ""
                for slot in ("value", "value2"):
                    if slot not in fields:
                        continue
                    for rule in self._parse_value(fields[slot], feature, select_fields):
                        self._add_rule(rule, class_id, feature, slot, unit, required, node["name"], label)

    def _add_rule(self, rule, class_id, feature, slot, unit, required, node_name, label):
        key = (class_id, feature, slot)
        self._sort[key] += 1
        rule.update(class_id=class_id, feature_id=feature, slot=slot, unit_id=unit,
                    required_pim_field=required, sort_order=self._sort[key] * 10,
                    note=rule.get("note") or f"Lobster {node_name} ({label})")
        self.rule_rows.append(rule)
        self.stats[f"rule_{rule['source_type']}"] += 1

    # ---------- SQL ----------
    def sql(self) -> str:
        def q(v):
            if v is None:
                return "NULL"
            if isinstance(v, bool):
                return "TRUE" if v else "FALSE"
            return "'" + str(v).replace("'", "''") + "'"

        out = ["-- Generated by etim10_export/import_lobster_rules.py from the Lobster profile",
               "-- 'PIM_DWH_ETIM10 Features vorberechnen V2 Step 2' + ./conf/etim10/*.csv.",
               "-- Initial load only (tables must be empty).", "BEGIN;", ""]
        out.append("INSERT INTO public.etim10_class_mapping (zztyp, class_id) VALUES")
        out.append(",\n".join(f"    ({q(z)}, {q(c)})" for z, c in self.class_rows) + ";\n")
        rows = [(name, code, val) for name, m in sorted(self.crosswalk.items()) for code, val in m.items()]
        out.append("INSERT INTO public.etim10_value_crosswalk (crosswalk_set, pim_code, etim_value) VALUES")
        out.append(",\n".join(f"    ({q(n)}, {q(c)}, {q(v)})" for n, c, v in rows) + ";\n")
        cols = ["class_id", "feature_id", "slot", "source_type", "pim_field", "crosswalk_set", "fix_value",
                "transform", "unit_id", "required_pim_field", "warn_if_empty", "sort_order", "active", "note"]
        out.append(f"INSERT INTO public.etim10_feature_rules ({', '.join(cols)}) VALUES")
        out.append(",\n".join(
            "    (" + ", ".join(q(r.get(c, True if c == "active" else (False if c == "warn_if_empty" else None)))
                            for c in cols) + ")"
            for r in self.rule_rows) + ";\n")
        out.append("COMMIT;")
        self.stats["crosswalk_sets"] = len(self.crosswalk)
        self.stats["crosswalk_rows"] = len(rows)
        self.stats["class_mappings"] = len(self.class_rows)
        return "\n".join(out)


if __name__ == "__main__":
    imp = Importer(Path(sys.argv[1]), Path(sys.argv[2]))
    imp.run()
    Path(sys.argv[3]).write_text(imp.sql(), encoding="utf-8")
    for k, v in sorted(imp.stats.items()):
        print(f"{k}: {v}")
