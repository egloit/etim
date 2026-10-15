-- 2026-10-02: Regeln, die bei jedem Artikel einer Klasse dieselbe Warnung erzeugten
-- (Auswertung Lauf "ETIM 10 Formular - Standard Sortiment", 3.507 Artikel).
-- Angleichung an die gleichen Regeln der uebrigen Klassen.
BEGIN;

-- EF013759 "Bedienung ueber Bluetooth" (logisch) war bei Tisch-/Pendelleuchte fest EV001008
-- (Lobster-Festwert schlug den Switch) -> wie in allen anderen Klassen: Bluetooth -> true, sonst false.
UPDATE public.etim10_feature_rules
SET source_type = 'crosswalk', pim_field = 'PIM_transmission_technology',
    crosswalk_set = 'PIM_transmission_technology_EF013759', fix_value = NULL, updated_at = now(),
    note = note || ' | korrigiert: Festwert EV001008 -> Bluetooth true/false'
WHERE id IN (195, 296) AND feature_id = 'EF013759';

-- EF002972 "Mit Praesenzmelder" (EC002892): kein Treffer lieferte den ZZSCHAA-Rohcode -> false wie EC000300/EC001743.
UPDATE public.etim10_feature_rules SET crosswalk_set = 'ZZSCHAA_EF002972_2', updated_at = now(),
    note = note || ' | korrigiert: kein Treffer -> false'
WHERE id = 54 AND feature_id = 'EF002972';
UPDATE public.etim10_feature_rules SET active = FALSE, updated_at = now(),
    note = note || ' | deaktiviert: Rohcode statt true/false'
WHERE id = 55 AND feature_id = 'EF002972';

-- EF016286 "Austauschbares Betriebsgeraet" (EC000300): kein Treffer lieferte den Rohcode -> false wie alle anderen Klassen.
UPDATE public.etim10_feature_rules SET crosswalk_set = 'PIM_LED_DRIVER_TAUSCHBAR_EF016286', updated_at = now(),
    note = note || ' | korrigiert: kein Treffer -> false'
WHERE id = 99 AND feature_id = 'EF016286';
UPDATE public.etim10_feature_rules SET active = FALSE, updated_at = now(),
    note = note || ' | deaktiviert: Rohcode statt true/false'
WHERE id = 100 AND feature_id = 'EF016286';

COMMIT;
