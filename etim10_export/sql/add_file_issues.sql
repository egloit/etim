-- 2026-10-02: Problemliste (Warnungen + XSD-Fehler je Artikel) zu jeder erzeugten Datei,
-- als Excel-"Fehlerliste" neben dem Datei-Download abrufbar. Gilt fuer ETIM10 und GS1.
ALTER TABLE public.etim10_export_files ADD COLUMN IF NOT EXISTS issues JSONB;
ALTER TABLE public.gs1_export_files ADD COLUMN IF NOT EXISTS issues JSONB;
