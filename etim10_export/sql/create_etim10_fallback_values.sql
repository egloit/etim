-- ETIM10 fallback values per article and feature, used only when the PIM rule
-- of the feature yields nothing (see feature_engine.compute_raw).
--   source 'eprel'     - public EPREL API (light source / LED module),
--                        loaded by etim10_export/load_fallback_values.py
--   source 'webcenter' - packaging layouts (Webcenter output/produkte.csv)
--   source 'manual'    - maintained by hand, never overwritten by the loader
-- Values are already in ETIM form (EVxxxxxx / true / false / number); the
-- export still checks them against the ETIM model of the article's class.
-- Remove a source later, e.g.: DELETE FROM public.etim10_fallback_values WHERE source = 'webcenter';
-- Executed 2026-10-16 (user approved), filled by load_fallback_values.py (25,994 rows).
CREATE TABLE public.etim10_fallback_values (
    id          BIGSERIAL PRIMARY KEY,
    matnr       VARCHAR(60)  NOT NULL,          -- PIM has LED module matnrs up to 50 chars
    feature_id  VARCHAR(10)  NOT NULL,          -- e.g. EF000442
    value       VARCHAR(80)  NOT NULL,
    value2      VARCHAR(80),                    -- upper bound for range features (R)
    source      VARCHAR(20)  NOT NULL CHECK (source IN ('eprel', 'webcenter', 'manual')),
    note        TEXT,                           -- e.g. EPREL registration number
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (matnr, feature_id)
);
