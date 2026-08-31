-- Activates Pick 4.226 (FSC Mark Indicator) for BrickIds 10005641 and
-- 10008404 - gs1_gpc_attribute_types already classifies it as propertyCode
-- for both, but gs1_mapping only had an active row for 10008403 so it was
-- never actually built for these two. pim_field = 'MATKL', same source
-- field as 10008403 - live-checked against real articles in both bricks
-- (matnr 44115 / ZZTYPEN_STL / 10005641, matnr 300536 / ZZTYPEN_HAL /
-- 10008404), both have a populated MATKL value.
--
-- No crosswalk data needed: pipeline.py resolves 4.226 directly from the
-- raw MATKL code (substring check for "FSC"), gs1_pim_value_crosswalk is
-- not consulted for this pick.
INSERT INTO public.gs1_mapping (brick_id, pick_id, pim_field, active)
VALUES
    ('10005641', '4.226', 'MATKL', TRUE),
    ('10008404', '4.226', 'MATKL', TRUE);
