-- gs1_export_history: one row per matnr, latest GS1 export wins (see
-- database.upsert_export_history()/get_changed_articles()). Used to flag
-- articles whose PIM data changed since their last GS1 export.
--
-- Column types matched to what's actually queried against:
--   - matnr is the join/conflict key -> PRIMARY KEY (also gives the unique
--     index ON CONFLICT (matnr) needs).
--   - pim_updated_at_export is compared with IS DISTINCT FROM against
--     pim_egloakeneo_product.updated, which is character varying -> same
--     type here, no implicit cast surprises.
--   - exported_by matches gs1_export_files.exported_by (text).
CREATE TABLE public.gs1_export_history (
    matnr                   VARCHAR(20) PRIMARY KEY,
    brick_id                VARCHAR(20),
    exported_at             TIMESTAMPTZ NOT NULL,
    pim_updated_at_export   VARCHAR,
    exported_by             TEXT
);
