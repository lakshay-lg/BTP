"""Admission gate for source-verified, explicitly reviewed normalized inputs."""
from __future__ import annotations

from datetime import date, datetime, timezone

from .agent import ProjectAgent
from .models import BOQItem, TraceEvent, Finding, config_from_dict
from .scheduling import productivity_for, template_for
from .taxonomy import TAXONOMY, infer_track


class ReviewBlocked(ValueError):
    pass


def analyze_reviewed(report, settings):
    yes = lambda key: str(settings.get(key, '')).lower() == 'true'
    reviewer = settings.get('reviewer','').strip()
    if not reviewer or len(reviewer)>120 or not yes('review_confirmed'):
        raise ReviewBlocked('A named planner must confirm the descriptions, classifications and complete source inventory')
    if not yes('scope_confirmed') or settings.get('quantity_basis')!='project_total':
        raise ReviewBlocked('Confirm that the reviewed effective quantities are totals for the entire project; per-structure scaling is not supported')
    if 'calculation_checks' not in report and not report['source_verified']:
        raise ReviewBlocked('Source verification failed. Correct the normalized file before calculating')
    if settings.get('typology') not in {'rwh','building','stp_tank','linear_mep'}:
        raise ReviewBlocked('Select the project typology explicitly')
    if not settings.get('start_date') or not settings.get('structure_count'):
        raise ReviewBlocked('Provide the project start date and structure count')
    date.fromisoformat(settings['start_date'])
    cash_mode=settings.get('cashflow_mode','schedule')
    if cash_mode not in {'schedule','compare','phase'}:
        raise ReviewBlocked('Choose a supported cash-flow mode')
    if cash_mode in {'compare','phase'} and (not settings.get('contract_duration_days') or not yes('contract_confirmed')):
        raise ReviewBlocked('Independent cash flow requires a separately confirmed contract duration in working days')
    resolved={'QUANTITY_SCOPE_UNRESOLVED'}
    if settings.get('contract_duration_days') and yes('contract_confirmed'):
        resolved |= {'MISSING_CONTRACT_DURATION','DURATION_BASIS_UNRESOLVED'}
    for check in report.get('calculation_checks',report['checks']):
        if check.get('origin')=='model' and check['code'] in resolved:
            continue
        blocks=set(check['blocks'])
        if 'schedule' in blocks or 'extraction' in blocks or (cash_mode!='schedule' and 'cashflow' in blocks):
            raise ReviewBlocked(check['message'])
    data={key:value for key,value in settings.items() if value!=''}
    data.update(use_llm_fallback=False, data_provenance='reviewed_chat_import',cashflow_mode=cash_mode)
    if not yes('contract_confirmed'):
        data.pop('contract_duration_days',None)
    config=config_from_dict(data)
    if config.contract_duration_days and config.contract_duration_days>3650:
        raise ReviewBlocked('Review duration exceeds the supported 3,650-working-day horizon')
    phases={taxon.key:taxon.phase for taxon in TAXONOMY}
    items=[]
    for row in report['items']:
        items.append(BOQItem(id=row['id'],description=row['normalized_description'],unit=row['unit'],
            quantity=row['quantity'],rate=row['rate'] if row['rate'] is not None else 0,
            amount=row['amount'] if row['amount'] is not None else 0,
            amount_missing=row['amount'] is None,source_row=row['source']['row'],source_sheet=row['source']['sheet'],
            work_package=row['work_package'],phase=phases[row['work_package']],
            track=infer_track(row['normalized_description'].lower(),row['work_package']),
            confidence=None,classifier='planner_reviewed',source_metadata=row,
            evidence=[f'{row["source"]["file"]}: {row["source"]["sheet"]}!{cell}' for cell in row['description_refs']],
            flags=['Missing source price; cash flow unavailable'] if row['amount'] is None else []))
    specs=template_for(config.typology,config)
    for item in items:
        shares=sum(spec.allocations.get(item.work_package,0) for spec in specs if not spec.accepted_tracks or item.track in spec.accepted_tracks)
        if abs(shares-1)>1e-6:
            raise ReviewBlocked(f'{item.id}: the selected typology does not allocate this operation exactly once')
    workload=sum(i.quantity/productivity_for(i.work_package,i.unit) for i in items)/config.crew_multiplier
    if workload>3650:
        raise ReviewBlocked('Workload exceeds the review horizon; verify quantities, units and crew capacities')
    result=ProjectAgent().run(items,config,reviewed=True)
    result.normalization_review=dict(reviewer=reviewer,reviewed_at=datetime.now(timezone.utc).isoformat(),
        source_sha256=report['source_sha256'],normalized_sha256=report['normalized_sha256'],
        quantity_basis='project_total',contract_duration_confirmed=yes('contract_confirmed'),
        confirmations=['Descriptions, labels, decisions and complete source inventory reviewed','Effective quantities confirmed as project totals'],
        source_document=report['document'],has_overrides=report.get('has_overrides',False),
        decision_audit=report.get('decision_audit',[]),source_checks=report.get('original_checks',report['checks']))
    if report.get('has_overrides'):
        result.findings.append(Finding('warning','PLANNER_INPUT_OVERRIDES',
            'Planner-overridden inputs: calculations include values or assumptions not verified as source facts.',
            'Inspect Input Decisions and obtain project approval before relying on this draft.'))
        result.assumptions.append('Planner-overridden inputs are used. Original Excel evidence and AI proposals are retained in the decision audit.')
    if cash_mode=='schedule':
        # Never expose the legacy CPM-duration fallback as independent evidence.
        result.independent_cashflow=[]
        result.cashflow=[]
        result.metrics['cashflow_comparison']={}
        result.assumptions=[a for a in result.assumptions if not a.startswith('The independent cash curve')]
        result.assumptions.append('No independent cash-flow comparison was requested for this reviewed import.')
    result.assumptions.append(f'Normalized BOQ source evidence and effective classifications reviewed by {reviewer}; effective quantities are confirmed project totals.')
    result.trace.append(TraceEvent(
        len(result.trace)+1,'review_normalized_source','completed','Source-checked chat import approved by planner',
        {key:value for key,value in result.normalization_review.items() if key!='source_document'}))
    return result
