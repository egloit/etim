-- Vom User freigegeben und ausgefuehrt am 2026-10-02: Korrekturen an den 1:1 aus Lobster
-- uebernommenen Regeln. Jede Korrektur wurde gegen das ETIM-10-Modell
-- (etim10_model_*) geprueft; die Ids stammen aus dem Import vom 2026-10-02.

BEGIN;

-- 1) "Farbtemperatur einstellbar": Lobster-Knoten EF016261 schreibt FeatureId
--    EF011948 (Photobiologische Sicherheit) -> doppelt/falsch belegt.
--    Fuer EC000300/EC000301/EC000302/EC001743 zeigte die Lookup-Spalte zudem
--    auf den Text ('Stufen'/'stufenlos') statt auf den EV-Code (Spalte 7).
--    Mit der EV-Spalte sind alle 14 Werte fuer EF016261 zulaessig.
UPDATE public.etim10_feature_rules SET feature_id = 'EF016261', updated_at = now(),
       note = note || ' | korrigiert: EF011948 -> EF016261'
WHERE id IN (16, 405, 431);
UPDATE public.etim10_feature_rules SET feature_id = 'EF016261', crosswalk_set = 'PIM_dimming_level_EF011948',
       updated_at = now(), note = note || ' | korrigiert: EF011948 -> EF016261, EV-Spalte statt Text'
WHERE id IN (137, 188, 230, 286);

-- 2) Laenge aus ZZLMLAE: Knoten EF001438 (Laenge) schreibt FeatureId EF008801
--    (Signal-Frequenz), EF008801 gehoert nicht einmal zur Klasse.
UPDATE public.etim10_feature_rules SET feature_id = 'EF001438', updated_at = now(),
       note = note || ' | korrigiert: EF008801 -> EF001438'
WHERE id IN (8, 292);

-- 3) EC001744 Downlight: drei Knoten schreiben alle EF000015 (Aussendurchmesser).
--    ZZAUSSC -> EF023168 Einbaulaenge, ZZLMHOE -> EF010795 Einbauhoehe (beide Typ R).
UPDATE public.etim10_feature_rules SET feature_id = 'EF023168', sort_order = 10, updated_at = now(),
       note = note || ' | korrigiert: EF000015 -> EF023168'
WHERE id = 366;
UPDATE public.etim10_feature_rules SET feature_id = 'EF010795', sort_order = 10, updated_at = now(),
       note = note || ' | korrigiert: EF000015 -> EF010795'
WHERE id = 367;

-- 4) Material: Lookup auf die Text-Spalte ('Aluminium', 'Kunststoff' ...) statt
--    EV-Code. Mit der EV-Spalte (ZZGHMAT_EF001596) sind 79-87 von 89 Codes zulaessig.
UPDATE public.etim10_feature_rules SET crosswalk_set = 'ZZGHMAT_EF001596', updated_at = now(),
       note = note || ' | korrigiert: EV-Spalte statt Text'
WHERE id IN (145, 199, 283, 325);

COMMIT;

-- Offen (Entscheidung noetig, nicht enthalten):
--  * ZZGLASF -> EF004269 (Farbe der Abdeckung) statt EF002423: Mapping-Datei
--    mapping_ZZGLASF_EF004269.csv fehlt (Regeln inaktiv).
--  * EC001959 Leuchtmittel: Knoten EF001360 (Lampenform) schreibt EF001348,
--    Knoten EF012196 (Lampenbezeichnung) schreibt EF002423; beide Maps waren in
--    Lobster nie geladen (Regeln inaktiv).
--  * EF015688/EF015690/EF015686 vertauscht (EC002892, EC001743) - in Lobster
--    ohnehin nur '-' (nicht importiert).
