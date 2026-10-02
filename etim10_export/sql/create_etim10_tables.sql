-- ETIM10 BMEcat export: rule + reference tables. Replaces the Lobster profile
-- "PIM_DWH_ETIM10 Features vorberechnen V2 Step 2" and its ./conf/etim10/*.csv
-- maps; same pattern as gs1_brick_mapping / gs1_mapping / gs1_pim_value_crosswalk.
--
-- Rule evaluation (per article):
--   1. ZZTYPEN raw code -> etim10_class_mapping.class_id
--   2. every active etim10_feature_rules row of that class yields one slot
--      (value / value2) of one feature:
--        fix        -> fix_value
--        copy       -> raw PIM value of pim_field (numbers/text), optional transform
--        crosswalk  -> PIM code(s) of pim_field looked up in etim10_value_crosswalk
--                      (crosswalk_set); pim_code '*' = default when nothing matched.
--      Several rules for the same class/feature/slot: lowest sort_order with a
--      non-empty result wins.
--   3. result is checked against the etim10_model_* tables (ETIM 10.0); values
--      not allowed for class/feature are dropped with a warning.

CREATE TABLE public.etim10_class_mapping (
    id          BIGSERIAL PRIMARY KEY,
    zztyp       VARCHAR(40)  NOT NULL UNIQUE,   -- raw ZZTYPEN code, e.g. ZZTYPEN_STL
    class_id    VARCHAR(10)  NOT NULL,          -- ETIM class, e.g. EC000300
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    note        TEXT,
    created_at  TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE TABLE public.etim10_value_crosswalk (
    id             BIGSERIAL PRIMARY KEY,
    crosswalk_set  VARCHAR(80)  NOT NULL,       -- e.g. ZZNETZS_EF000187 (shared across classes)
    pim_code       VARCHAR(120) NOT NULL,       -- raw PIM option code, number text, or '*' (default)
    etim_value     VARCHAR(80)  NOT NULL,       -- EVxxxxxx / true / false / number; '' = explicitly empty
    active         BOOLEAN      NOT NULL DEFAULT TRUE,
    note           TEXT,
    created_at     TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at     TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (crosswalk_set, pim_code)
);

CREATE TABLE public.etim10_feature_rules (
    id                  BIGSERIAL PRIMARY KEY,
    class_id            VARCHAR(10)  NOT NULL,
    feature_id          VARCHAR(10)  NOT NULL,
    slot                VARCHAR(6)   NOT NULL DEFAULT 'value' CHECK (slot IN ('value', 'value2')),
    source_type         VARCHAR(10)  NOT NULL CHECK (source_type IN ('fix', 'copy', 'crosswalk')),
    pim_field           VARCHAR(80),            -- copy/crosswalk source attribute, e.g. ZZNETZS
    crosswalk_set       VARCHAR(80),            -- crosswalk: etim10_value_crosswalk.crosswalk_set
    fix_value           VARCHAR(80),            -- fix: literal value
    transform           VARCHAR(80),            -- copy: optional, e.g. 'substring_after:W'
    unit_id             VARCHAR(10),            -- EUxxxxxx, only written when the value is non-empty
    required_pim_field  VARCHAR(80),            -- rule only applies if this PIM field is filled
    warn_if_empty       BOOLEAN      NOT NULL DEFAULT FALSE,
    sort_order          INTEGER      NOT NULL DEFAULT 0,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    note                TEXT,                   -- origin in the Lobster profile / known issues
    created_at          TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at          TIMESTAMP    NOT NULL DEFAULT now(),
    CHECK (source_type <> 'fix'       OR fix_value IS NOT NULL),
    CHECK (source_type <> 'copy'      OR pim_field IS NOT NULL),
    CHECK (source_type <> 'crosswalk' OR (pim_field IS NOT NULL AND crosswalk_set IS NOT NULL))
);
CREATE INDEX etim10_feature_rules_class_idx ON public.etim10_feature_rules (class_id) WHERE active;

-- ETIM 10.0 model (loaded from ETIM-10.0-ALL-SECTORS-CSV-METRIC-EI), read-only reference.
CREATE TABLE public.etim10_model_class (
    class_id     VARCHAR(10) PRIMARY KEY,
    group_id     VARCHAR(10),
    description  TEXT,
    version      VARCHAR(10)
);

CREATE TABLE public.etim10_model_feature (
    feature_id   VARCHAR(10) PRIMARY KEY,
    description  TEXT
);

CREATE TABLE public.etim10_model_value (
    value_id     VARCHAR(10) PRIMARY KEY,
    description  TEXT
);

CREATE TABLE public.etim10_model_unit (
    unit_id      VARCHAR(10) PRIMARY KEY,
    description  TEXT
);

CREATE TABLE public.etim10_model_class_feature (
    class_id      VARCHAR(10) NOT NULL,
    feature_id    VARCHAR(10) NOT NULL,
    feature_type  CHAR(1)     NOT NULL,         -- A alphanumeric, L logical, N numeric, R range
    unit_id       VARCHAR(10),
    sort_nr       INTEGER,
    PRIMARY KEY (class_id, feature_id)
);

CREATE TABLE public.etim10_model_allowed_value (
    class_id    VARCHAR(10) NOT NULL,
    feature_id  VARCHAR(10) NOT NULL,
    value_id    VARCHAR(10) NOT NULL,
    PRIMARY KEY (class_id, feature_id, value_id)
);

-- Export history / file archive, same shape as gs1_export_history / gs1_export_files.
CREATE TABLE public.etim10_export_history (
    matnr                   VARCHAR(20) PRIMARY KEY,
    class_id                VARCHAR(10),
    exported_at             TIMESTAMPTZ NOT NULL,
    pim_updated_at_export   VARCHAR,
    exported_by             TEXT
);

CREATE TABLE public.etim10_export_files (
    id             BIGSERIAL PRIMARY KEY,
    filename       TEXT        NOT NULL,
    exported_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    exported_by    TEXT,
    matnrs         TEXT[],
    article_count  INTEGER,
    xml_content    BYTEA       NOT NULL
);
CREATE INDEX etim10_export_files_user_idx ON public.etim10_export_files (exported_by, exported_at DESC);
