-- 2026-10-02 (vom User freigegeben): EF017556 "Schutzart (IP), Rueckseite" fuer Downlights (EC001744)
-- nicht mehr aus ZZLMSCH ableiten. ZZLMSCH ist die Gesamt-/Frontschutzart (bereits in EF003118);
-- fuer die Rueckseite gibt es keine eigene PIM-Quelle, und ETIM erlaubt dort nur bis IP34.
UPDATE public.etim10_feature_rules
SET active = FALSE, updated_at = now(),
    note = note || ' | deaktiviert: ZZLMSCH ist die Frontschutzart, keine Quelle fuer die Rueckseite'
WHERE id = 351 AND class_id = 'EC001744' AND feature_id = 'EF017556';

-- Ebenso fuer Decken-/Wandleuchten (EC002892), vom User freigegeben am 2026-10-02.
UPDATE public.etim10_feature_rules
SET active = FALSE, updated_at = now(),
    note = note || ' | deaktiviert: ZZLMSCH ist die Frontschutzart, keine Quelle fuer die Rueckseite'
WHERE id = 21 AND class_id = 'EC002892' AND feature_id = 'EF017556';
