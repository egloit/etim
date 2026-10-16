-- Pick 4.292 (Batteries included) for Brick 10000552 (Light Sources) pointed to
-- ZZAKKJY, a field no PIM product has; the real field is ZZAKKJN (J/N), as
-- already used for Brick 10008403. Executed 2026-10-15 (user approved).
UPDATE public.gs1_mapping
SET pim_field = 'ZZAKKJN', updated_at = now()
WHERE brick_id = '10000552' AND pick_id = '4.292' AND pim_field = 'ZZAKKJY';
