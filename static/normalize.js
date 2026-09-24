const q = (selector) => document.querySelector(selector);
const escapeText = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currency = value => value === null ? 'Unavailable' : new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',maximumFractionDigits:2}).format(value);
let report = null;
let generation = 0;
let busy = false;
let dirty = false;
let editor = null;
const confirmableCodes = new Set(['QUANTITY_SCOPE_UNRESOLVED','MISSING_CONTRACT_DURATION','DURATION_BASIS_UNRESOLVED']);

function showError(message) {
  q('#reviewError').textContent = message;
  q('#reviewError').classList.toggle('hidden', !message);
}

function admission() {
  const compare = q('#importCashMode').value === 'compare';
  q('#importContractDays').required = compare;
  q('#importContractConfirmed').required = compare;
  const hardBlocks = report?.calculation_checks.filter(c => !(c.origin === 'model' && confirmableCodes.has(c.code)) &&
    c.blocks.some(b => b === 'extraction' || b === 'schedule' || (compare && b === 'cashflow'))) || [];
  q('#analyzeImport').disabled = busy || dirty || !report || hardBlocks.length > 0;
  q('#downloadReview').disabled = busy || dirty || !report;
  q('#recheckDecisions').disabled = busy || !report;
  q('#useAllAI').disabled = busy || !report;
  q('#admissionNote').textContent = dirty ? 'Decisions changed. Recheck before saving or running.' : hardBlocks.length ?
    `${hardBlocks.length} blocking check(s) need resolution. Use the decision editor or follow the repair guidance. Unsupported operations require a model capability update; do not relabel them to bypass the check.` :
    'Confirm the source interpretation and project settings below. Every calculation request rechecks both uploaded files.';
}

function renderReview(data, initialize=true) {
  report = data;
  dirty = false;
  q('#reviewOutput').classList.remove('hidden');
  q('#reviewMetrics').innerHTML = [
    ['Source checks',data.has_overrides ? 'Overrides recorded' : data.source_verified ? 'Matched' : data.admission_ready ? 'Corrections reviewed' : 'Needs correction',`${data.items.length} items; original checks retained below`],
    ['Pricing',currency(data.priced_total),`${data.unpriced_item_count} items missing prices`],
    ['Next step','Planner review','No AI proposal is automatically approved'],
  ].map(([label,value,note]) => `<article><small>${escapeText(label)}</small><strong>${escapeText(value)}</strong><span>${escapeText(note)}</span></article>`).join('');
  q('#reviewRows').innerHTML = data.items.map(item => `<tr>
    <td><strong>${escapeText(item.id)}</strong><small>${escapeText(item.source.sheet)} · row ${item.source.row}</small></td>
    <td>${escapeText(item.normalized_description)}<details><summary>View source evidence</summary>
      <small>${escapeText(item.source.file)} · ${escapeText(item.description_refs.join(', '))}</small>
      <blockquote>${escapeText(item.raw_description)}</blockquote>
      ${item.context.map(c => `<small>${escapeText(c.cell)}</small><blockquote>${escapeText(c.text)}</blockquote>`).join('')}
      <small>Proposal: ${escapeText(item.classification_reason)}</small></details></td>
    <td>${item.quantity === null ? 'Missing' : escapeText(item.quantity)} ${escapeText(item.unit)}<small>Source: ${escapeText(item.source_unit)}</small></td>
    <td>${escapeText(item.operation.replaceAll('_',' '))}<small>${escapeText(item.work_package)}</small></td>
    <td>${escapeText(currency(item.amount))}</td></tr>`).join('');
  const checkMarkup = (checks, editable) => checks.map(check => `<div class="finding">
    <span class="review-check-code">${escapeText(check.code)}</span><div><b>${escapeText(check.message)}</b>
    <p>${escapeText(check.guidance)}</p>
    <p>${check.origin === 'model' ? 'Reported by chat model. ' : 'Checked locally. '}Affects: ${escapeText(check.blocks.join(', ') || 'review only')}</p>
    ${editable && check.item_id ? `<button type="button" class="text-button" data-edit-item="${escapeText(check.item_id)}">Review ${escapeText(check.item_id)} values</button>`:''}</div></div>`).join('');
  q('#reviewChecks').innerHTML = data.calculation_checks.length ? checkMarkup(data.calculation_checks,true) :
    '<p>No unresolved calculation checks. Review descriptions, complete scope and project settings before running.</p>';
  q('#originalChecks').innerHTML = data.original_checks.length ? checkMarkup(data.original_checks,false) : '<p>Original extraction matched the checked source cells.</p>';
  editor = ReviewDecisions.render(q('#decisionEditor'),data,markDirty);
  q('#decisionState').textContent = `${data.decision_audit.length} decision(s) checked. ${data.has_overrides ? 'Planner overrides are recorded; original discrepancies remain in the audit.' : 'No planning override recorded.'} Saving produces a reusable draft, not approval.`;
  const project = data.document.project;
  if (initialize) {
    q('#approvalForm [name=name]').value = project.name || 'Reviewed BOQ project';
    q('#approvalForm [name=typology]').value = project.typology_proposal === 'unknown' ? '' : project.typology_proposal;
    q('#approvalForm [name=structure_count]').value = project.structure_count ?? '';
    q('#approvalForm [name=start_date]').value = project.start_date || '';
    for (const [key,value] of Object.entries(data.reviewed_document.review_settings)) {
      const input=q('#approvalForm').elements.namedItem(key);
      if (input) input.value=value;
    }
    q('#decisionReviewer').value=data.reviewed_document.review_settings.reviewer || data.decision_audit[0]?.reviewer || '';
  }
  admission();
}

function markDirty() {
  generation++;
  dirty=true;
  q('#importResult').classList.add('hidden');
  q('#approvalForm [name=review_confirmed]').checked=false;
  q('#approvalForm [name=scope_confirmed]').checked=false;
  q('#decisionState').textContent='Unsaved changes. Recheck to update the effective items and calculation checks.';
  admission();
}

function reviewPayload() {
  const saved=JSON.parse(JSON.stringify(report.reviewed_document));
  saved.decisions=editor.collect(q('#decisionReviewer').value);
  saved.review_settings={};
  for (const [key,value] of new FormData(q('#approvalForm'))) {
    if (!['review_confirmed','scope_confirmed','contract_confirmed'].includes(key)) saved.review_settings[key]=value;
  }
  const data=new FormData(q('#sourceForm'));
  data.set('normalized',new Blob([JSON.stringify(saved)],{type:'application/json'}),'reviewed_boq.json');
  return data;
}

async function send(endpoint, data) {
  const response = await fetch(endpoint,{method:'POST',body:data});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

q('#sourceForm').addEventListener('change', () => {
  generation++;
  report = null;
  dirty=false;
  editor=null;
  q('#reviewOutput').classList.add('hidden');
  q('#importResult').classList.add('hidden');
  q('#approvalForm').reset();
  q('#reviewStatus').textContent = 'Files changed. Check against originals again before reviewing.';
  showError('');
  admission();
});

q('#sourceForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  const current = ++generation;
  busy = true;
  q('#reviewButton').disabled = true;
  q('#reviewOutput').classList.add('hidden');
  q('#importResult').classList.add('hidden');
  q('#approvalForm').reset();
  showError('');
  q('#reviewStatus').textContent = 'Checking the original cells, item inventory and proposed operations…';
  try {
    const data = await send('/api/normalization/review',new FormData(event.target));
    if (current !== generation) return;
    renderReview(data);
    q('#reviewStatus').textContent = 'Review complete. Inspect the evidence and resolve the checks below.';
  } catch (error) { if (current === generation) { report = null; showError(error.message); q('#reviewStatus').textContent = 'The files could not be verified.'; } }
  finally { busy = false; q('#reviewButton').disabled = false; admission(); }
});

q('#reviewChecks').addEventListener('click',event=>{
  const button=event.target.closest('[data-edit-item]');
  if (button) editor?.openItem(button.dataset.editItem);
});
q('#decisionReviewer').addEventListener('input',()=>{
  q('#approvalForm [name=reviewer]').value=q('#decisionReviewer').value;
  q('#importResult').classList.add('hidden');
  generation++;
});
q('#useAllAI').addEventListener('click',()=>{
  if (busy || !editor) return;
  try {
    const count=editor.useAllAI(q('#decisionReviewer').value,q('#bulkReason').value,
      existing=>window.confirm(`Replace ${existing} existing field choice(s) with the original AI proposals and the shared optional reason? Evidence repairs will not change.`));
    if (count===null) return;
    showError('');
    q('#decisionState').textContent=`AI values selected for ${count} fields. Adjust individual choices if needed, then click Recheck changes.`;
  } catch(error) { showError(error.message); }
});
q('#approvalForm').addEventListener('input',()=>{
  generation++;
  q('#importResult').classList.add('hidden');
  admission();
});
q('#recheckDecisions').addEventListener('click',async()=>{
  if (!report || busy) return;
  let data;
  try { data=reviewPayload(); } catch(error) { showError(error.message); return; }
  const current=++generation;
  busy=true;
  admission();
  showError('');
  try {
    const result=await send('/api/normalization/review',data);
    if (generation!==current) return;
    renderReview(result,false);
    q('#reviewStatus').textContent='Changes rechecked. Inspect effective values, save the reviewed JSON, then confirm the model run.';
  } catch(error) { if(current===generation) { dirty=true; showError(error.message); } }
  finally { busy=false; admission(); }
});
q('#approvalForm').addEventListener('submit',async event => {
  event.preventDefault();
  if (busy || dirty || !report) return;
  const current = generation;
  let data;
  try { data=reviewPayload(); } catch(error) { showError(error.message); return; }
  for (const [key,value] of new FormData(event.target)) data.set(key,value);
  busy = true;
  admission();
  showError('');
  q('#importResult').classList.add('hidden');
  try {
    const result = await send('/api/normalization/analyze',data);
    if (current !== generation) return;
    const box = q('#importResult');
    box.innerHTML = `<h2>Reviewed draft generated</h2>
      ${result.normalization_review.has_overrides ? '<p class="override-warning"><strong>Planner-overridden inputs.</strong> This draft uses values or assumptions that differ from source evidence. Inspect Input Decisions in the Excel export.</p>' : ''}
      <p>${result.metrics.duration_working_days} working days · finishes ${escapeText(result.metrics.finish_date)} · BOQ value: ${escapeText(currency(result.metrics.boq_total))}</p>
      <p class="demo-note">Reviewed effective values were used after planner confirmation. This is a draft based on default productivity assumptions, not an approved baseline.</p>
      <a class="export-button" href="/api/jobs/${encodeURIComponent(result.job_id)}/export.xlsx">Download analysis Excel</a>
      <div class="table-wrap"><table><thead><tr><th>Activity</th><th>Days</th><th>Start</th><th>Finish</th></tr></thead><tbody>
      ${result.activities.map(a=>`<tr><td>${escapeText(a.name)}</td><td>${a.duration_days}</td><td>${escapeText(a.start_date)}</td><td>${escapeText(a.finish_date)}</td></tr>`).join('')}
      </tbody></table></div>
      <p>${result.cashflow.length ? 'Monthly cash curves are included in the Excel download.' : 'No cash forecast was generated for this schedule-only import.'}</p>
      ${result.findings.map(f=>`<p><strong>${escapeText(f.code)}</strong>: ${escapeText(f.message)}</p>`).join('')}`;
    box.classList.remove('hidden');
    box.scrollIntoView({block:'start'});
  } catch(error) { if(current===generation) showError(error.message); }
  finally { busy = false; admission(); }
});

q('#downloadReview').addEventListener('click',async()=>{
  if (!report || busy || dirty) return;
  const current=generation;
  try {
    const data=reviewPayload();
    busy=true;
    admission();
    const saved=await send('/api/normalization/save',data);
    if(current!==generation) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)],{type:'application/json'}));
    const link = document.createElement('a');
    link.href = url; link.download = 'buildflow-reviewed-boq.json'; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    q('#decisionState').textContent='Reviewed JSON downloaded. Keep the original Excel files with it; unresolved checks still need resolution before a run.';
  } catch(error) { if(current===generation) showError(error.message); }
  finally { busy=false; admission(); }
});
