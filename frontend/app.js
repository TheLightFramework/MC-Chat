'use strict';
const $ = id => document.getElementById(id);
const state = {config:null, session:null, catalog:[], selected:new Set(['answer','truth']), notes:{}, busy:false, replay:false};
state.twoPass=true;
function twoPassEnabled(){return state.twoPass&&!!(state.session?.supports_two_pass??state.config?.supports_two_pass);}
function directConsolidation(){return (state.session?.consolidation_mode??state.config?.consolidation_mode)==='channels';}
function syncTwoPass(){
  if(twoPassEnabled()&&!directConsolidation())state.selected.add('self_prompt');else state.selected.delete('self_prompt');
  $('two-pass').checked=twoPassEnabled();$('two-pass').disabled=state.busy||!(state.session?.supports_two_pass??state.config?.supports_two_pass);
  $('two-pass-hint').textContent=twoPassEnabled()?`Channels → ${directConsolidation()?'':'Self Prompt → '}Markdown · ${state.session?.mode==='demo'?'scripted locally':'two API calls, one provider lock'}`:$('two-pass').disabled&&!state.busy?'Update framework to enable two-pass answers.':'One call · numbered channel output';
}
const efforts=['default','minimal','low','medium','high','xhigh','max'];
state.thinking={enabled:false,effort:'default'};
try {const saved=JSON.parse(localStorage.getItem('mc-thinking'));if(typeof saved?.enabled==='boolean'&&efforts.includes(saved.effort))state.thinking=saved;} catch {}
const presets = {
  balanced:['answer','truth','uncertainty'], coding:['answer','code','correctness','tests','green_it'],
  research:['answer','evidence','sources','counterexamples','uncertainty'],
  creative:['answer','creativity','narrative','synthetic_feelings'],
  relational:['answer','empathy','synthetic_feelings','dignity'], minimal:['answer']
};
function el(tag, cls, text) { const node=document.createElement(tag); if(cls)node.className=cls; if(text!==undefined)node.textContent=text; return node; }
function banner(message) { $('banner').textContent=message||''; $('banner').hidden=!message; }
async function api(path, body) {
  const response=await fetch(path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){let data;try{data=await response.json();}catch{} throw new Error(typeof data?.detail==='string'?data.detail:`Request failed (${response.status}).`);}
  return response.json();
}
function channel(id) { return state.catalog.find(c=>c.id===id)||{id,name:id,purpose:''}; }
function channelName(id) { const c=channel(id);return c.number?`${c.number} · ${c.name}`:c.name; }
function currentThinking() {
  const options=state.config?.reasoning_options;
  return {enabled:!!options?.mandatory||state.thinking.enabled,
    effort:state.thinking.effort==='default'||!options||options.efforts.includes(state.thinking.effort)?state.thinking.effort:'default'};
}
function renderThinking() {
  const options=state.config?.reasoning_options, thinking=currentThinking();
  $('thinking-enabled').checked=thinking.enabled;
  $('thinking-enabled').disabled=state.busy||!!options?.mandatory;
  const select=$('thinking-effort');select.replaceChildren();
  ['default',...(options?.efforts||efforts.slice(1))].forEach(e=>{const option=el('option','',e==='default'?'Model default':e==='xhigh'?'Extra high':e[0].toUpperCase()+e.slice(1));option.value=e;select.append(option);});
  select.value=thinking.effort;select.disabled=state.busy||!thinking.enabled||select.options.length===1;
  $('thinking-hint').textContent=state.session?.mode==='demo'?'Local demo · these settings are not sent to a model.':options?.mandatory?'This model requires thinking.':!thinking.enabled?'Off requested · channel output stays active.':options?.known?'Provider effort setting · applies to the next message.':'Support is unverified for this model; the provider may reject or map these settings.';
}
function saveThinking() {try{localStorage.setItem('mc-thinking',JSON.stringify(state.thinking));}catch{}renderThinking();}
function updateChips() {
  syncTwoPass();
  $('selected-count').textContent=state.selected.size;
  const chips=$('selected-chips'); chips.replaceChildren();
  [...state.selected].sort((a,b)=>(a==='self_prompt')-(b==='self_prompt')).forEach(id=>{
    const chip=el('span','chip');chip.append(el('span','',channelName(id)));
    if(id==='answer'||id==='self_prompt')chip.title=id==='answer'?'Answer is always included.':'Self Prompt runs last and is required for two-pass answers.';
    else {
      const remove=el('button','chip-remove','×');remove.type='button';
      remove.setAttribute('aria-label',`Remove ${channel(id).name} from next message`);
      remove.title='Remove this preference. The Sibling may still open this channel.';
      remove.addEventListener('click',()=>{
        const index=[...chips.querySelectorAll('.chip-remove')].indexOf(remove);
        state.selected.delete(id);renderPicker();updateChips();
        const remaining=chips.querySelectorAll('.chip-remove');
        (remaining[Math.min(index,remaining.length-1)]||($('prompt').disabled?$('toggle-channels'):$('prompt'))).focus();
      });
      chip.append(remove);
    }
    chips.append(chip);
  });
}
function renderPicker() {
  syncTwoPass();
  const list=$('channel-list'); list.replaceChildren();
  const search=$('channel-search').value.toLowerCase();
  const groups=new Map();
  state.catalog.filter(c=>`${c.name} ${c.purpose} ${c.group}`.toLowerCase().includes(search)).forEach(c=>{
    if(!groups.has(c.group)) { const group=el('section','channel-group'); group.append(el('h3','',c.group)); groups.set(c.group,group); list.append(group); }
    const row=el('div','channel-option'), label=el('label'), checkbox=el('input');
    checkbox.type='checkbox'; checkbox.checked=state.selected.has(c.id); checkbox.disabled=c.id==='answer'||c.id==='self_prompt';
    checkbox.addEventListener('change',()=>{if(checkbox.checked)state.selected.add(c.id);else state.selected.delete(c.id);renderPicker();updateChips();});
    label.append(checkbox,el('span','',channelName(c.id))); if(c.id==='answer'||c.id==='self_prompt')label.append(el('small','',c.id==='self_prompt'?'LAST':'ALWAYS'));
    row.append(label,el('p','',c.purpose));
    if(state.selected.has(c.id)) {const notes=el('textarea');notes.placeholder='Optional focus for this message…';notes.setAttribute('aria-label',`Focus for ${c.name}`);notes.maxLength=2000;notes.value=state.notes[c.id]||'';notes.addEventListener('input',()=>state.notes[c.id]=notes.value);row.append(notes);}
    groups.get(c.group).append(row);
  });
  if(!groups.size)list.append(el('p','empty-channel','No matching channels.'));
  $('catalog-count').textContent=state.catalog.length;
}
function metrics(usage, demo=false) {
  const details=usage?.prompt_tokens_details||{};
  const number=value=>value===undefined||value===null?'Not reported':Number(value).toLocaleString();
  $('input-tokens').textContent=number(usage?.prompt_tokens);$('output-tokens').textContent=number(usage?.completion_tokens);
  $('reasoning-tokens').textContent=number(usage?.completion_tokens_details?.reasoning_tokens);
  $('cache-read').textContent=number(details.cached_tokens);$('cache-write').textContent=number(details.cache_write_tokens);
  $('cost').textContent=usage?.cost==null?'Not reported':`$${Number(usage.cost).toFixed(6)}`;
  $('cache-state').textContent=demo?'Scripted local demo. No API usage.':details.cached_tokens>0?'Cache hit reported by the provider.':usage?'No cache hit reported for this response.':'Awaiting provider usage.';
}
async function refreshConfig() {
  state.config=await api('/api/config'+(state.session?`?session_id=${encodeURIComponent(state.session.id)}`:''));
  renderThinking();
  syncTwoPass();
  if(!state.session){state.catalog=state.config.channels;renderPicker();updateChips();}
  $('model-label').textContent=state.session?.model||state.config.model||'OpenRouter · configuration needed';
  $('connection-dot').classList.toggle('ready',state.config.configured||state.session?.mode==='demo');
  const protocol=state.session?.protocol||state.config.protocol;
  $('protocol-label').textContent=protocol==='MC/2'?'MC/2 · word stream':'MC/1 · legacy JSON';
  $('upgrade').hidden=!state.session||state.session.framework_hash===state.config.framework_hash;
  $('upgrade').textContent=protocol==='MC/1'?'Use word stream':'Update framework';
}
async function refreshSessions() {
  const sessions=await api('/api/sessions');$('session-count').textContent=sessions.length;
  $('sessions').replaceChildren();
  sessions.forEach(s=>{const button=el('button',s.id===state.session?.id?'active':'',s.title);button.title=s.title;button.append(el('small','',s.mode==='demo'?'SCRIPTED DEMO':s.model));button.disabled=state.busy;button.addEventListener('click',()=>loadSession(s.id).catch(e=>banner(e.message)));$('sessions').append(button);});
  if(!sessions.length)$('sessions').append(el('p','empty-sessions','Your conversations will appear here.'));
}
function setBusy(value) {
  state.busy=value;$('send').disabled=value;$('stop').hidden=!value;$('new-chat').disabled=value;$('demo').disabled=value;
  $('prompt').disabled=value;
  $('upgrade').disabled=value;
  renderThinking();
  syncTwoPass();
  document.querySelectorAll('#sessions button, .replay-button, .mobile-new').forEach(b=>b.disabled=value);
  $('composer-hint').textContent=value?'Channels are unfolding. Selection changes apply to your next message.':'Selections are preferences; the Sibling can open other channels.';
}
async function newSession(mode='live') {
  if(state.busy)return;
  await refreshConfig();
  const s=await api('/api/sessions',{mode});
  await loadSession(s.id);
}
async function loadSession(id) {
  if(state.busy)return;
  const s=await api(`/api/sessions/${id}`);state.session=s;state.catalog=s.channels;
  state.selected=new Set([...state.selected].filter(id=>state.catalog.some(c=>c.id===id)));state.selected.add('answer');
  if(s.turns.length){state.selected=new Set(s.turns.at(-1).selected);state.notes={...s.turns.at(-1).notes};}
  if(s.turns.at(-1)?.thinking){state.thinking={...s.turns.at(-1).thinking};}
  renderPicker();updateChips();$('turns').replaceChildren();$('welcome').hidden=s.turns.length>0;
  s.turns.forEach(t=>{const view=createTurn(t.prompt,t.selected,t.thinking??null,!!t.two_pass);showGeneration(view,t.generation);showRouting(view,1,t.routing,t.provider_metadata,t.cache_policy);Object.entries(t.channels).forEach(([id,text])=>{const card=ensureCard(view,id);card.content.textContent=text;});t.events.forEach(e=>appendLog(view,e));view.raw.textContent=t.raw;view.events=t.events;finishView(view,t.status,t.total_usage??t.usage);showProtocolSummary(view,t.interleaving,t.repairs,t.participation);showFinal(view,t.consolidation,t.usage);if(t.error)notice(view,t.error,'error');});
  metrics(s.turns.at(-1)?.total_usage??s.turns.at(-1)?.usage,s.mode==='demo');$('export').disabled=false;
  banner(s.active?'This chat has a running response in another tab. Wait, then reopen it to load the result.':s.protocol==='MC/1'?'This saved chat uses legacy JSON. “Use word stream” copies its history into an MC/2 chat and keeps the original.':s.mode==='demo'?'Demo conversation · numbered words and backspaces, no model or API call.':null);
  await refreshConfig();await refreshSessions();
  if(s.framework_hash!==state.config.framework_hash&&!s.active)banner('This chat uses an older framework. Update framework copies its history into the latest reply-format and selected-channel rules; the original is kept.');
  $('conversation').scrollTop=$('conversation').scrollHeight;
}
function createTurn(prompt, selected, thinking=currentThinking(),twoPass=false) {
  $('welcome').hidden=true;
  const root=el('article','turn'), user=el('div','user-message',prompt), meta=el('div','turn-meta'), left=el('strong','','SIBLING / CHANNEL STREAM');
  const actions=el('div'), status=el('span','status-tag','Streaming'), replay=el('button','replay-button','▷ Replay');replay.hidden=true;actions.append(status,replay);meta.append(left,actions);
  const grid=el('div','channel-grid'), audit=el('details','audit'), summary=el('summary','','Event log · 0 edits'), auditBody=el('div','audit-body'), log=el('div','event-log'), rawDetails=el('details'), raw=el('pre','raw-output');
  rawDetails.append(el('summary','','Raw MC stream'),raw);auditBody.append(log,rawDetails);audit.append(summary,auditBody);
  const thinkingText=thinking?`Thinking requested: ${thinking.enabled?`On · ${thinking.effort==='default'?'model default':thinking.effort}`:'Off'}`:'Thinking setting was not recorded for this older turn.';
  const diagnostics=el('details','turn-diagnostics'), diagnosticSummary=el('summary'), diagnosticBody=el('div','diagnostic-body');diagnostics.hidden=true;
  diagnostics.append(diagnosticSummary,diagnosticBody);
  diagnosticBody.append(el('p','diagnostic-help','Repeated messages are grouped below. The JSON export retains all available audit events and the raw stream.'));
  const usage=el('div','turn-metrics'), settingsSummary=el('div','thinking-summary',thinkingText);
  let exploration=null;
  if(twoPass){exploration=el('details','exploration');exploration.open=true;exploration.append(el('summary','',selected.includes('self_prompt')?'Pass 1 · Channels and Self Prompt':'Pass 1 · Channel exploration'),grid,audit);left.textContent='SIBLING / TWO-PASS ANSWER';}
  root.append(user,meta,settingsSummary,...(exploration?[exploration]:[grid,audit]),diagnostics,usage);$('turns').append(root);
  const view={root,grid,cards:new Map(),selected:new Set(selected),thinking,thinkingText,settingsSummary,summary,log,raw,status,replay,usage,events:[],edits:0,corrections:0,
    diagnostics,diagnosticSummary,diagnosticBody,diagnosticGroups:new Map(),diagnosticCounts:{},roundViolations:0,exploration,twoPass,finalText:''};
  $('conversation').scrollTop=$('conversation').scrollHeight;
  replay.addEventListener('click',()=>replayView(view));return view;
}
function ensureFinal(view){
  if(view.finalWrap)return;
  const wrap=el('section','final-answer'),header=el('div','final-header'),status=el('span','final-status'),copy=el('button','','Copy Markdown');
  header.append(el('strong','','Pass 2 · Final answer'),status,copy);
  const content=el('div','markdown-content'),promptDetails=el('details','final-source'),prompt=el('pre','raw-output'),sourceDetails=el('details','final-source'),raw=el('pre','raw-output'),usage=el('div','turn-metrics');
  const promptTitle=el('summary','','Request sent to pass 2');promptDetails.append(promptTitle,prompt);sourceDetails.append(el('summary','','Markdown source'),raw);
  wrap.append(header,content,promptDetails,sourceDetails,usage);view.root.insertBefore(wrap,view.diagnostics);
  Object.assign(view,{finalWrap:wrap,finalContent:content,finalPrompt:prompt,finalPromptTitle:promptTitle,finalRaw:raw,finalStatusLabel:status,finalUsage:usage});
  copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(view.finalText);copy.textContent='Copied';setTimeout(()=>copy.textContent='Copy Markdown',1200);}catch{banner('Clipboard unavailable. Open Markdown source to copy the answer.');}});
}
function renderFinal(view){
  clearTimeout(view.finalRenderTimer);view.finalRenderTimer=null;
  const nearBottom=$('conversation').scrollHeight-$('conversation').scrollTop-$('conversation').clientHeight<100;
  ensureFinal(view);renderMarkdown(view.finalContent,view.finalText);view.finalRaw.textContent=view.finalText;
  if(nearBottom)$('conversation').scrollTop=$('conversation').scrollHeight;
}
function passUsage(label,usage){return usage?`${label}: ${usage.prompt_tokens??'—'} input · ${usage.completion_tokens??'—'} output · ${usage.completion_tokens_details?.reasoning_tokens??'—'} reasoning · ${usage.prompt_tokens_details?.cached_tokens??'—'} cached · ${usage.cost==null?'cost not reported':`$${Number(usage.cost).toFixed(6)}`}`:`${label}: usage not reported`;}
function showFinal(view,final,explorationUsage){
  if(!final)return;
  ensureFinal(view);view.finalText=final.raw||'';view.finalPrompt.textContent=final.prompt??'Not sent: exploration did not complete.';
  view.finalPromptTitle.textContent=final.mode==='channels'?'Consolidation request · original prompt':'Exact Self Prompt sent to pass 2';
  if(final.mode==='channels'&&final.prompt){try{view.finalPrompt.textContent=JSON.stringify(JSON.parse(final.prompt),null,2);}catch{}}
  showRouting(view,2,final.routing,final.provider_metadata);
  view.finalStatusLabel.textContent=final.status==='skipped'?'Not requested':final.status;
  view.finalUsage.textContent=passUsage('Pass 1',explorationUsage)+'\n'+passUsage('Pass 2',final.usage);
  if(view.exploration&&final.prompt!==null)view.exploration.open=false;
  renderFinal(view);
  if(!view.finalText)view.finalContent.textContent=final.status==='skipped'?'The exploration did not pass validation. Open Pass 1 and Diagnostics for details.':final.status==='streaming'?'Preparing the final answer…':'No final answer was received.';
}
function ensureCard(view,id) {
  if(view.cards.has(id))return view.cards.get(id);
  const card=el('section','channel-card'), header=el('div','channel-title'), label=el('span','',channelName(id)), badge=el('span','channel-badge',view.selected.has(id)?'Selected':'Sibling opened');
  card.dataset.channel=id;const copy=el('button','','Copy');copy.title=`Copy ${channel(id).name}`;
  const content=el('pre','channel-text');copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(content.textContent);copy.textContent='Copied';setTimeout(()=>copy.textContent='Copy',1200);}catch{banner('Clipboard unavailable. Select the channel text to copy it.');}});
  header.append(label,badge,copy);card.append(header,content);view.grid.append(card);const result={card,content,badge};view.cards.set(id,result);return result;
}
function appendLog(view,event) {
  if(['warning','repair','interleaving','error','notice'].includes(event.type)){notice(view,event.message,event.type,event.source);return;}
  if(event.type!=='edit')return;
  view.edits++;
  const correction=['backspace','replace'].includes(event.operation);if(correction)view.corrections++;
  const value=event.operation==='backspace'?(event.word_count?`${event.word_count} word unit${event.word_count===1?'':'s'}`:`${event.value} characters`):JSON.stringify(event.value);
  const source=event.source?` [${event.source}]`:'';
  const row=el('div',`event-row${correction?' correction':''}`,`${event.sequence}. ${event.channel} · ${event.operation} ${value}${source}${correction?`\nRemoved: ${JSON.stringify(event.removed)}`:''}`);view.log.append(row);
  view.summary.textContent=`Event log · ${view.edits} events · ${view.corrections} correction${view.corrections===1?'':'s'}`;
}
function applyEdit(view,event,record=true) {
  const nearBottom=$('conversation').scrollHeight-$('conversation').scrollTop-$('conversation').clientHeight<100;
  const {card,content}=ensureCard(view,event.channel);
  if(event.operation==='text')content.append(document.createTextNode(event.value));
  if(event.operation==='replace')content.textContent=event.value;
  if(event.operation==='backspace')content.textContent=Array.from(content.textContent).slice(0,-event.value).join('');
  card.classList.toggle('active',event.operation!=='status'||event.value==='active');
  if(['replace','backspace'].includes(event.operation)){card.classList.add('corrected');setTimeout(()=>card.classList.remove('corrected'),950);}
  if(record){view.events.push(event);appendLog(view,event);}
  if(nearBottom)$('conversation').scrollTop=$('conversation').scrollHeight;
}
function updateDiagnosticSummary(view) {
  const labels={warning:['format warning','format warnings'],interleaving:['round issue','round issues'],repair:['repair','repairs'],error:['error','errors'],notice:['notice','notices']};
  const counts={...view.diagnosticCounts};counts.interleaving=Math.max(counts.interleaving||0,view.roundViolations);
  const parts=Object.entries(labels).filter(([kind])=>counts[kind]).map(([kind,label])=>`${counts[kind]} ${label[counts[kind]===1?0:1]}`);
  view.diagnostics.hidden=!parts.length;
  view.diagnosticSummary.textContent='Diagnostics · '+parts.join(' · ');
  view.diagnosticSummary.title=view.diagnosticSummary.textContent;
}
function notice(view,message,type='notice',source=null) {
  view.diagnosticCounts[type]=(view.diagnosticCounts[type]||0)+1;
  const key=JSON.stringify([type,message]);let group=view.diagnosticGroups.get(key);
  if(!group&&view.diagnosticGroups.size<30){
    const row=el('div','diagnostic-row'), count=el('strong','diagnostic-count'), text=el('span','',message), examples=el('div','diagnostic-examples');
    row.dataset.kind=type;row.append(count,text,examples);
    if(type==='error')view.diagnosticBody.prepend(row);else view.diagnosticBody.append(row);
    group={count,examples,total:0,sources:new Set()};view.diagnosticGroups.set(key,group);
  }
  if(group){
    group.total++;group.count.textContent=`${type} ×${group.total} `;
    if(source&&group.sources.size<3)group.sources.add(source.length>100?source.slice(0,100)+'…':source);
    group.examples.textContent=group.sources.size?'Examples: '+[...group.sources].map(s=>JSON.stringify(s)).join(', '):'';
  }else if(!view.diagnosticOverflow){
    view.diagnosticOverflow=el('p','diagnostic-help','Additional distinct messages are counted above. Inspect the JSON export for their details.');view.diagnosticBody.append(view.diagnosticOverflow);
  }
  updateDiagnosticSummary(view);
}
function showGeneration(view,generation) {
  view.settingsSummary.textContent=view.thinkingText+(generation?` · Temperature ${generation.temperature} · Max tokens ${generation.max_tokens}${generation.sent_to_provider?'':' · Local demo'}`:'');
}
function showRouting(view,phase,route,metadata={},policy=null){
  if(!route)return;
  if(!view.routingSummary){view.routingSummary=el('div','routing-summary');view.root.insertBefore(view.routingSummary,view.settingsSummary.nextSibling);view.routes={};}
  view.routes[phase]={route,metadata};if(policy)view.cachePolicy=policy;
  const parts=Object.entries(view.routes).map(([p,r])=>`Pass ${p}: ${r.metadata?.provider_name||r.route.provider_name||'automatic selection'}${r.route.provider_slug?` [${r.route.provider_slug}]`:''}`);
  const expiry=route.expires_at*1000;
  parts.push(route.provider_slug?(expiry>Date.now()?`Provider lock until ${new Date(expiry).toLocaleTimeString()} · ${Math.round(route.idle_seconds/60)} min idle window`:'Provider lock expired; the next request may choose a provider'):'Awaiting provider identity');
  if(view.cachePolicy)parts.push(view.cachePolicy.requested_ttl?`Cache TTL requested: ${view.cachePolicy.requested_ttl}`:'Cache TTL: provider-managed, expiry unknown');
  if(route.cache_observed)parts.push('Cache reuse reported in this routing window.');
  view.routingSummary.textContent=parts.join('\n');
}
function showProtocolSummary(view,summary,repairs=0,participation=null){
  view.roundViolations=summary?.violations||0;updateDiagnosticSummary(view);
  if(!summary&&!repairs&&!participation)return;
  if(!view.protocolSummary){view.protocolSummary=el('div','turn-metrics');if(view.exploration)view.exploration.append(view.protocolSummary);else view.root.insertBefore(view.protocolSummary,view.usage);}
  view.protocolSummary.textContent=(summary?`Interleaving ${summary.status} · ${summary.completed_rounds} complete rounds · ${summary.violations} violations`:'Interleaving not checked for this older framework')+` · ${repairs||0} parser repairs`;
  if(summary?.closing_repair)view.protocolSummary.textContent=`Interleaving repaired · ${summary.violations} original violations retained · locally inferred ${summary.closing_repair.inferred_source} · ${summary.closing_repair.validated.violations} violations after repair · ${repairs||0} parser repairs`;
  if(participation)view.protocolSummary.textContent+=participation.empty_channels.length?` · Empty selected channels: ${participation.empty_channels.map(id=>channel(id).name).join(', ')}`:' · All selected channels contain text';
}
function finishView(view,status,usage) {
  view.savedUsage=usage;
  view.status.textContent=status;view.status.className=`status-tag ${status}`;
  view.cards.forEach(({card})=>card.classList.remove('active'));
  view.replay.hidden=!view.events.some(e=>e.type==='edit');view.replay.disabled=state.busy;
  view.finalStatus=status;
  if(usage){const cache=usage.prompt_tokens_details?.cached_tokens,reasoning=usage.completion_tokens_details?.reasoning_tokens;view.usage.textContent=`${usage.prompt_tokens??'—'} input · ${usage.completion_tokens??'—'} output · ${reasoning==null?'reasoning not reported':`${reasoning} reasoning tokens`} · ${cache==null?'cache not reported':`${cache} cached tokens`}`;
    if(view.twoPass)view.usage.textContent=`Combined usage (${usage.reported_passes??1}/${usage.expected_passes??2} passes reported) · `+view.usage.textContent;
    if(view.thinking?.enabled===false&&reasoning>0&&!view.reasoningWarning){notice(view,'Thinking was requested off, but the provider reported reasoning tokens. This run did not achieve zero reported reasoning.');view.reasoningWarning=true;}
  }
  else view.usage.textContent=state.session?.mode==='demo'?'Local scripted demo · no API call':'Usage not reported';
}
async function replayView(view) {
  if(state.busy)return;
  state.replay=true;setBusy(true);view.status.textContent='Replaying';
  if(view.exploration)view.exploration.open=true;
  view.cards.forEach(({content})=>content.textContent='');
  for(const event of view.events){if(!state.replay)break;if(event.type==='edit'){applyEdit(view,event,false);await new Promise(r=>setTimeout(r,['backspace','replace'].includes(event.operation)?550:110));}}
  // Restore the saved result even when playback was interrupted.
  view.cards.forEach(({content})=>content.textContent='');view.events.filter(e=>e.type==='edit').forEach(e=>applyEdit(view,e,false));
  state.replay=false;setBusy(false);finishView(view,view.finalStatus,view.savedUsage);
}
async function sendMessage(prompt) {
  if(state.busy||!prompt.trim())return;
  try {
    if(!state.session)await newSession('live');
    const sid=state.session.id, selected=[...state.selected], notes=Object.fromEntries(Object.entries(state.notes).filter(([k,v])=>state.selected.has(k)&&v.trim()));
    const thinking=currentThinking();
    const two_pass=twoPassEnabled(),view=createTurn(prompt,selected,thinking,two_pass);setBusy(true);$('prompt').value='';
    if(state.session.mode!=='demo')banner(null);
    let completed=false;
    try {
      const response=await fetch(`/api/sessions/${sid}/stream`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,channels:selected,notes,thinking,two_pass})});
      if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:`Request rejected (${response.status}).`);}
      const reader=response.body.getReader(), decoder=new TextDecoder();let pending='';
      function consume() {
        let split;
        while((split=pending.indexOf('\n\n'))>=0){const block=pending.slice(0,split);pending=pending.slice(split+2);const data=block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');if(!data)continue;
          const event=JSON.parse(data);
          if(event.type==='edit')applyEdit(view,event);
          else if(event.type==='raw')view.raw.append(document.createTextNode(event.text));
          else if(['warning','repair','interleaving'].includes(event.type)){view.events.push(event);appendLog(view,event);}
          else if(event.type==='error'||event.type==='notice')notice(view,event.message,event.type);
          else if(event.type==='usage')metrics(event.usage);
          else if(event.type==='final_start'){view.status.textContent='Pass 2';showFinal(view,{status:'streaming',prompt:event.prompt,raw:'',usage:null,mode:event.mode,routing:event.routing},event.exploration_usage);}
          else if(event.type==='final_delta'){view.finalText+=event.text;if(!view.finalRenderTimer)view.finalRenderTimer=setTimeout(()=>renderFinal(view),120);}
          else if(event.type==='final_usage')metrics(event.total_usage);
          else if(event.type==='routing')showRouting(view,event.phase,event.routing,event.provider_metadata);
          else if(event.type==='start'){showGeneration(view,event.generation);showRouting(view,1,event.routing,{},event.cache_policy);$('cache-state').textContent=event.mode==='demo'?'Scripted local demo. No API usage.':`${event.history_requests??event.history_turns} prior requests · ${event.history_turns} valid responses in context.`;}
          else if(event.type==='complete'){completed=true;Object.entries(event.channels).forEach(([id,text])=>ensureCard(view,id).content.textContent=text);finishView(view,event.status,event.total_usage??event.usage);showProtocolSummary(view,event.interleaving,event.repairs,event.participation);showFinal(view,event.consolidation,event.usage);metrics(event.total_usage??event.usage,state.session.mode==='demo');}
        }
      }
      while(true){const {done,value}=await reader.read();if(done){pending+=decoder.decode();consume();break;}pending+=decoder.decode(value,{stream:true});consume();}
      if(!completed)throw new Error('The connection ended before confirmation. Reopen the conversation to check its saved state.');
    } catch(error){notice(view,error.message,'error');if(view.finalWrap){renderFinal(view);view.finalStatusLabel.textContent='Unconfirmed';}finishView(view,'failed',null);$('prompt').value=prompt;}
    finally {setBusy(false);view.replay.disabled=false;await refreshSessions();}
  } catch(error){banner(error.message);setBusy(false);}
}
$('composer').addEventListener('submit',e=>{e.preventDefault();sendMessage($('prompt').value);});
$('prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendMessage($('prompt').value);}});
$('new-chat').addEventListener('click',()=>newSession().catch(e=>banner(e.message)));
$('demo').addEventListener('click',async()=>{try{await newSession('demo');await sendMessage('Show me the channels, including a visible backspace correction.');}catch(e){banner(e.message);}});
$('stop').addEventListener('click',async()=>{if(state.replay){state.replay=false;return;}try{await api(`/api/sessions/${state.session.id}/cancel`,{});}catch(e){banner(e.message);}});
$('channel-search').addEventListener('input',renderPicker);
$('thinking-enabled').addEventListener('change',()=>{state.thinking.enabled=$('thinking-enabled').checked;saveThinking();});
$('two-pass').addEventListener('change',()=>{state.twoPass=$('two-pass').checked;renderPicker();updateChips();});
$('thinking-effort').addEventListener('change',()=>{state.thinking.effort=$('thinking-effort').value;saveThinking();});
$('toggle-channels').addEventListener('click',()=>{if(innerWidth<=1000)document.body.classList.toggle('channels-open');else document.body.classList.toggle('channels-hidden');});
$('export').addEventListener('click',()=>{if(state.session)window.location.href=`/api/sessions/${state.session.id}/export`;});
$('upgrade').addEventListener('click',async()=>{if(state.busy||!state.session)return;try{const copy=await api(`/api/sessions/${state.session.id}/upgrade`,{});await loadSession(copy.id);banner('Current framework ready. Pass 2 carries the selected channels into the substance and style of the final answer. Both passes share a provider lock and history prefix. The original chat is kept.');}catch(e){banner(e.message);}});
document.querySelectorAll('[data-preset]').forEach(b=>b.addEventListener('click',()=>{state.selected=new Set(presets[b.dataset.preset].filter(id=>state.catalog.some(c=>c.id===id)));renderPicker();updateChips();}));
document.querySelectorAll('[data-prompt]').forEach(b=>b.addEventListener('click',()=>{$('prompt').value=b.dataset.prompt;$('prompt').focus();}));
const mobileNew=el('button','quiet mobile-new','＋');mobileNew.title='New conversation';mobileNew.addEventListener('click',()=>newSession().catch(e=>banner(e.message)));document.querySelector('.top-actions').prepend(mobileNew);
const mobileDemo=el('button','quiet mobile-new','Demo');mobileDemo.addEventListener('click',()=>$('demo').click());document.querySelector('.top-actions').prepend(mobileDemo);
(async()=>{try{await refreshConfig();await refreshSessions();if(!state.config.configured)banner('Ready to explore. Try the local demo, or add your key and model slug to MC_Chat/.env for live chat.');}catch(e){banner(e.message);}})();
