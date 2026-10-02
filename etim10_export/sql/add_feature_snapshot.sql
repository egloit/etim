-- 2026-10-02: Snapshot der exportierten ETIM-Features je Artikel, um spaeter anzuzeigen,
-- welche Merkmale sich seit dem Export geaendert haben bzw. neu hinzugekommen sind.
ALTER TABLE public.etim10_export_history ADD COLUMN IF NOT EXISTS feature_snapshot JSONB;
