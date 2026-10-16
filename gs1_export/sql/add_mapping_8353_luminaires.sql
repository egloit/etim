-- Pick 8.353 visibleLight (nominal useful luminous flux) for the luminaire
-- bricks - Benelux rule 520.660: mandatory if 4.641 Integrated LED = TRUE.
-- PIM_LM_41 like for Light Sources; usually empty for luminaires, the export
-- then takes the EPREL value from gs1_manual_property_values (source 'eprel',
-- see gs1_export/load_eprel_values.py). Executed 2026-10-16 (user approved).
INSERT INTO public.gs1_mapping (brick_id, pick_id, pim_field, active)
SELECT b.brick_id, '8.353', 'PIM_LM_41', TRUE
FROM (VALUES ('10008404'), ('10008402'), ('10008403'), ('10008405'), ('10008406'), ('10005641'), ('10008292')) AS b(brick_id)
WHERE NOT EXISTS (SELECT 1 FROM public.gs1_mapping m
                  WHERE m.brick_id = b.brick_id AND m.pick_id = '8.353' AND m.active);
