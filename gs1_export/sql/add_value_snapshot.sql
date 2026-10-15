-- 2026-10-02: Snapshot der exportierten GS1-Werte je Artikel (+ Export-Parameter), um spaeter
-- anzuzeigen, welche Werte sich seit dem Export geaendert haben bzw. hinzugekommen sind.
ALTER TABLE public.gs1_export_history ADD COLUMN IF NOT EXISTS value_snapshot JSONB;
