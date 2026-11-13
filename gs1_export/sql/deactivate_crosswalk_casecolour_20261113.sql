-- 2026-11-13: deactivate the old identity crosswalk rows for 4.012 (Material)
-- and 4.015 (Colour Family) from PIM_casecolour_main (pim_code -> same German
-- code, e.g. 'schwarz' -> 'schwarz'). 4.012/4.015 are mapped to ZZGHMAT/
-- ZZGHFAR since the Benelux fixes of 2026-10-15; the old rows produced GS1
-- error G541 ("code not in code list") in the test import of 31235
-- (response RESP-42bbb36c..., 2026-11-13). Expected: 796 rows (398 per Pick).
-- Executed 2026-11-13.

UPDATE public.gs1_pim_value_crosswalk
SET active = FALSE, updated_at = now()
WHERE pick_id IN ('4.012', '4.015')
  AND pim_field = 'PIM_casecolour_main'
  AND active = TRUE;
