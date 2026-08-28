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

from . import database, gs1_exporter, gs1_mapping, pim_reader, validator

load_dotenv()

logger = logging.getLogger(__name__)

_LANGUAGE_CONFIG_PATH = Path(__file__).with_name("language_mapping.json")
_VKORG_COUNTRY_CONFIG_PATH = Path(__file__).with_name("vkorg_country_mapping.json")
_VKORG_VAT_CONFIG_PATH = Path(__file__).with_name("vkorg_vat_mapping.json")

# Picks whose propertyMeasurement value is the sum of several numbers found in
# one free-text PIM field, rather than a single clean number (e.g. 7.041
# "Nominal Power Consumption" on ceiling fans: PIM_MAX_POWER_INCL_TRANSFORMER
# combines fan + light wattage as "22W = FAN; 18W = LIGHT").
_SUM_ALL_NUMBERS_PICKS = {"7.041"}

GS1_SENDER_GLN = os.getenv("GS1_SENDER_GLN", "")
GS1_SENDER_PARTY_NAME = os.getenv("GS1_SENDER_PARTY_NAME", "EGLO")
GS1_RECEIVER_GLN = os.getenv("GS1_RECEIVER_GLN", "")
GS1_ADDITIONAL_CLASSIFICATION_SYSTEM_CODE = os.getenv("GS1_ADDITIONAL_CLASSIFICATION_SYSTEM_CODE", "64")


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


def export_batch(matnrs: list[str], lang_codes: list[str], vkorg: str = "") -> tuple[bytes, list[str], list[dict]]:
    """Build a combined GS1 XML document for *matnrs*.

    Returns (xml_bytes, warnings, validation_errors).
    Raises PipelineError on hard failures (e.g. DB connection).
    """
    language_config = _load_json_config(_LANGUAGE_CONFIG_PATH)
    languages = [(code, language_config[code]) for code in lang_codes if code in language_config]

    vkorg_country_config = _load_json_config(_VKORG_COUNTRY_CONFIG_PATH)
    target_market_country_code = vkorg_country_config.get(vkorg.strip(), "")

    vkorg_vat_config = _load_json_config(_VKORG_VAT_CONFIG_PATH)
    vat_info = vkorg_vat_config.get(vkorg.strip())

    unique_matnrs = list(dict.fromkeys(m.strip() for m in matnrs if m.strip()))
    warnings: list[str] = []
    notifications = []

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
                    pim_codes = pim_reader.get_pim_raw_codes(product, pimfeld)
                    if not pim_codes:
                        logger.info(
                            "GS1 EXPORT | matnr=%s pickid=%s pimfeld=%s: kein PIM-Rohcode, "
                            "Property wird ausgelassen",
                            matnr, pickid, pimfeld,
                        )
                        continue

                    code_value = _resolve_crosswalk_value(conn, pickid, pim_codes)
                    if not code_value:
                        logger.info(
                            "GS1 EXPORT | matnr=%s pickid=%s pim_codes=%s: kein Crosswalk-Eintrag "
                            "in gs1_pim_value_crosswalk, Property wird ausgelassen",
                            matnr, pickid, pim_codes,
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
            )
            catalogue_item = gs1_exporter.build_catalogue_item_element(trade_item)
            notifications.append(gs1_exporter.build_notification_element(catalogue_item, sender_gln=GS1_SENDER_GLN))
    finally:
        conn.close()

    if not GS1_SENDER_GLN:
        warnings.append("GS1_SENDER_GLN ist nicht konfiguriert – Sender/BrandOwner-GLN fehlt im XML.")
    if not GS1_RECEIVER_GLN:
        warnings.append("GS1_RECEIVER_GLN ist nicht konfiguriert – Receiver-GLN fehlt im XML.")

    xml_bytes = gs1_exporter.build_document(notifications, sender_gln=GS1_SENDER_GLN, receiver_gln=GS1_RECEIVER_GLN)
    validation_errors = validator.validate_xml(xml_bytes)

    return xml_bytes, warnings, validation_errors
