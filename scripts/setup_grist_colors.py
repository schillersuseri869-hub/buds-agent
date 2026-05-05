#!/usr/bin/env python3
"""
Настройка цветовых правил в Grist для таблицы PricingReport.
Запускается один раз после создания таблицы.

Что делает:
  1. Добавляет 5 формульных колонок-правил в PricingReport (по одной на статус)
  2. Применяет эти правила ко всем полям секции rawViewSectionRef=20

Запуск:
    uv run python scripts/setup_grist_colors.py
"""
import json
import os
import sys
from pathlib import Path

import httpx

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

GRIST_URL = os.environ.get("GRIST_URL", "http://82.22.3.55:8484").rstrip("/")
GRIST_DOC_ID = os.environ.get("GRIST_DOC_ID", "sCXE5vuggkAZ")
GRIST_API_KEY = os.environ.get("GRIST_API_KEY", "15a30ce05a02cc91a197c69c516f3fb41c27a193")
TABLE_NAME = "PricingReport"
SECTION_REF = 19  # primary view section ref для PricingReport (rawViewSection=20 is read-only)

HEADERS = {"Authorization": f"Bearer {GRIST_API_KEY}"}
BASE = f"{GRIST_URL}/api/docs/{GRIST_DOC_ID}"

STATUS_RULES = [
    ("danger",   "$status == 'danger'",   "#2d1a1a", "#ef4444"),
    ("warning",  "$status == 'warning'",  "#2d200a", "#f97316"),
    ("info",     "$status == 'info'",     "#0d1f2d", "#3b82f6"),
    ("ok",       "$status == 'ok'",       "#0d1f12", "#22c55e"),
    ("no_promo", "$status == 'no_promo'", "#18181b", "#71717a"),
]


def apply(actions: list) -> dict:
    r = httpx.post(f"{BASE}/apply", headers=HEADERS, json=actions, timeout=30)
    r.raise_for_status()
    return r.json()


def get_all_col_ids_for_table(table_ref: int) -> dict:
    """Returns {colId: colRef} for all columns (including formula) for given tableRef."""
    r = httpx.get(f"{BASE}/tables/_grist_Tables_column/data", headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    col_ids = data.get("colId", [])
    col_refs = data.get("id", [])
    parent_ids = data.get("parentId", [])
    return {
        cid: cref
        for cid, cref, pid in zip(col_ids, col_refs, parent_ids)
        if pid == table_ref
    }


def get_table_ref(table_name: str) -> int:
    """Returns tableRef for a given table name."""
    r = httpx.get(f"{BASE}/tables/_grist_Tables/data", headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    ids = data.get("id", [])
    table_ids = data.get("tableId", [])
    for tid, tname in zip(ids, table_ids):
        if tname == table_name:
            return tid
    raise ValueError(f"Table {table_name!r} not found")


def get_section_fields(section_ref: int) -> list[dict]:
    """Returns list of {id, colRef} for fields in given section."""
    r = httpx.get(f"{BASE}/tables/_grist_Views_section_field/data", headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    ids = data.get("id", [])
    parent_ids = data.get("parentId", [])
    col_refs = data.get("colRef", [])
    return [
        {"id": fid, "colRef": cref}
        for fid, pid, cref in zip(ids, parent_ids, col_refs)
        if pid == section_ref
    ]


def main():
    print(f"Grist: {GRIST_URL}, doc: {GRIST_DOC_ID}")

    table_ref = get_table_ref(TABLE_NAME)
    print(f"Table {TABLE_NAME!r} has tableRef={table_ref}")

    existing = get_all_col_ids_for_table(table_ref)
    print(f"Existing columns ({len(existing)}): {sorted(existing.keys())}")

    # Step 1: Create missing rule columns
    actions_to_apply = []
    needed_rule_cols = []
    for status_val, formula, bg, fg in STATUS_RULES:
        col_id = f"gristHelper_ConditionalRule_{status_val}"
        if col_id in existing:
            print(f"  {col_id} already exists (colRef={existing[col_id]})")
            needed_rule_cols.append((col_id, existing[col_id]))
        else:
            print(f"  Will create: {col_id}")
            actions_to_apply.append(["AddColumn", TABLE_NAME, col_id, {
                "type": "Bool",
                "isFormula": True,
                "formula": formula,
                "label": col_id,
                "widgetOptions": json.dumps({"fillColor": bg, "textColor": fg}),
            }])
            needed_rule_cols.append((col_id, None))  # colRef filled after apply

    if actions_to_apply:
        print(f"\nCreating {len(actions_to_apply)} rule columns...")
        result = apply(actions_to_apply)
        ret_values = result.get("retValues", [])
        print(f"  retValues: {ret_values}")

        # Fill in the colRefs returned by AddColumn
        new_col_refs = {rv["colId"]: rv["colRef"] for rv in ret_values if rv and "colId" in rv}
        needed_rule_cols = [
            (col_id, new_col_refs.get(col_id, cref))
            for col_id, cref in needed_rule_cols
        ]
        print(f"  Created: {new_col_refs}")

    rule_col_refs = [cref for _, cref in needed_rule_cols]
    print(f"\nRule colRefs: {rule_col_refs}")

    if None in rule_col_refs:
        print("ERROR: some rule colRefs are missing — aborting")
        sys.exit(1)

    # Step 2: Get field refs in the view section
    fields = get_section_fields(SECTION_REF)
    # Exclude the rule columns themselves from the list of fields to apply rules to
    data_field_refs = [f["id"] for f in fields if f["colRef"] not in rule_col_refs]
    print(f"Section {SECTION_REF} data fields: {data_field_refs}")

    if not data_field_refs:
        print("ERROR: no data fields found in section — check SECTION_REF")
        sys.exit(1)

    # Step 3: Set rules on all data fields
    # Grist stores RefList as ["L", ref1, ref2, ...] in the internal format
    ref_list = ["L"] + rule_col_refs
    rules_value = [ref_list] * len(data_field_refs)
    print(f"\nApplying color rules to {len(data_field_refs)} fields...")
    apply([["BulkUpdateRecord", "_grist_Views_section_field", data_field_refs, {
        "rules": rules_value,
    }]])
    print("Done! Color rules applied.")


if __name__ == "__main__":
    main()
