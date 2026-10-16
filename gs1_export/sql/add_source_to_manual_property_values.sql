-- Herkunft der Werte in gs1_manual_property_values.
-- 'manual'    = bisher manuell gepflegt (z. B. Bebat-Code 4.651)
-- 'webcenter' = vorübergehend aus den Verpackungslayouts (WebCenter-Auslesung,
--               PycharmProjects/Webcenter/output/produkte.csv) - nur als Ersatz,
--               solange das PIM-Feld leer ist; später gezielt löschbar:
--               DELETE FROM public.gs1_manual_property_values WHERE source = 'webcenter';
-- Ausgeführt 2026-10-15 (vom User freigegeben).
ALTER TABLE public.gs1_manual_property_values
    ADD COLUMN IF NOT EXISTS source varchar(30) NOT NULL DEFAULT 'manual';
