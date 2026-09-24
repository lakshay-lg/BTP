"""Versioned chat-output contract. This accepts proposals, never approvals."""
from __future__ import annotations

import json
import math

from jsonschema import Draft202012Validator

from .taxonomy import TAXONOMY


VERSION = 'buildflow.normalized-boq.v0.1'
MAX_JSON_BYTES = 2 * 1024 * 1024


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def arr(items, minimum=0, maximum=5000):
    return dict(type='array', items=items, minItems=minimum, maxItems=maximum)


def enum(*values):
    return dict(enum=list(values))


TEXT = dict(type='string', maxLength=30000)
NAME = dict(type='string', minLength=1, maxLength=255)
NULL_TEXT = dict(type=['string', 'null'], maxLength=30000)
NUMBER = dict(type=['number', 'null'], minimum=-1e12, maximum=1e12)
ROW = dict(type='integer', minimum=1, maximum=50000)
COUNT = dict(type='integer', minimum=0, maximum=50000)
CELL = dict(type='string', pattern=r'^[A-Z]{1,3}[1-9][0-9]{0,4}$')
NULL_CELL = dict(anyOf=[CELL, dict(type='null')])
SCOPE = enum('project_total', 'per_structure', 'mixed', 'unresolved')
REFERENCE = obj(dict(file=NAME, sheet=NAME, cell=CELL))
SOURCE = obj(dict(file=NAME, sheet=NAME, row=ROW))
ITEM = obj(dict(
    id=NAME, source=SOURCE, source_item_number=NULL_TEXT, section=NULL_TEXT,
    raw_description=TEXT, description_refs=arr(CELL, 1, 20),
    context=arr(obj(dict(cell=CELL, text=TEXT)), maximum=100),
    normalized_description=dict(type='string', minLength=1, maxLength=30000),
    quantity=NUMBER, quantity_ref=NULL_CELL, source_unit=NULL_TEXT, unit_ref=NULL_CELL,
    unit=enum('m3','m2','kg','m','no','h','t','day','ls','ft2','point','unknown'),
    quantity_basis=SCOPE, quantity_role=enum('physical','cost_only','unresolved'),
    rate=NUMBER, rate_ref=NULL_CELL, amount=NUMBER, amount_ref=NULL_CELL, amount_formula=NULL_TEXT,
    amount_basis=enum('source_value','quantity_times_rate','unresolved_formula','missing'),
    work_package=enum(*(tuple(t.key for t in TAXONOMY)+('filter_media','access_metalwork','unknown'))),
    operation=dict(type='string', minLength=1, maxLength=100), classification_reason=TEXT,
    review_status=enum('proposed','needs_review'), issue_ids=arr(NAME),
))
SCHEMA = obj(dict(
    schema_version=dict(const=VERSION), complete=dict(const=True),
    source_files=arr(obj(dict(file=NAME,role=enum('original_boq'))),1,10),
    project=obj(dict(
        name=NULL_TEXT, typology_proposal=enum('building','stp_tank','linear_mep','rwh','unknown'),
        structure_count=dict(type=['integer','null'], minimum=1,maximum=100), quantity_basis=SCOPE,
        contract_duration=NUMBER, contract_duration_basis=enum('working_days','calendar_days',None),
        start_date=NULL_TEXT, price_basis=enum('single_price_column','selected_bid','unpriced','unresolved'),
        selected_bidder=NULL_TEXT,currency=NULL_TEXT,source_total=NUMBER,
        source_total_ref=dict(anyOf=[REFERENCE,dict(type='null')]),
        evidence=arr(obj(dict(field=NAME,file=NAME,sheet=NAME,cell=CELL,text=TEXT))),
    )),
    source_inventory=arr(obj(dict(file=NAME,sheet=NAME,
        role=enum('boq','reference_schedule','reference_cashflow','summary','other'),
        item_rows=arr(ROW), non_item_rows=arr(obj(dict(row=ROW,
        role=enum('title','header','section','parent_description','continuation','subtotal','note','other'),reason=TEXT))),
    )),1,100),
    items=arr(ITEM,1,2000),
    issues=arr(obj(dict(id=NAME,code=NAME,item_ids=arr(NAME),source_refs=arr(REFERENCE),
        message=TEXT,blocks=arr(enum('extraction','schedule','cashflow'),maximum=3),question_for_reviewer=TEXT))),
    extraction_summary=obj({key:COUNT for key in ('source_leaf_item_count','emitted_item_count',
        'missing_quantity_count','unpriced_item_count','unclassified_item_count','issue_count')}),
))
SCHEMA['$schema'] = 'https://json-schema.org/draft/2020-12/schema'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _finite_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON numbers are not allowed')
    return number


def _invalid_constant(value):
    raise ValueError(f'Invalid JSON number: {value}')


def strict_json(content: bytes, limit=MAX_JSON_BYTES):
    if len(content) > limit:
        raise ValueError(f'JSON exceeds the {limit // (1024 * 1024)} MB limit')
    try:
        return json.loads(content.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                              parse_float=_finite_number, parse_constant=_invalid_constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Normalized JSON must be a bounded UTF-8 JSON document') from exc


def validate_schema(document, schema=SCHEMA):
    error = next(Draft202012Validator(schema).iter_errors(document), None)
    if error:
        path = '.'.join(str(p) for p in error.absolute_path) or 'document'
        raise ValueError(f'Invalid normalization contract at {path}: {error.message[:300]}')
    return document


def load_document(content: bytes) -> dict:
    return validate_schema(strict_json(content))
