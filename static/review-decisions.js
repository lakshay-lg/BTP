/* Field decisions are proposals to the server; this UI never grants admission. */
(function(root) {
  const numeric = new Set(['quantity','rate','amount']);
  const labels = {quantity:'Quantity',unit:'Unit',rate:'Rate',amount:'Amount',normalized_description:'Description',work_package:'Work package',operation:'Operation',source_evidence:'Source evidence'};
  const units=['m3','m2','kg','m','no','h','t','day','ls','ft2','point','unknown'];
  const key = (item,field) => JSON.stringify([item,field]);
  const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const display = value => value === null ? 'Unavailable' : typeof value === 'object' ? JSON.stringify(value,null,2) : String(value);

  function makeDecision(option,input,previous) {
    if (!input.action) return null;
    if (!option.actions.includes(input.action)) throw new Error('That decision is not available for this field.');
    let value;
    if (input.action==='source') value=option.source_value;
    else if (input.action==='ai') value=option.proposed_value;
    else if (numeric.has(option.field)) {
      value=input.text.trim()==='' ? null : Number(input.text);
      if (value!==null && (!Number.isFinite(value) || Math.abs(value)>1e12)) throw new Error(`${option.item_id}: enter a finite numeric ${option.field}, or leave it blank for missing.`);
    } else value=input.text;
    const reason=(input.reason || '').trim() || 'Not provided';
    if (previous && previous.action===input.action && JSON.stringify(previous.value)===JSON.stringify(value) && (previous.reason?.trim() || 'Not provided')===reason) return {...previous,reason};
    const reviewer=(input.reviewer || '').trim();
    if (!reviewer) throw new Error('Enter a reviewer name before rechecking decisions.');
    return {item_id:option.item_id,field:option.field,action:input.action,value,reason,reviewer,recorded_at:''};
  }

  function makeBulkAI(options,reviewer,reason) {
    return options.filter(o=>o.actions.includes('ai')).map(o=>makeDecision(o,{action:'ai',reviewer,reason}));
  }

  function render(container,report,onChange) {
    const options=report.resolution_options;
    const previous=new Map(report.reviewed_document.decisions.map(d=>[key(d.item_id,d.field),d]));
    const audits=new Map(report.decision_audit.map(d=>[key(d.item_id,d.field),d]));
    const groups=new Map();
    options.forEach((o,index)=>{
      if (!groups.has(o.item_id)) groups.set(o.item_id,[]);
      groups.get(o.item_id).push({o,index});
    });
    container.innerHTML=[...groups].map(([id,fields],groupIndex)=>{
      const item=report.items.find(i=>i.id===id);
      const flagged=report.calculation_checks.some(c=>c.item_id===id);
      return `<details class="decision-item" id="decision-item-${groupIndex}" ${flagged?'open':''}>
        <summary><strong>${escape(id)}</strong> ${escape(item.normalized_description)} <span>${flagged?'Needs review':'Edit reviewed values'}</span></summary>
        ${fields.map(({o,index})=>{
          const d=previous.get(key(id,o.field));
          const audit=audits.get(key(id,o.field));
          const product=report.calculation_checks.find(c=>c.item_id===id && c.code==='EFFECTIVE_AMOUNT_CONFLICT');
          const label=escape(`${id} ${labels[o.field]}`);
          const manual=o.field==='unit'
            ? `<select data-manual aria-label="${label} manual value">${units.map(u=>`<option value="${u}">${u}</option>`).join('')}</select>`
            : `<input data-manual aria-label="${label} manual value" ${numeric.has(o.field)?'inputmode="decimal"':''} placeholder="${numeric.has(o.field)?'Blank means missing':'Enter reviewed value'}">`;
          return `<section class="decision-field" data-index="${index}">
            <h4>${escape(labels[o.field])}</h4>
            <div class="decision-evidence"><div><small>${escape(o.suggestion_label)} ${escape(o.source_ref || '')}</small><pre>${escape(o.source_available?display(o.source_value):'No unambiguous source value')}</pre></div>
            <div><small>Original AI proposal</small><pre>${escape(display(o.proposed_value))}</pre></div></div>
            <div class="decision-controls"><label>Decision<select data-action aria-label="${label} decision"><option value="">No decision / undo</option>${o.actions.map(a=>`<option value="${a}">${{source:o.field==='source_evidence'?'Repair evidence from Excel':'Use Excel',ai:'Accept AI value',manual:'Enter manually'}[a]}</option>`).join('')}</select></label>
            <label data-manual-label hidden>Manual value${manual}</label>
            <label>Reason (optional)<input data-reason aria-label="${label} reason" maxlength="30000" placeholder="Optional note"></label></div>
            ${o.field==='amount' && product?`<p class="demo-note">Calculated from chosen quantity × rate: <strong>${escape(product.suggested_amount)}</strong>. Enter this as a manual amount, or explain a different amount.</p>`:''}
            <p class="decision-status">${audit?`${escape(audit.status.replaceAll('_',' '))} · effective: ${escape(display(audit.effective_value))} · ${escape(audit.reviewer)}`:'No decision recorded; original proposal is unchanged.'}</p>
          </section>`;
        }).join('')}</details>`;
    }).join('') || '<p>No safe field-level editor is available. Follow the source repair guidance above.</p>';
    container.querySelectorAll('.decision-field').forEach(row=>{
      const option=options[Number(row.dataset.index)];
      const d=previous.get(key(option.item_id,option.field));
      row.querySelector('[data-action]').value=d?.action || '';
      row.querySelector('[data-reason]').value=d?.reason==='Not provided'?'':d?.reason || '';
      row.querySelector('[data-manual]').value=d?.value===null?'':typeof d?.value==='object'?'':String(d?.value ?? option.proposed_value ?? '');
      row.querySelector('[data-manual-label]').hidden=d?.action!=='manual';
    });
    container.oninput=event=>{
      const row=event.target.closest('.decision-field');
      if (!row) return;
      row.querySelector('[data-manual-label]').hidden=row.querySelector('[data-action]').value!=='manual';
      row.querySelector('.decision-status').textContent='Changed — recheck before saving or running.';
      onChange();
    };
    return {
      useAllAI(reviewer,reason,confirmReplace) {
        const decisions=makeBulkAI(options,reviewer,reason);
        const eligible=[...container.querySelectorAll('.decision-field')].filter(row=>options[Number(row.dataset.index)].actions.includes('ai'));
        const existing=eligible.filter(row=>row.querySelector('[data-action]').value!=='').length;
        if (existing && !confirmReplace(existing)) return null;
        eligible.forEach(row=>{
          row.querySelector('[data-action]').value='ai';
          row.querySelector('[data-reason]').value=(reason || '').trim();
          row.querySelector('[data-manual-label]').hidden=true;
          row.querySelector('.decision-status').textContent='AI value selected in bulk — recheck before saving or running.';
        });
        onChange();
        return decisions.length;
      },
      collect(reviewer) {
        return [...container.querySelectorAll('.decision-field')].map(row=>{
          const o=options[Number(row.dataset.index)];
          return makeDecision(o,{action:row.querySelector('[data-action]').value,text:row.querySelector('[data-manual]').value,
            reason:row.querySelector('[data-reason]').value,reviewer},previous.get(key(o.item_id,o.field)));
        }).filter(Boolean);
      },
      openItem(id) {
        const index=[...groups.keys()].indexOf(id);
        const el=document.getElementById(`decision-item-${index}`);
        if (el) { el.open=true; el.scrollIntoView({block:'start'}); }
      }
    };
  }
  const api={makeDecision,makeBulkAI,render};
  if(typeof module!=='undefined' && module.exports) module.exports=api;
  else root.ReviewDecisions=api;
})(typeof window==='undefined'?globalThis:window);
