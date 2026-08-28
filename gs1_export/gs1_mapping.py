"""
Loads Brick/Pick mapping data (public.gs1_mapping, public.gs1_brick_mapping).
Thin wrapper around database.py so pipeline.py doesn't talk SQL directly.
"""
from typing import Optional

import psycopg

from . import database


def get_brick_id(conn: psycopg.Connection, zztypen_code: str) -> Optional[str]:
    return database.get_brick_id(conn, zztypen_code)


def get_active_mappings(conn: psycopg.Connection, brick_id: str) -> list[dict]:
    return database.get_active_mappings(conn, brick_id)


def get_gdsn_attribute_type(conn: psycopg.Connection, brick_id: str, pick_id: str) -> Optional[str]:
    return database.get_gdsn_attribute_type(conn, brick_id, pick_id)


def get_measurement_unit_code(conn: psycopg.Connection, brick_id: str, pick_id: str) -> Optional[str]:
    return database.get_measurement_unit_code(conn, brick_id, pick_id)


def get_manual_property_value(conn: psycopg.Connection, matnr: str, pick_id: str) -> Optional[str]:
    return database.get_manual_property_value(conn, matnr, pick_id)


def get_pim_value_crosswalk(conn: psycopg.Connection, pick_id: str, pim_code: str) -> Optional[str]:
    return database.get_pim_value_crosswalk(conn, pick_id, pim_code)


def get_pim_value_crosswalk_exact(conn: psycopg.Connection, pick_id: str, pim_code: str) -> Optional[str]:
    return database.get_pim_value_crosswalk_exact(conn, pick_id, pim_code)


def get_pim_value_crosswalk_wildcard(conn: psycopg.Connection, pick_id: str) -> Optional[str]:
    return database.get_pim_value_crosswalk_wildcard(conn, pick_id)
