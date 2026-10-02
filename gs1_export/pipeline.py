"""
Orchestrates the GS1 export flow (spec section 9):

  Produkt aus PostgreSQL laden
        -> BrickId bestimmen
        -> GS1 Mapping fuer BrickId laden
        -> PIM-Felder anhand des Mappings lesen
        -> sprachabhaengige Werte ermitteln
        -> GS1 XML erzeugen
        -> XML gegen GS1 XSD validieren

A missing product, missing BrickId mapping, or missing PIM field never
aborts the whole batch – it's logged and surfaced as a warning, the
remaining articles are still processed.
"""
import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from . import database, gs1_exporter, gs1_mapping, pim_reader, sap_database, snapshot, validator

load_dotenv()

logger = logging.getLogger(__name__)

_LANGUAGE_CONFIG_PATH = Path(__file__).with_name("language_mapping.json")
_VKORG_COUNTRY_CONFIG_PATH = Path(__file__).with_name("vkorg_country_mapping.json")
_VKORG_VAT_CONFIG_PATH = Path(__file__).with_name("vkorg_vat_mapping.json")
_SAP_LANGUAGE_CONFIG_PATH = Path(__file__).with_name("sap_language_mapping.json")
_COUNTRY_CODE_CONFIG_PATH = Path(__file__).with_name("country_code_mapping.json")

# SAP MARA.GEWEI/MEABM -> GS1 UN/CEFACT measurementUnitCode. Fixed technical
# unit codes (not business data), so no external mapping file needed. Weight
# is always normalised to grams and dimensions to millimetres, matching the
# units used in a real GS1-portal reference export for matnr 43706.
_SAP_WEIGHT_UNIT_TO_GRAMS_FACTOR = {"KG": 1000, "G": 1}
_SAP_DIMENSION_UNIT_TO_MM_FACTOR = {"MM": 1, "CM": 10, "M": 1000}

# Picks whose propertyMeasurement value is the sum of several numbers found in
# one free-text PIM field, rather than a single clean number (e.g. 7.041
# "Nominal Power Consumption" on ceiling fans: PIM_MAX_POWER_INCL_TRANSFORMER
# combines fan + light wattage as "22W = FAN; 18W = LIGHT").
_SUM_ALL_NUMBERS_PICKS = {"7.041"}

# 4.226 (FSC Mark Indicator, MATKL-sourced): user's rule is "TRUE if the raw
# code contains the substring FSC, else FALSE" - checked directly against the
# raw code instead of via gs1_pim_value_crosswalk, since MATKL gets new
# FSC-certified material codes over time and an exact-match crosswalk table
# silently mis-resolves any not yet added to it as FALSE (found live on
# 2026-08-31: 27 real FSC_* codes not in the crosswalk).
_FSC_SUBSTRING_PICKS = {"4.226"}

# packagingInformationModule (GS1 error 500.061) - fixed value, EGLO's
# standard packaging is a box ("BX"), same reasoning as
# variableTradeItemInformationModule (no per-article source). Translations
# match the real GS1-portal reference export (matnr 43706) verbatim; only
# covers the GS1 languages this app actually emits (see language_mapping.json
# - en/nl/fr), "BOX" as a fallback for anything else.
_PACKAGING_TYPE_CODE = "BX"
_PACKAGING_TYPE_DESCRIPTIONS_BY_GS1_CODE = {"en": "BOX", "fr": "BOITE", "nl": "DOOS"}

GS1_SENDER_GLN = os.getenv("GS1_SENDER_GLN", "")
GS1_SENDER_PARTY_NAME = os.getenv("GS1_SENDER_PARTY_NAME", "EGLO")
GS1_RECEIVER_GLN = os.getenv("GS1_RECEIVER_GLN", "")
GS1_ADDITIONAL_CLASSIFICATION_SYSTEM_CODE = os.getenv("GS1_ADDITIONAL_CLASSIFICATION_SYSTEM_CODE", "64")
# Fixed EGLO-wide support contact for tradeItemContactInformation, not article-specific.
GS1_SUPPORT_CONTACT_NAME = os.getenv("GS1_SUPPORT_CONTACT_NAME", "EGLO SAP Support")
GS1_SUPPORT_CONTACT_EMAIL = os.getenv("GS1_SUPPORT_CONTACT_EMAIL", "sap.support@eglo.com")


class PipelineError(Exception):
    """Raised for hard failures that abort the whole export (e.g. DB unreachable)."""


def _load_json_config(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _gtin_from_ean(ean: str) -> str:
    """GDSN expects a 14-digit GTIN; our PIM EAN11 values are 13-digit EANs."""
    ean = (ean or "").strip()
    return ean.zfill(14) if ean else ""


def _weight_in_grams(value, unit: str) -> Optional[int]:
    """Normalise a SAP MARA weight (BRGEW/NTGEW, GEWEI unit) to whole grams,
    matching the unit used in a real GS1-portal reference export. None if the
    value is 0/missing or the unit isn't one of the ones seen in practice
    (KG/G) - never guess a conversion factor."""
    if value is None:
        return None
    factor = _SAP_WEIGHT_UNIT_TO_GRAMS_FACTOR.get((unit or "").strip().upper())
    if factor is None:
        return None
    grams = round(float(value) * factor)
    return grams if grams > 0 else None


def _dimension_in_mm(value, unit: str) -> Optional[int]:
    """Normalise a SAP MARA/MARM dimension (LAENG/BREIT/HOEHE, MEABM unit) to
    whole millimetres. None if 0/missing or an unrecognised unit - SAP
    dimensions are frequently just unset (0, no unit) in practice."""
    if value is None:
        return None
    factor = _SAP_DIMENSION_UNIT_TO_MM_FACTOR.get((unit or "").strip().upper())
    if factor is None:
        return None
    mm = round(float(value) * factor)
    return mm if mm > 0 else None


def _package_dimension_in_mm(value) -> Optional[int]:
    """ZZEVLAE/ZZEVBRE/ZZEVTIE (individual-packaging length/width/depth) have
    no companion unit field like MEABM - assumed to already be stored in
    millimetres. Live-verified 2026-09-23 (see sap_database.
    get_material_measurements' docstring): the mm assumption checks out
    against MARA.VOLUM for 3 real articles."""
    if value is None:
        return None
    mm = round(float(value))
    return mm if mm > 0 else None


# File extension -> GS1 fileFormatName, matching the casing seen in the real
# GS1-portal reference export ("Jpeg" for a .JPEG file). Falls back to the
# extension itself (capitalised) for anything not seen in practice yet.
_FILE_FORMAT_NAMES = {"jpg": "Jpeg", "jpeg": "Jpeg", "png": "Png", "pdf": "Pdf"}


def _file_format_name(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _FILE_FORMAT_NAMES.get(ext, ext.capitalize())


def _build_sap_extension_modules(
    hana_conn,
    matnr: str,
    werks: str,
    languages: list[tuple[str, dict]],
    sap_language_config: dict,
    country_code_config: dict,
) -> list:
    """Measurements/description/origin extension modules sourced from SAP
    HANA rather than the PIM - see sap_database.py. hana_conn is None if the
    HANA connection itself failed (see export_batch); every lookup here is
    independently best-effort, matching the "log and skip" philosophy used
    for PIM-sourced properties elsewhere in this module."""
    modules = []
    if hana_conn is None:
        return modules

    measurements = sap_database.get_material_measurements(hana_conn, matnr)
    if measurements:
        # ZZEVLAE(length)/ZZEVBRE(width)/ZZEVTIE(depth) preferred over
        # LAENG/BREIT/HOEHE - mirrors the LAENG->depth/HOEHE->height mapping
        # structurally, but note ZZEVTIE is SAP's own "Tiefe" (depth) field
        # mapped here to GS1 "height", same positional convention as before.
        # The mm-unit assumption is live-verified (see sap_database.
        # get_material_measurements' docstring); the depth/height axis
        # assignment itself is still a best guess - user confirmed 2026-09-23
        # to ship with it rather than block on further verification.
        depth = (
            _package_dimension_in_mm(measurements.get("package_length"))
            or _dimension_in_mm(measurements["length"], measurements["dimension_unit"])
        )
        width = (
            _package_dimension_in_mm(measurements.get("package_width"))
            or _dimension_in_mm(measurements["width"], measurements["dimension_unit"])
        )
        height = (
            _package_dimension_in_mm(measurements.get("package_depth"))
            or _dimension_in_mm(measurements["height"], measurements["dimension_unit"])
        )
        gross_weight = _weight_in_grams(measurements["gross_weight"], measurements["weight_unit"])
        net_weight = _weight_in_grams(measurements["net_weight"], measurements["weight_unit"])
        if any(v is not None for v in (depth, width, height, gross_weight, net_weight)):
            modules.append(
                gs1_exporter.build_trade_item_measurements_module_element(
                    depth=depth, width=width, height=height,
                    gross_weight=gross_weight, net_weight=net_weight,
                    # Single-unit lighting products, no PIM/SAP source for this yet -
                    # see pipeline.py's docstring / session notes for the reasoning.
                    net_content=1,
                )
            )
        else:
            logger.info("GS1 EXPORT | matnr=%s: keine SAP-Masse/Gewichte vorhanden", matnr)

    sap_descriptions = sap_database.get_material_descriptions(hana_conn, matnr)
    descriptions_by_language: dict[str, str] = {}
    for spras, gs1_code in sap_language_config.items():
        text = sap_descriptions.get(spras)
        if text:
            descriptions_by_language[gs1_code] = text
    if descriptions_by_language:
        modules.append(
            gs1_exporter.build_trade_item_description_module_element(
                descriptions_by_language=descriptions_by_language,
                # MAKTX reused as a placeholder for functionalName too - not a real
                # "product function" text (see e.g. "WALL LIGHT" in the GS1
                # reference export), kept until a better source is identified.
                functional_names_by_language=descriptions_by_language,
                brand_name="EGLO",
            )
        )
    else:
        logger.info("GS1 EXPORT | matnr=%s: keine SAP-Beschreibungstexte vorhanden", matnr)

    if werks:
        origin = sap_database.get_material_origin(hana_conn, matnr, werks)
        country_code = country_code_config.get((origin or {}).get("country_of_origin", "")) if origin else None
        if country_code:
            customs_tariff = (origin.get("customs_tariff_number") or "").strip()
            modules.append(
                gs1_exporter.build_place_of_item_activity_module_element(
                    country_of_origin_code=country_code,
                    # First 8 digits = the base CN/HS commodity code; SAP's STAWN can
                    # carry extra national digits beyond that - verified against the
                    # real GS1 reference export for matnr 43706 (STAWN "9405199090" ->
                    # importClassificationValue "94051990").
                    import_classification_value=customs_tariff[:8] if customs_tariff else None,
                )
            )
        else:
            logger.info(
                "GS1 EXPORT | matnr=%s werks=%s: kein Herkunftsland in MARC oder "
                "kein Eintrag in country_code_mapping.json", matnr, werks,
            )

    return modules


def _resolve_crosswalk_value(conn, pickid: str, pim_codes: list[str]) -> Optional[str]:
    """Check every candidate raw PIM code for an exact gs1_pim_value_crosswalk
    match (multiselect fields can have several selected codes - a match
    anywhere wins over the wildcard, regardless of position), falling back to
    the wildcard once none of them matched. Used for both propertyCode and
    select-sourced propertyMeasurement Picks (e.g. 4.769/ZZNETZS, where the
    raw select code - not a plain number - has to be translated to a fixed
    voltage via the crosswalk)."""
    for pim_code in pim_codes:
        value = gs1_mapping.get_pim_value_crosswalk_exact(conn, pickid, pim_code)
        if value:
            return value
    return gs1_mapping.get_pim_value_crosswalk_wildcard(conn, pickid)


def export_batch(
    matnrs: list[str], lang_codes: list[str], vkorg: str = "", exported_by: Optional[str] = None,
    werks: str = "", record_history: bool = True, snapshots: Optional[dict] = None,
) -> tuple[bytes, list[str], list[dict]]:
    """Build a combined GS1 XML document for *matnrs*.

    Returns (xml_bytes, warnings, validation_errors).
    Raises PipelineError on hard failures (e.g. DB connection).

    Every article that makes it into the document also gets an
    upsert_export_history() entry (matnr + PIM's "updated" timestamp at this
    moment) - see get_changed_articles() for how that's used later to flag
    articles whose PIM data has changed since their last export.

    werks is used to pick the right plant-specific country-of-origin row
    from SAP MARC (see _build_sap_extension_modules()) - the same form field
    already used for ETIM.
    """
    language_config = _load_json_config(_LANGUAGE_CONFIG_PATH)
    languages = [(code, language_config[code]) for code in lang_codes if code in language_config]

    vkorg_country_config = _load_json_config(_VKORG_COUNTRY_CONFIG_PATH)
    target_market_country_code = vkorg_country_config.get(vkorg.strip(), "")

    vkorg_vat_config = _load_json_config(_VKORG_VAT_CONFIG_PATH)
    vat_info = vkorg_vat_config.get(vkorg.strip())

    sap_language_config = _load_json_config(_SAP_LANGUAGE_CONFIG_PATH)
    country_code_config = _load_json_config(_COUNTRY_CODE_CONFIG_PATH)

    unique_matnrs = list(dict.fromkeys(m.strip() for m in matnrs if m.strip()))
    warnings: list[str] = []
    notifications = []

    try:
        hana_conn = sap_database.get_connection()
    except Exception as exc:
        hana_conn = None
        logger.error("GS1 EXPORT | SAP-HANA-Verbindung fehlgeschlagen: %s", exc)
        warnings.append(
            "SAP-HANA-Verbindung fehlgeschlagen – Masse/Gewicht, Beschreibungstexte und "
            "Herkunftsland werden im XML ausgelassen."
        )

    if vkorg.strip() and not target_market_country_code:
        warnings.append(
            f"VKORG {vkorg.strip()}: kein targetMarketCountryCode in vkorg_country_mapping.json hinterlegt "
            "– Zielmarkt wird im XML ausgelassen."
        )
    if vkorg.strip() and not vat_info:
        warnings.append(
            f"VKORG {vkorg.strip()}: keine dutyFeeTaxInformationModule-Werte in vkorg_vat_mapping.json "
            "hinterlegt – Steuerinformationen werden im XML ausgelassen."
        )

    try:
        conn = database.get_connection()
    except Exception as exc:
        raise PipelineError(f"Datenbankverbindung fehlgeschlagen: {exc}") from exc

    try:
        for matnr in unique_matnrs:
            product = database.get_product_by_matnr(conn, matnr)
            if product is None:
                warnings.append(f"Artikel {matnr}: nicht in PIM gefunden.")
                logger.warning("GS1 EXPORT | matnr=%s: nicht gefunden", matnr)
                continue

            pim_updated_at = database.get_product_updated_at(conn, matnr)

            zztypen_code = pim_reader.get_zztypen_code(product)
            if not zztypen_code:
                warnings.append(f"Artikel {matnr}: kein ZZTYPEN-Wert vorhanden, BrickId nicht bestimmbar.")
                logger.warning("GS1 EXPORT | matnr=%s: ZZTYPEN fehlt", matnr)
                continue

            brick_id = gs1_mapping.get_brick_id(conn, zztypen_code)
            if not brick_id:
                warnings.append(f"Artikel {matnr}: keine BrickId-Zuordnung für ZZTYPEN={zztypen_code}.")
                logger.warning("GS1 EXPORT | matnr=%s zztypen=%s: keine BrickId gefunden", matnr, zztypen_code)
                continue

            mappings = gs1_mapping.get_active_mappings(conn, brick_id)
            if not mappings:
                warnings.append(f"Artikel {matnr}: keine aktiven Mappings für BrickId={brick_id}.")
                logger.warning("GS1 EXPORT | matnr=%s brickid=%s: keine aktiven Mappings", matnr, brick_id)
                continue

            properties = []
            for mapping_row in mappings:
                pickid = mapping_row["pickid"]
                pimfeld = mapping_row["pimfeld"]

                # gs1_gpc_attribute_types tells us which GDSN element a Pick actually
                # needs: "propertyDescription" (free, language-dependent text),
                # "propertyCode" (via gs1_pim_value_crosswalk), "propertyMeasurement"
                # (via measurement_unit_code) and "propertyInteger" (plain number,
                # no unit) are all built.
                gdsn_type = gs1_mapping.get_gdsn_attribute_type(conn, brick_id, pickid)

                if gdsn_type == "propertyMeasurement":
                    unit_code = gs1_mapping.get_measurement_unit_code(conn, brick_id, pickid)
                    if not unit_code:
                        logger.info(
                            "GS1 EXPORT | matnr=%s pickid=%s: kein measurementUnitCode in "
                            "gs1_gpc_attribute_types, Property wird ausgelassen",
                            matnr, pickid,
                        )
                        continue

                    # Some sources are select fields whose raw code needs
                    # translating to a fixed number via the crosswalk (e.g.
                    # 4.769/ZZNETZS: "ZZNETZS_02" -> "230"), others are plain
                    # numeric/text PIM fields (e.g. 4.052/PIM_FAS_05_1: "10").
                    # Try the crosswalk first, fall back to direct parsing.
                    amount = None
                    pim_codes = pim_reader.get_pim_raw_codes(product, pimfeld)
                    if pim_codes:
                        crosswalk_value = _resolve_crosswalk_value(conn, pickid, pim_codes)
                        if crosswalk_value:
                            try:
                                amount = int(float(crosswalk_value.replace(",", ".")))
                            except ValueError:
                                amount = None
                    if amount is None:
                        if pickid in _SUM_ALL_NUMBERS_PICKS:
                            amount = pim_reader.get_pim_number_sum_as_int(product, pimfeld)
                        else:
                            amount = pim_reader.get_pim_number_as_int(product, pimfeld)

                    if amount is None:
                        logger.info(
                            "GS1 EXPORT | matnr=%s pickid=%s pimfeld=%s: kein Zahlwert (weder Crosswalk "
                            "noch direkte Zahl), Property wird ausgelassen",
                            matnr, pickid, pimfeld,
                        )
                        continue
                    properties.append(gs1_exporter.build_measurement_property_element(pickid, amount, unit_code))
                    continue

                if gdsn_type == "propertyCode":
                    # Multiselect fields can have several selected codes (e.g.
                    # B2C_Filter_Function); a crosswalk rule can be "true if ANY
                    # of these codes is selected", so every code needs an exact-
                    # match check before falling back to the wildcard once.
                    # An entirely unset PIM field (pim_codes == []) still goes
                    # through _resolve_crosswalk_value() - it falls straight
                    # through to the wildcard, same as "no exact match" - so a
                    # Pick with a wildcard default (e.g. 4.290: "* -> FALSE")
                    # gets that default even when the source field was never
                    # filled in, instead of being silently skipped.
                    pim_codes = pim_reader.get_pim_raw_codes(product, pimfeld)
                    if pickid in _FSC_SUBSTRING_PICKS:
                        code_value = "TRUE" if any("FSC" in c.upper() for c in pim_codes) else "FALSE"
                    else:
                        code_value = _resolve_crosswalk_value(conn, pickid, pim_codes)
                    if not code_value:
                        logger.info(
                            "GS1 EXPORT | matnr=%s pickid=%s pimfeld=%s pim_codes=%s: kein "
                            "Crosswalk-Eintrag (auch keine Wildcard) in gs1_pim_value_crosswalk, "
                            "Property wird ausgelassen",
                            matnr, pickid, pimfeld, pim_codes,
                        )
                        continue
                    properties.append(gs1_exporter.build_code_property_element(pickid, code_value))
                    continue

                if gdsn_type == "propertyString":
                    # GS1-documented "NOT IN PIM" attributes (Recupel/Bebat/
                    # Stibat/Wecycle recycling codes) - no PIM source field to
                    # translate, looked up directly by matnr instead.
                    value = gs1_mapping.get_manual_property_value(conn, matnr, pickid)
                    if not value:
                        logger.info(
                            "GS1 EXPORT | matnr=%s pickid=%s: kein Eintrag in "
                            "gs1_manual_property_values, Property wird ausgelassen",
                            matnr, pickid,
                        )
                        continue
                    properties.append(gs1_exporter.build_string_property_element(pickid, value))
                    continue

                if gdsn_type == "propertyInteger":
                    amount = pim_reader.get_pim_number_as_int(product, pimfeld)
                    if amount is None:
                        logger.info(
                            "GS1 EXPORT | matnr=%s pickid=%s pimfeld=%s: kein Zahlwert, "
                            "Property wird ausgelassen",
                            matnr, pickid, pimfeld,
                        )
                        continue
                    properties.append(gs1_exporter.build_integer_property_element(pickid, amount))
                    continue

                if gdsn_type is not None and gdsn_type != "propertyDescription":
                    logger.info(
                        "GS1 EXPORT | matnr=%s pickid=%s: GDSN-Typ '%s' noch nicht unterstuetzt "
                        "(nur propertyDescription/propertyCode/propertyMeasurement/propertyInteger), "
                        "Property wird ausgelassen",
                        matnr, pickid, gdsn_type,
                    )
                    continue
                if gdsn_type is None:
                    logger.info(
                        "GS1 EXPORT | matnr=%s pickid=%s: kein Eintrag in gs1_gpc_attribute_types, "
                        "behandle vorlaeufig als propertyDescription",
                        matnr, pickid,
                    )

                value = pim_reader.get_pim_value(product, pimfeld)

                texts_by_language: dict[str, str] = {}
                for lang_code, lang_cfg in languages:
                    text = pim_reader.resolve_text(value, lang_cfg["pim_locale"])
                    if text:
                        texts_by_language[lang_cfg["gs1_code"]] = text

                if not texts_by_language:
                    logger.warning(
                        "GS1 EXPORT | matnr=%s pickid=%s pimfeld=%s: kein Wert gefunden, Property wird ausgelassen",
                        matnr, pickid, pimfeld,
                    )
                    continue

                properties.append(gs1_exporter.build_description_property_element(pickid, texts_by_language))

            gtin_value = pim_reader.get_pim_value(product, "EAN11")
            gtin = _gtin_from_ean((gtin_value.get("translations") or {}).get(None, "") if gtin_value else "")

            extension_modules = []
            if vat_info:
                extension_modules.append(
                    gs1_exporter.build_duty_fee_tax_information_module_element(
                        agency_code=vat_info["dutyFeeTaxAgencyCode"],
                        tax_type_code=vat_info["dutyFeeTaxTypeCode"],
                        category_code=vat_info["dutyFeeTaxCategoryCode"],
                    )
                )

            declared_power = pim_reader.get_pim_number_as_int(product, "PIM_FAS_05_1")
            if declared_power is not None:
                extension_modules.append(
                    gs1_exporter.build_lighting_device_module_element(declared_power, "WTT")
                )
            else:
                logger.info(
                    "GS1 EXPORT | matnr=%s: kein PIM_FAS_05_1-Wert, lightingDeviceModule wird ausgelassen",
                    matnr,
                )

            extension_modules.extend(
                _build_sap_extension_modules(
                    hana_conn, matnr, werks.strip(), languages, sap_language_config, country_code_config,
                )
            )

            # referencedFileDetailInformationModule - primary product image
            # only for now (asset_code 101/VIEW), see
            # database.get_primary_product_image's docstring. Other asset
            # types (DETAIL/DIMENSION/AMBIENT images, 360 video) deliberately
            # parked, user to clarify their referencedFileTypeCode later.
            image = gs1_mapping.get_primary_product_image(conn, matnr)
            if image:
                extension_modules.append(
                    gs1_exporter.build_referenced_file_detail_information_module_element(
                        file_type_code="PRODUCT_IMAGE",
                        file_format_name=_file_format_name(image["filename"]),
                        file_name=image["filename"],
                        uri=image["url"],
                        is_primary_file=True,
                        media_source_gln=GS1_SENDER_GLN,
                    )
                )
            else:
                logger.info(
                    "GS1 EXPORT | matnr=%s: kein VIEW-Bild gefunden, "
                    "referencedFileDetailInformationModule wird ausgelassen",
                    matnr,
                )

            # marketingInformationModule/tradeItemMarketingMessage - source
            # PIM_ARTIKELTEXT (pim_catalog_textarea), confirmed by the user
            # 2026-09-23 as the real flowing marketing-copy field, replacing
            # the earlier MAKTX (SAP material short text) placeholder - see
            # gs1_exporter.build_marketing_information_module_element's
            # docstring. Uses the same PIM-locale convention as
            # propertyDescription (language_mapping.json), not SAP's spras.
            marketing_texts_by_language: dict[str, str] = {}
            for lang_code, lang_cfg in languages:
                text = gs1_mapping.get_pim_catalog_textarea_value(conn, matnr, "PIM_ARTIKELTEXT", lang_cfg["pim_locale"])
                if text:
                    marketing_texts_by_language[lang_cfg["gs1_code"]] = text
            if marketing_texts_by_language:
                extension_modules.append(
                    gs1_exporter.build_marketing_information_module_element(marketing_texts_by_language)
                )
            else:
                logger.info(
                    "GS1 EXPORT | matnr=%s: kein PIM_ARTIKELTEXT gefunden, "
                    "marketingInformationModule wird ausgelassen",
                    matnr,
                )

            # Fixed-value modules (GS1 errors G1013/G1004/500.061) - no
            # per-article source exists for any of these yet, see the builder
            # functions' docstrings.
            extension_modules.append(gs1_exporter.build_variable_trade_item_information_module_element(False))
            extension_modules.append(gs1_exporter.build_delivery_purchasing_information_module_element())
            packaging_descriptions = {
                lang_cfg["gs1_code"]: _PACKAGING_TYPE_DESCRIPTIONS_BY_GS1_CODE.get(lang_cfg["gs1_code"], "BOX")
                for _, lang_cfg in languages
            }
            extension_modules.append(
                gs1_exporter.build_packaging_information_module_element(_PACKAGING_TYPE_CODE, packaging_descriptions)
            )

            trade_item = gs1_exporter.build_trade_item_element(
                matnr=matnr,
                gtin=gtin,
                gpc_category_code=brick_id,
                properties=properties,
                sender_gln=GS1_SENDER_GLN,
                sender_party_name=GS1_SENDER_PARTY_NAME,
                target_market_country_code=target_market_country_code,
                classification_system_code=GS1_ADDITIONAL_CLASSIFICATION_SYSTEM_CODE,
                extension_modules=extension_modules,
                contact_name=GS1_SUPPORT_CONTACT_NAME,
                contact_email=GS1_SUPPORT_CONTACT_EMAIL,
            )
            catalogue_item = gs1_exporter.build_catalogue_item_element(trade_item)
            notifications.append(gs1_exporter.build_notification_element(catalogue_item, sender_gln=GS1_SENDER_GLN))
            values = snapshot.snapshot_trade_item(trade_item)
            if snapshots is not None:
                snapshots[matnr] = values
            if record_history:
                # params: needed to rebuild the article identically for the change report
                export_params = {"lang_codes": lang_codes, "vkorg": vkorg, "werks": werks}
                database.upsert_export_history(conn, matnr, brick_id, pim_updated_at, exported_by,
                                               {"params": export_params, "values": values})
    finally:
        conn.close()
        if hana_conn is not None:
            hana_conn.close()

    if not GS1_SENDER_GLN:
        warnings.append("GS1_SENDER_GLN ist nicht konfiguriert – Sender/BrandOwner-GLN fehlt im XML.")
    if not GS1_RECEIVER_GLN:
        warnings.append("GS1_RECEIVER_GLN ist nicht konfiguriert – Receiver-GLN fehlt im XML.")

    xml_bytes = gs1_exporter.build_document(notifications, sender_gln=GS1_SENDER_GLN, receiver_gln=GS1_RECEIVER_GLN)
    validation_errors = validator.validate_xml(xml_bytes)

    return xml_bytes, warnings, validation_errors


def get_changed_articles() -> list[dict]:
    """Previously GS1-exported articles whose PIM data has changed since
    their last export (see database.get_changed_articles()). Opens its own
    connection, same self-contained pattern as export_batch(). Raises
    PipelineError on hard failures (e.g. DB unreachable); returns [] if
    gs1_export_history doesn't exist yet (nothing exported/tracked so far).
    """
    try:
        conn = database.get_connection()
    except Exception as exc:
        raise PipelineError(f"Datenbankverbindung fehlgeschlagen: {exc}") from exc

    try:
        return database.get_changed_articles(conn)
    finally:
        conn.close()


def get_article_changes(matnr: str) -> Optional[list[dict]]:
    """What changed for *matnr* since its last GS1 export: the article is rebuilt
    with the same languages/VKORG/plant as back then (nothing is written) and
    compared value by value with the stored snapshot. None if the export has
    no snapshot yet (exported before this feature existed)."""
    try:
        conn = database.get_connection()
    except Exception as exc:
        raise PipelineError(f"Datenbankverbindung fehlgeschlagen: {exc}") from exc
    try:
        stored = database.get_export_snapshot(conn, matnr)
        if stored is None:
            return None
        names = database.get_pick_names(conn, stored["brick_id"]) if stored.get("brick_id") else {}
    finally:
        conn.close()

    params = stored.get("params", {})
    current: dict = {}
    export_batch([matnr], params.get("lang_codes", []), params.get("vkorg", ""), None, params.get("werks", ""),
                 record_history=False, snapshots=current)
    return snapshot.diff_snapshots(stored.get("values", {}), current.get(matnr, {}), names)


def save_export_file(filename: str, exported_by: Optional[str], matnrs: list[str], xml_bytes: bytes) -> None:
    """Archive a generated GS1 XML file so exported_by can find/re-download it
    later, see get_export_file()/list_export_files(). Swallows DB-connection
    failures (logs only) rather than raising - saving an already-successful
    export/download must never fail the request."""
    try:
        conn = database.get_connection()
    except Exception as exc:
        logger.error("GS1 EXPORT | Datei konnte nicht archiviert werden (Verbindung): %s", exc)
        return

    try:
        database.save_export_file(conn, filename, exported_by, matnrs, xml_bytes)
    finally:
        conn.close()


def list_export_files(exported_by: str) -> list[dict]:
    """Files previously generated by exported_by, newest first. Raises
    PipelineError on hard failures (e.g. DB unreachable)."""
    try:
        conn = database.get_connection()
    except Exception as exc:
        raise PipelineError(f"Datenbankverbindung fehlgeschlagen: {exc}") from exc

    try:
        return database.list_export_files(conn, exported_by)
    finally:
        conn.close()


def get_export_file(file_id: int, exported_by: str) -> Optional[dict]:
    """A single stored export file, scoped to exported_by. Raises
    PipelineError on hard failures (e.g. DB unreachable)."""
    try:
        conn = database.get_connection()
    except Exception as exc:
        raise PipelineError(f"Datenbankverbindung fehlgeschlagen: {exc}") from exc

    try:
        return database.get_export_file(conn, file_id, exported_by)
    finally:
        conn.close()
