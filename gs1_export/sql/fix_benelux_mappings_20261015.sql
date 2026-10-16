-- GS1 Benelux corrections after the test import of 32048 (GS1 response of
-- 2026-10-15, 10 errors). Executed 2026-10-15 (user approved).

BEGIN;

-- 1. Material (4.012) / colour family (4.015): PIM_casecolour_main only had a
--    1:1 placeholder crosswalk ('chrom' -> 'chrom', error G541). Use the same
--    source as Mounted (10008403): ZZGHMAT / ZZGHFAR with a real crosswalk.
UPDATE public.gs1_mapping SET pim_field = 'ZZGHMAT', updated_at = now()
WHERE active AND pick_id = '4.012' AND pim_field = 'PIM_casecolour_main';
UPDATE public.gs1_mapping SET pim_field = 'ZZGHFAR', updated_at = now()
WHERE active AND pick_id = '4.015' AND pim_field = 'PIM_casecolour_main';

-- 2. Conditional mandatory picks (data model sheet 'Validations'):
--    4.155 With light source included = TRUE -> 6.203, 8.492, 9.130, 9.135, 9.137
--    (rules 520.502, 520.800, 520.821, 520.803, 520.804); = FALSE -> 8.682 (520.802).
--    Same PIM sources as for Light Sources (10000552).
INSERT INTO public.gs1_mapping (brick_id, pick_id, pim_field, active)
SELECT b.brick_id, p.pick_id, p.pim_field, TRUE
FROM (VALUES ('10008404'), ('10008402'), ('10008403'), ('10008405'), ('10008406'), ('10005641')) AS b(brick_id)
CROSS JOIN (VALUES ('6.203', 'PIM_FAS_02_1'), ('9.130', 'PIM_FAS_01_1'), ('9.135', 'PIM_FAS_02_1'),
                   ('9.137', 'PIM_FAS_05_1'), ('8.492', 'PIM_dimmable_with'), ('8.682', 'PIM_FAS_05_1')) AS p(pick_id, pim_field)
WHERE NOT EXISTS (SELECT 1 FROM public.gs1_mapping m
                  WHERE m.brick_id = b.brick_id AND m.pick_id = p.pick_id AND m.active);

-- 9.135 'Type of Light Source Included' uses the same code list as 6.203.
INSERT INTO public.gs1_pim_value_crosswalk (pick_id, pim_field, pim_code, code_value, active)
SELECT '9.135', pim_field, pim_code, code_value, TRUE
FROM public.gs1_pim_value_crosswalk c
WHERE c.active AND c.pick_id = '6.203'
  AND NOT EXISTS (SELECT 1 FROM public.gs1_pim_value_crosswalk x
                  WHERE x.pick_id = '9.135' AND x.pim_code = c.pim_code AND x.active);

-- Wattage picks had no measurement unit -> the exporter would skip them.
UPDATE public.gs1_gpc_attribute_types SET measurement_unit_code = 'WTT', updated_at = now()
WHERE pick_id IN ('9.137', '8.682') AND measurement_unit_code IS NULL;

-- 3. IP rating (4.250): combined ratings from ZZLMSCH weren't valid codes.
--    2044 has an own Benelux code; for the others the lower (= overall)
--    protection is sent; X69K has no equivalent -> 'zzzanders' (other).
UPDATE public.gs1_pim_value_crosswalk AS c SET code_value = v.code_value, updated_at = now()
FROM (VALUES ('ZZLMSCH_2044', 'IP44_en_IP20'), ('ZZLMSCH_2065', 'IP20'), ('ZZLMSCH_2320', 'IP20'),
             ('ZZLMSCH_4455', 'IP44'), ('ZZLMSCH_5420', 'IP20'), ('ZZLMSCH_6420', 'IP20'),
             ('ZZLMSCH_6520', 'IP20'), ('ZZLMSCH_6544', 'IP44'), ('ZZLMSCH_6555', 'IP55'),
             ('ZZLMSCH_6567', 'IP65'), ('ZZLMSCH_X69K', 'zzzanders')) AS v(pim_code, code_value)
WHERE c.active AND c.pick_id = '4.250' AND c.pim_code = v.pim_code;

COMMIT;
