/* Local-only extension. The Python backend owns all OpenAI credentials. */
let localConfig = null;
let codexStatus = null;
let codexRunning = false;
let localGeneration = 0;
const baseCloseModal = closeModal;
const baseImportModal = importModal;
const baseGenerateDraft = generateDraft;
const baseAddDraft = addDraft;
const baseAboutModal = aboutModal;

async function getLocalConfig() {
  const response = await fetch('/api/config', {cache:'no-store'});
  if (!response.ok) throw Error('Start the local Python server, then open its localhost URL.');
  const data = await response.json();
  if (!data.session_token) throw Error('The local AI backend is unavailable.');
  localConfig = data;
  return data;
}

async function getCodexStatus() {
  const response = await fetch('/api/codex', {cache:'no-store'});
  if (!response.ok) throw Error('The local AI backend is unavailable.');
  codexStatus = await response.json();
  return codexStatus;
}

function aiProvider() {
  const mode = document.getElementById('generation-mode')?.value;
  return mode==='codex' ? 'codex' : mode==='ai' ? 'openai' : null;
}

function syncAiMode() {
  const provider=aiProvider(), connection=document.getElementById('ai-connection'), error=document.getElementById('import-error'), model=document.getElementById('ai-model');
  if (!connection) return;
  model.placeholder = provider==='codex' ? 'Codex default' : '';
  if (provider==='codex' && model.value===localConfig?.model) model.value='';
  if (provider==='openai' && !model.value) model.value=localConfig?.model||'gpt-6-astra';
  let label='Not needed', problem='';
  if (provider==='codex') {
    label=!codexStatus?'Checking…':codexStatus.ready?codexStatus.message:'Not ready';
    problem=codexStatus&&!codexStatus.ready?codexStatus.message:'';
  } else if (provider==='openai') {
    label=!localConfig?'Checking…':localConfig.key_configured?'Ready':'API key missing';
    problem=localConfig&&!localConfig.key_configured?'Set OPENAI_API_KEY and restart the server, or use another import mode.':'';
  }
  connection.textContent=label;
  connection.classList.toggle('missing-key',Boolean(problem));
  error.textContent=problem;
}

closeModal = function() {
  if (codexRunning && localConfig) fetch('/api/cancel',{method:'POST',headers:{'X-Briefboard-Token':localConfig.session_token}}).catch(()=>{});
  baseCloseModal();
};

importModal = function() {
  localGeneration++;
  baseImportModal();
  const fileZone = document.querySelector('.upload-zone');
  const controls = document.createElement('div');
  controls.className = 'ai-settings';
  controls.innerHTML = `<div class="field-grid"><div class="field"><label class="field-label" for="generation-mode">Import mode</label><select id="generation-mode"><option value="codex">ChatGPT (Codex)</option><option value="ai">OpenAI API key</option><option value="local">Local rules</option></select></div><div class="field"><span class="field-label">Connection</span><p id="ai-connection" class="ai-connection">Checking…</p></div></div><details><summary>AI settings</summary><div class="field-grid"><div class="field"><label class="field-label" for="ai-model">Model</label><input id="ai-model" value="" placeholder="Codex default" maxlength="100"></div><div class="field"><label class="field-label" for="ai-limit">Task limit</label><input id="ai-limit" type="number" min="10" max="100" step="1" value="80"></div></div></details><p class="import-note ai-disclosure">AI sends this brief to OpenAI. Review before adding.</p>`;
  fileZone.before(controls);
  document.querySelector('.modal .import-note:not(.ai-disclosure)').textContent = 'HTML, Markdown, text, or pasted slide text. Linked files are not read.';
  document.getElementById('import-submit').innerHTML = `Generate with AI ${icon('arrow')}`;
  // Codex is checked first; the API key is only preselected when Codex cannot be used.
  Promise.all([getLocalConfig(),getCodexStatus()]).then(([config,codex])=>{
    if (!document.getElementById('ai-connection')) return;
    if (!codex.ready && config.key_configured && aiProvider()==='codex') document.getElementById('generation-mode').value='ai';
    syncAiMode();
  }).catch(()=>{
    if (document.getElementById('ai-connection')) document.getElementById('ai-connection').textContent = 'Server unavailable';
  });
};

aboutModal = function() {
  baseAboutModal();
  const list = document.querySelector('.about-list');
  if (list) list.innerHTML = `<li>The board, source text and diagrams are bundled in this local HTML build.</li><li>AI task generation runs through your local Python server, using either your own signed-in Codex CLI or an API key read from OPENAI_API_KEY. Briefboard never asks for your password, and no credential is sent to this browser.</li><li>Only the imported text and source name are submitted when you click Generate with AI. Local-rule extraction makes no OpenAI request.</li><li>Review and edit AI hierarchy, disciplines, videos, scope and completion criteria before adding tasks.</li><li>Board data saves in this browser. Use workspace JSON export/restore to transfer work from the hosted demo.</li><li>Direct Google Slides access, linked image/video interpretation and team synchronization are not included.</li>`;
};

function aiDepth(item, records) {
  let parent=item.parent_id, depth=0;
  const seen=new Set([item.id]);
  while(parent&&!seen.has(parent)) {
    seen.add(parent); depth++;
    parent=records.find(t=>t.id===parent)?.parent_id;
  }
  return Math.min(4,depth);
}

function orderAiTasks(tasks) {
  const result=[], seen=new Set();
  const visit=t=>{
    if(seen.has(t.id)) return;
    seen.add(t.id); result.push(t);
    tasks.filter(x=>x.parent_id===t.id).forEach(visit);
  };
  tasks.filter(t=>t.parent_id===null).forEach(visit);
  tasks.forEach(visit);
  return result;
}

function renderAiReview(plan) {
  const items=orderAiTasks(plan.tasks);
  importDraft.items=items;
  const projects=plan.base_projects||[];
  const ranked=projects.map(p=>({...p,count:p.references.filter(r=>r.verified).length})).filter(p=>p.count).sort((a,b)=>b.count-a.count);
  const summary = `<div class="ai-summary"><strong>${esc(plan.project_title)}</strong><small>${items.length} records · ${esc(importDraft.model)}</small>${ranked.length?`<details class="base-review"><summary>Suggested base: ${esc(ranked[0].name)} · ${ranked[0].count} refs</summary>${ranked.map(p=>`<div class="base-review-row"><span>${esc(p.name)}</span><small>${p.count} refs</small></div>`).join('')}</details>`:''}<details><summary>Plan details${plan.open_questions.length?' · '+plan.open_questions.length+' questions':''}</summary><p>${esc(plan.summary)}</p>${plan.open_questions.length?`<ul>${plan.open_questions.map(q=>`<li>${esc(q)}</li>`).join('')}</ul>`:''}</details></div>`;
  document.getElementById('import-review').innerHTML = summary + (items.length ? `<p class="import-note">Keep the tasks you need. Parent and prerequisite changes are noted.</p>` : '<p class="import-note">No tasks returned. Try a more specific brief.</p>') + items.map((t,i)=>`
    <div class="ai-review-item ${t.kind==='group'?'ai-group':''}" style="margin-left:${aiDepth(t,items)*12}px">
      <label class="ai-include"><input type="checkbox" data-ai-include="${i}" checked aria-label="Include ${esc(t.title)}"><span>${t.kind==='group'?'Work package':'Task'} · ${esc(t.id)}</span></label>
      <div class="ai-review-heading"><input data-ai-title="${i}" value="${esc(t.title.slice(0,240))}" maxlength="240" aria-label="Title ${i+1}">${t.kind==='group'?'<span class="count-pill">Group</span>':`<select data-ai-discipline="${i}" aria-label="Discipline ${i+1}">${options(['Dev','Art','QA'],t.discipline)}</select>`}</div>
      <details><summary>Details${t.videos.length?' · videos '+t.videos.join(', '):' · video scope unspecified'}</summary>
        <label class="field-label" for="ai-desc-${i}">Description</label><textarea id="ai-desc-${i}" data-ai-description="${i}" rows="3">${esc(t.description)}</textarea>
        <label class="field-label" for="ai-criteria-${i}">Done when · one per line</label><textarea id="ai-criteria-${i}" data-ai-criteria="${i}" rows="3">${esc(t.criteria.join('\n'))}</textarea>
        <label class="field-label" for="ai-videos-${i}">Videos · comma-separated</label><input id="ai-videos-${i}" data-ai-videos="${i}" value="${esc(t.videos.join(', '))}">
        <p class="detail-note">Prerequisites: ${t.dependencies.length?esc(t.dependencies.join(', ')):'none specified'} · effort: ${t.estimate_days===null?'unspecified; 1 day placeholder until edited':esc(t.estimate_days)+' days from brief'}</p>
        ${t.notes?`<p class="detail-note">${esc(t.notes)}</p>`:''}
        ${t.evidence.length?t.evidence.map(e=>`<blockquote class="ai-evidence">${esc(e.quote)}<small>${esc(e.section)} · ${e.verified?'exact text found in source':'quote unverified; confirm against source'}</small></blockquote>`).join(''):'<p class="detail-note">No exact evidence supplied. Confirm whether this is an inferred task.</p>'}
      </details>
    </div>`).join('');
  const button=document.getElementById('import-submit');
  button.dataset.action='add-draft';button.textContent='Add selected tasks';button.disabled=!items.length;
  document.getElementById('import-summary').textContent='Nothing added yet.';
}

generateDraft = async function() {
  const provider=aiProvider();
  if(!provider) {
    baseGenerateDraft();return;
  }
  const raw=document.getElementById('import-text').value.trim();
  const title=document.getElementById('import-title').value.trim()||'Imported brief';
  const error=document.getElementById('import-error');
  if(!raw){error.textContent='Choose a brief file or paste source text first.';return;}
  let text;
  try {text=extractDraft(raw,title).text;}catch(e){error.textContent='Could not extract text from this file. Paste the brief as plain text.';return;}
  const model=document.getElementById('ai-model').value.trim();
  const maxTasks=Number(document.getElementById('ai-limit').value);
  if(!Number.isInteger(maxTasks)||maxTasks<10||maxTasks>100){error.textContent='Choose a task limit between 10 and 100.';return;}
  if(text.length>200000){error.textContent='This version accepts up to 200,000 source characters. Split the brief into sections.';return;}
  const button=document.getElementById('import-submit');
  button.disabled=true;button.textContent=provider==='codex'?'Planning with Codex…':'Planning with OpenAI…';error.textContent='';
  document.getElementById('import-review').innerHTML=`<div class="evidence">${provider==='codex'?'Generating… This can take several minutes. Closing stops the request.':'Generating… Closing discards the draft; the API request may continue.'}</div>`;
  const requestId=++localGeneration;
  const controller=new AbortController();
  // Slightly longer than the server's own limit, so its clearer timeout message arrives first.
  const timer=setTimeout(()=>controller.abort(),provider==='codex'?615000:255000);
  codexRunning=provider==='codex';
  document.querySelectorAll('.ai-settings input,.ai-settings select,#import-title,#import-text,#import-file').forEach(el=>el.disabled=true);
  try{
    const config=await getLocalConfig();
    if(provider==='openai'&&!config.key_configured) throw Error('Set OPENAI_API_KEY in the server terminal and restart the server. The key belongs in the server environment, not in this page.');
    const response=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json','X-Briefboard-Token':config.session_token},body:JSON.stringify({title,text,model,max_tasks:maxTasks,provider,language:currentLanguage}),signal:controller.signal});
    const result=await response.json();
    if(!response.ok) throw Error(result.error||'AI generation failed. No tasks were added.');
    if(!document.getElementById('import-review')||activeModal?.type!=='import'||requestId!==localGeneration) return;
    if(!result.plan||!Array.isArray(result.plan.tasks)) throw Error('The server returned an invalid plan.');
    importDraft={title,text,ai:true,model:result.model,plan:result.plan,items:[]};
    renderAiReview(result.plan);
    error.textContent='';
  }catch(e){
    if(document.getElementById('import-error')&&activeModal?.type==='import'&&requestId===localGeneration){
      document.getElementById('import-error').textContent=e.name==='AbortError'?'The request timed out. The provider request may still finish; no automatic retry was made.':e.message;
      document.getElementById('import-review').innerHTML='';
      button.dataset.action='generate';button.textContent='Generate with AI';button.disabled=false;
    }
  }finally{
    clearTimeout(timer);
    if(requestId===localGeneration)codexRunning=false;
    if(document.getElementById('generation-mode')&&requestId===localGeneration)document.querySelectorAll('.ai-settings input,.ai-settings select,#import-title,#import-text,#import-file').forEach(el=>el.disabled=false);
  }
};

function collectAiSelection(draft, root) {
  return draft.items.map((t,i)=>({...clone(t),
    selected:root.querySelector(`[data-ai-include="${i}"]`).checked,
    title:root.querySelector(`[data-ai-title="${i}"]`).value.trim(),
    discipline:t.kind==='group'?'Mixed':root.querySelector(`[data-ai-discipline="${i}"]`).value,
    description:root.querySelector(`[data-ai-description="${i}"]`).value.trim(),
    criteria:root.querySelector(`[data-ai-criteria="${i}"]`).value.split('\n').map(x=>x.trim()).filter(Boolean),
    videos:[...new Set(root.querySelector(`[data-ai-videos="${i}"]`).value.split(',').map(x=>x.trim()).filter(Boolean))]
  })).filter(t=>t.selected);
}

function applyAiSelection(draft, selected) {
  if(!selected.length||selected.some(t=>!t.title))throw Error('Select at least one record and give it a title.');
  if(selected.some(t=>t.videos.some(v=>!ALL_VIDEOS.includes(v))))throw Error('Use video tags 001 through 008, separated by commas.');
  const allRecords=new Map(draft.items.map(t=>[t.id,t]));
  selected=selected.filter(record=>record.kind!=='group'||selected.some(t=>{
    if(t.kind==='group')return false;
    let parent=t.parent_id;const seen=new Set();
    while(parent&&!seen.has(parent)){if(parent===record.id)return true;seen.add(parent);parent=allRecords.get(parent)?.parent_id;}
    return false;
  }));
  if(!selected.some(t=>t.kind==='task'))throw Error('Select at least one implementation task. Empty work packages are not added.');
  const before=clone(state),selectedIds=new Set(selected.map(t=>t.id));
  const newIds=new Map();let next=Number(nextId());
  selected.forEach(t=>newIds.set(t.id,String(next++).padStart(3,'0')));
  const sourceId='ai-'+Date.now().toString(36);
  const fullSource={id:sourceId,title:draft.title,text:draft.text,file:draft.title,images:[],imported:true};
  const fullRecords=new Map(draft.items.map(t=>[t.id,t]));
  try{
    state.sources.push(fullSource);
    for(const record of selected){
      let parent=record.parent_id;const seen=new Set([record.id]);
      while(parent&&!selectedIds.has(parent)&&!seen.has(parent)){seen.add(parent);parent=fullRecords.get(parent)?.parent_id||null;}
      const hasChildren=selected.some(x=>{let p=x.parent_id,walked=new Set();while(p&&!walked.has(p)){walked.add(p);if(p===record.id)return true;if(selectedIds.has(p))return false;p=fullRecords.get(p)?.parent_id;}return false;});
      if(record.kind==='group'&&!hasChildren)continue;
      const sourceIds=[sourceId];
      if(record.evidence.length){
        const evidenceId=sourceId+'-'+newIds.get(record.id);
        state.sources.push({id:evidenceId,title:record.evidence[0].section||record.title,file:draft.title,imported:true,images:[],text:record.evidence.map(e=>`${e.section}\n${e.quote}\n[${e.verified?'Exact text verified in supplied source':'Quote unverified: review source'}]`).join('\n\n')});
        sourceIds.push(evidenceId);
      }
      const excluded=record.dependencies.filter(id=>!selectedIds.has(id));
      const notes=[record.notes,`AI-suggested plan using ${draft.model}. Review before production.`,record.estimate_days===null?'Effort unspecified in source. Timeline uses a 1-day placeholder until edited.':'Effort extracted from brief; verify units.',excluded.length?'Excluded prerequisite draft IDs: '+excluded.join(', '):'',draft.plan.open_questions.length?'Plan questions:\n'+draft.plan.open_questions.join('\n'):''].filter(Boolean).join('\n\n');
      state.tasks.push({id:newIds.get(record.id),parent:parent?newIds.get(parent):null,kind:record.kind,title:record.title,
        discipline:record.kind==='group'?'Mixed':record.discipline,videos:record.videos,description:record.description,
        criteria:record.criteria,sources:sourceIds,dependencies:record.dependencies.filter(id=>selectedIds.has(id)).map(id=>newIds.get(id)),
        estimate:record.kind==='group'?0:(record.estimate_days??1),status:'Backlog',owner:'Unassigned',priority:'Medium',notes});
    }
    if(state.tasks.length===before.tasks.length)throw Error('Select at least one implementation task. Empty work packages are not added.');
    const projectRefs=(draft.plan.base_projects||[]).map(p=>({id:sourceId+'-'+p.id,name:p.name,aliases:p.aliases,scope:sourceId,brief:draft.title,references:p.references.map(r=>({...r,source_id:sourceId}))}));
    state.baseProjects=[...(state.baseProjects||[]),...projectRefs];
    state.baseBriefs={...state.baseBriefs,[sourceId]:draft.title};
    state.baseReferenceScope=sourceId;
    validateWorkspace(state);
    if(computeSchedule().unresolved.length)throw Error('This selection contains unschedulable prerequisites. Review the generated hierarchy before adding.');
  }catch(e){state=before;throw e;}
  return state.tasks.length-before.tasks.length;
}

addDraft = function() {
  if(!importDraft?.ai){baseAddDraft();return;}
  try{
    const added=applyAiSelection(importDraft,collectAiSelection(importDraft,document.getElementById('import-review')));
    state.view='tree';state.filters=clone(DEFAULT_STATE.filters);state.collapsed=state.collapsed.filter(id=>!state.tasks.some(t=>t.id===id&&t.sources.some(s=>s.startsWith('ai-'))));
    persist();closeModal();render();toast(`Added ${added} records.`);
  }catch(e){document.getElementById('import-error').textContent=e.message;}
};

document.addEventListener('change',e=>{
  if(e.target.id==='generation-mode'){
    importDraft=null;document.getElementById('import-review').innerHTML='';document.getElementById('import-error').textContent='';
    const ai=e.target.value!=='local';
    document.getElementById('ai-model').disabled=!ai;document.getElementById('ai-limit').disabled=!ai;
    const button=document.getElementById('import-submit');button.dataset.action='generate';button.disabled=false;
    button.textContent=ai?'Generate with AI':'Generate local draft';
    syncAiMode();
  }
});

document.addEventListener('input',e=>{
  if(['import-text','import-title'].includes(e.target.id)){
    const button=document.getElementById('import-submit');
    if(button&&button.dataset.action==='generate'){button.disabled=false;button.textContent=aiProvider()?'Generate with AI':'Generate local draft';}
  }
});
