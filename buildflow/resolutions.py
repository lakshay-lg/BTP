"""Apply explicit planning decisions without changing original extraction evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from .normalization import review_document
from .normalization_schema import ITEM, MAX_JSON_BYTES, strict_json, validate_schema
from .review_options import resolution_options, explain_checks
from .review_schema import MAX_REVIEW_BYTES, REVIEW_SCHEMA, REVIEW_VERSION

# These are value comparisons, not structural/evidence/engineering checks.
VALUE_CHECK_FIELDS = {
    'QUANTITY_MISMATCH':'quantity', 'UNIT_MISMATCH':'unit', 'RATE_MISMATCH':'rate',
    'AMOUNT_MISMATCH':'amount', 'MISSING_AMOUNT_VALUE':'amount',
    'INVALID_AMOUNT_BASIS':'amount', 'INVALID_AMOUNT_DERIVATION':'amount',
    'SOURCE_AMOUNT_CONFLICT':'amount',
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def review_input(content, sources):
    payload = strict_json(content, MAX_REVIEW_BYTES)
    is_review = isinstance(payload, dict) and payload.get('schema_version') == REVIEW_VERSION
    if is_review:
        validate_schema(payload, REVIEW_SCHEMA)
        original = payload['normalized_document']
        if len(encoded(original)) > MAX_JSON_BYTES:
            raise ValueError('Embedded normalization document exceeds the 2 MB limit')
        hashes = {name:hashlib.sha256(data).hexdigest() for name,data in sources.items()}
        if hashes != payload['source_sha256']:
            raise ValueError('Original workbook hashes changed. Upload the exact originals or begin a new review from the original normalized JSON.')
        decisions = deepcopy(payload['decisions'])
        settings = deepcopy(payload['review_settings'])
    else:
        if len(content) > MAX_JSON_BYTES:
            raise ValueError('Normalized JSON exceeds the 2 MB limit')
        original = validate_schema(payload)
        decisions, settings = [], {}
    original_report = review_document(encoded(original), sources)
    options = resolution_options(original, sources)
    by_target = {(o['item_id'],o['field']):o for o in options}
    effective = deepcopy(original)
    rows = {i['id']:i for i in effective['items']}
    chosen, audit = {}, []
    now = datetime.now(timezone.utc).isoformat()
    for d in decisions:
        target = d['item_id'], d['field']
        if target in chosen:
            raise ValueError('Duplicate decision target')
        option = by_target.get(target)
        if not option or d['action'] not in option['actions']:
            raise ValueError('Decision target/action is unavailable or has an ambiguous source identity')
        if not d['reviewer'].strip():
            raise ValueError('Each decision requires a named reviewer')
        d['reason']=d.get('reason','').strip() or 'Not provided'
        if d['recorded_at']:
            try:
                stamp=datetime.fromisoformat(d['recorded_at'])
                if stamp.tzinfo is None:
                    raise ValueError('Timezone required')
            except ValueError as exc:
                raise ValueError('Invalid decision timestamp') from exc
        else:
            d['recorded_at']=now
        field = d['field']
        if field != 'source_evidence':
            validate_schema(d['value'], ITEM['properties'][field])
        if d['action']=='source':
            value=option['source_value']
            if encoded(d['value']) != encoded(value) and d['value'] != value:
                raise ValueError('Submitted source choice differs from the locally derived value')
        elif d['action']=='ai':
            value=option['proposed_value']
            if d['value'] != value:
                raise ValueError('Submitted AI choice differs from the original proposal')
        else:
            value=d['value']
        if field=='source_evidence':
            # Patch whitelist and values are wholly computed from the original workbook.
            if d['value'] != value:
                raise ValueError('Evidence repair differs from the locally derived patch')
            rows[d['item_id']].update(deepcopy(value))
            status='corrected_to_source'
        else:
            rows[d['item_id']][field]=value
            status = 'source_matched' if option['source_available'] and value==option['source_value'] else 'override_acknowledged'
            if status=='source_matched' and value!=option['proposed_value']:
                status='corrected_to_source'
        chosen[target]=d
        audit.append(dict(**d,source_value=option['source_value'],source_available=option['source_available'],
            source_ref=option['source_ref'],proposed_value=option['proposed_value'],effective_value=value,status=status))

    # Counts describe derived rows; never hide a bad summary in the original input.
    effective['extraction_summary'].update(
        missing_quantity_count=sum(i['quantity'] is None for i in effective['items']),
        unpriced_item_count=sum(i['amount'] is None for i in effective['items']),
        unclassified_item_count=sum(i['work_package']=='unknown' for i in effective['items']))
    effective_report=review_document(encoded(effective),sources)
    calculation_checks=[]
    effective_checks=deepcopy(effective_report['checks'])
    for check in effective_checks:
        field=VALUE_CHECK_FIELDS.get(check['code']) if check.get('origin')!='model' else None
        decision=chosen.get((check.get('item_id'),field)) if field else None
        if decision:
            check['resolution_status']='override_acknowledged' if any(a['item_id']==decision['item_id'] and a['field']==field and a['status']=='override_acknowledged' for a in audit) else 'corrected_to_source'
        else:
            check['resolution_status']='unresolved'
            calculation_checks.append(deepcopy(check))
    if any(c['code']=='SUMMARY_MISMATCH' for c in original_report['checks']) and not any(c['code']=='SUMMARY_MISMATCH' for c in calculation_checks):
        calculation_checks.extend(deepcopy(c) for c in original_report['checks'] if c['code']=='SUMMARY_MISMATCH')

    for item in effective['items']:
        identity=item['id']
        unit_source=by_target.get((identity,'unit'))
        unit_decision=chosen.get((identity,'unit'))
        quantity_decision=chosen.get((identity,'quantity'))
        if unit_decision and (not unit_source['source_available'] or item['unit']!=unit_source['source_value']) and not quantity_decision:
            calculation_checks.append(dict(code='UNIT_QUANTITY_REVIEW',item_id=identity,blocks=['schedule'],
                message=f'{identity}: changing the source unit requires an explicit quantity decision'))
        qty,rate,amount=item['quantity'],item['rate'],item['amount']
        if qty is not None and rate is not None and amount is not None:
            product=Decimal(str(qty))*Decimal(str(rate))
            if abs(product-Decimal(str(amount)))>Decimal('0.01'):
                amount_decision=chosen.get((identity,'amount'))
                # A differing amount still requires an explicit decision; reasons are optional.
                if not amount_decision:
                    calculation_checks.append(dict(code='EFFECTIVE_AMOUNT_CONFLICT',item_id=identity,blocks=['cashflow'],
                        message=f'{identity}: chosen amount {amount} differs from chosen quantity × rate ({product})',
                        suggested_amount=float(product)))
    original_checks=explain_checks(deepcopy(original_report['checks']))
    explain_checks(effective_checks)
    explain_checks(calculation_checks)
    ready=not any(set(c['blocks']) & {'extraction','schedule'} for c in calculation_checks)
    result=dict(original_report)
    result.update(items=effective['items'], resolution_options=options, decision_audit=audit,
        original_checks=original_checks,checks=original_checks,effective_checks=effective_checks,
        calculation_checks=calculation_checks,admission_ready=ready,
        has_overrides=any(a['status']=='override_acknowledged' for a in audit),
        schedule_supported=ready, prices_complete=effective_report['prices_complete'],
        priced_total=effective_report['priced_total'],unpriced_item_count=effective_report['unpriced_item_count'],
        reviewed_document=dict(schema_version=REVIEW_VERSION,normalized_document=original,
            source_sha256=original_report['source_sha256'],decisions=decisions,review_settings=settings,review_state='draft'))
    return result
