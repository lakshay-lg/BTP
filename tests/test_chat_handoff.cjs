const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup(writeText) {
  const elements = {};
  for (const id of ['normalizerPrompt','promptStatus','promptDetails','copyPrompt']) {
    elements[id] = {value:'Complete source-preserving prompt',textContent:'',open:false,
      addEventListener(event, fn) { this[event] = fn; },
      focus() { this.focused = true; }, select() { this.selected = true; }};
  }
  const provider = {dataset:{provider:'Claude'},addEventListener(event,fn) {this[event]=fn;}};
  vm.runInNewContext(fs.readFileSync('static/chat-handoff.js','utf8'), {
    document:{getElementById:id=>elements[id],querySelectorAll:()=>[provider]},
    navigator:writeText ? {clipboard:{writeText}} : {},
  });
  return {elements,provider};
}

test('provider click copies the full prompt and explains the manual attachment step',async()=>{
  let copied;
  const {elements,provider} = setup(async text=>{copied=text;});
  await provider.click();
  assert.equal(copied,'Complete source-preserving prompt');
  assert.match(elements.promptStatus.textContent,/paste/i);
  assert.match(elements.promptStatus.textContent,/attach/i);
});

for (const [name,writeText] of [['denied',async()=>{throw new Error('Permission denied');}],['unavailable',null]]) {
  test(`clipboard ${name} exposes a selectable manual-copy fallback`,async()=>{
    const {elements} = setup(writeText);
    await elements.copyPrompt.click();
    assert.equal(elements.promptDetails.open,true);
    assert.equal(elements.normalizerPrompt.selected,true);
    assert.match(elements.promptStatus.textContent,/copy/i);
    assert.doesNotMatch(elements.promptStatus.textContent,/prompt copied/i);
  });
}
