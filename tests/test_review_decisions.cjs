const {test}=require('node:test');
const assert=require('node:assert/strict');
const {makeDecision,makeBulkAI}=require('../static/review-decisions.js');
const option={item_id:'I3',field:'quantity',source_value:12,proposed_value:999,actions:['source','ai','manual']};

test('source and AI choices use their respective values rather than manual text',()=>{
  assert.equal(makeDecision(option,{action:'source',text:'777',reason:'',reviewer:'Planner'}).value,12);
  assert.equal(makeDecision(option,{action:'ai',text:'777',reason:'Confirmed scope',reviewer:'Planner'}).value,999);
});
test('manual zero is distinct from missing, and invalid numeric input is rejected',()=>{
  const input={action:'manual',reason:'Confirmed scope',reviewer:'Planner'};
  assert.equal(makeDecision(option,{...input,text:'0'}).value,0);
  assert.equal(makeDecision(option,{...input,text:''}).value,null);
  for(const text of ['NaN','Infinity','10kg','1e999']) assert.throws(()=>makeDecision(option,{...input,text}));
});
test('reasons are optional but reviewer remains required; undo removes the decision',()=>{
  assert.equal(makeDecision(option,{action:'ai',reason:'',reviewer:'Planner'}).reason,'Not provided');
  assert.throws(()=>makeDecision(option,{action:'source',reason:'',reviewer:''}));
  assert.equal(makeDecision(option,{action:'',text:'24'}),null);
});
test('bulk AI uses every eligible proposal, skips evidence repairs and retains missing values',()=>{
  const rows=[option,{...option,item_id:'I4',proposed_value:null},
    {...option,field:'source_evidence',actions:['source'],source_value:{quantity_ref:'D3'}}];
  const result=makeBulkAI(rows,'Planner','');
  assert.equal(result.length,2);
  assert.deepEqual(result.map(d=>d.value),[999,null]);
  assert.ok(result.every(d=>d.action==='ai' && d.reason==='Not provided' && d.reviewer==='Planner'));
  assert.throws(()=>makeBulkAI(rows,'',''));
});
test('unchanged saved decisions retain reviewer and timestamp, changed ones need new review',()=>{
  const previous={item_id:'I3',field:'quantity',action:'manual',value:24,reason:'Checked',reviewer:'Original planner',recorded_at:'2026-09-21T01:00:00+00:00'};
  assert.deepEqual(makeDecision(option,{action:'manual',text:'24',reason:'Checked',reviewer:'New planner'},previous),previous);
  const changed=makeDecision(option,{action:'manual',text:'25',reason:'Checked',reviewer:'New planner'},previous);
  assert.equal(changed.reviewer,'New planner');
  assert.equal(changed.recorded_at,'');
});
