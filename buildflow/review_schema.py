"""Strict saved-review envelope; metadata is never a reusable approval token."""
from .normalization_schema import SCHEMA, NAME, TEXT, obj, arr, enum

REVIEW_VERSION = 'buildflow.reviewed-boq.v0.1'
MAX_REVIEW_BYTES = 5 * 1024 * 1024
EDIT_FIELDS = ('quantity', 'unit', 'rate', 'amount', 'normalized_description', 'work_package', 'operation')
SETTING_KEYS = ('name', 'typology', 'start_date', 'structure_count', 'quantity_basis',
                'cashflow_mode', 'contract_duration_days', 'reviewer')
REVIEW_SCHEMA = obj(dict(
    schema_version=dict(const=REVIEW_VERSION), normalized_document=SCHEMA,
    source_sha256=dict(type='object', minProperties=1, maxProperties=10,
                       additionalProperties=dict(type='string', pattern='^[a-f0-9]{64}$')),
    decisions=arr(obj(dict(item_id=NAME, field=enum(*EDIT_FIELDS, 'source_evidence'),
        action=enum('source', 'ai', 'manual'), value={}, reason=TEXT,
        reviewer=dict(type='string', minLength=1, maxLength=120),
        recorded_at=dict(type='string', maxLength=40))), maximum=16000),
    review_settings=dict(type='object', properties={key:dict(type='string',maxLength=250) for key in SETTING_KEYS},
                         additionalProperties=False),
    review_state=dict(const='draft'),
))
# A missing reason has the same meaning as a blank UI field.
REVIEW_SCHEMA['properties']['decisions']['items']['required'].remove('reason')
