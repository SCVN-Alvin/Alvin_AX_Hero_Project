/* Base choices are ranked by distinct requirements backed by source text. */
function referenceKey(value){return String(value||'').toLowerCase().replace(/[’']/g,'').replace(/[^\p{L}\p{N}]+/gu,' ').trim();}
function baseScopes(){const scopes=new Map(Object.entries(state.baseBriefs||{}));for(const p of state.baseProjects||[])scopes.set(p.scope||'seed',p.brief||'Crusher Factory');return [...scopes].map(([id,title])=>({id,title}));}
function activeBaseScope(){const scopes=baseScopes();return scopes.some(s=>s.id===state.baseReferenceScope)?state.baseReferenceScope:scopes[0]?.id;}
function rankBaseProjects(projects=state.baseProjects||[],scope=activeBaseScope()){
 const merged=[];
 for(const raw of projects.filter(p=>(p.scope||'seed')===scope)){
  const names=[raw.name,...raw.aliases||[]].map(referenceKey).filter(Boolean);
  const matches=merged.filter(p=>p.keys.some(k=>names.includes(k)));
  let candidate=matches[0];
  if(!candidate){candidate={id:raw.id,name:raw.name,aliases:[],keys:[],references:[]};merged.push(candidate);}
  for(const other of matches.slice(1)){
   candidate.keys.push(...other.keys);candidate.aliases.push(...other.aliases);
   for(const ref of other.references)if(!candidate.references.some(r=>referenceKey(r.requirement)===referenceKey(ref.requirement)))candidate.references.push(ref);
   merged.splice(merged.indexOf(other),1);
  }
  candidate.keys=[...new Set([...candidate.keys,...names])];candidate.aliases=[...new Set([...candidate.aliases,...raw.aliases||[]])];
  for(const ref of raw.references||[]){
   const source=state.sources.find(s=>s.id===ref.source_id);
   const quote=String(ref.quote||'').replace(/\s+/g,' ').trim();
   const exact=!!quote&&!!source&&source.text.replace(/\s+/g,' ').includes(quote);
   if(!exact||ref.verified===false)continue;
   const key=referenceKey(ref.requirement);
   if(key&&!candidate.references.some(r=>referenceKey(r.requirement)===key))candidate.references.push({...ref});
  }
 }
 return merged.filter(p=>p.references.length).map(p=>({...p,count:p.references.length,reuse:p.references.filter(r=>r.use_type==='Reuse').length,categories:[...new Set(p.references.map(r=>r.category))]})).sort((a,b)=>b.count-a.count||b.reuse-a.reuse||a.name.localeCompare(b.name));
}
function baseChoice(){const ranks=rankBaseProjects(),selected=state.baseSelections?.[activeBaseScope()];return{ranks,candidate:ranks.find(p=>p.id===selected)||ranks[0],manual:!!ranks.find(p=>p.id===selected),tied:ranks.length>1&&ranks[0].count===ranks[1].count};}
function baseSummaryHtml(){const{candidate,manual,tied}=baseChoice();return `<button class="base-summary" data-action="bases"><span class="base-summary-icon">${icon('folder')}</span><span><small>${manual?'Selected base':tied?'Top references · tied':'Suggested base'}</small><strong>${candidate?esc(candidate.name):'No project references yet'}</strong></span><span class="base-summary-tail">${candidate?`${candidate.count} references`:''}${icon('right')}</span></button>`;}
function baseProjectsHtml(){const{ranks,candidate,manual,tied}=baseChoice();const scopes=baseScopes();return `<div class="base-page"><div class="base-page-head"><div><h2>Base project</h2><p>Distinct requirements with source evidence.</p></div>${scopes.length>1?`<select id="base-scope" class="filter" aria-label="Brief to analyze">${scopes.map(s=>`<option value="${esc(s.id)}" ${activeBaseScope()===s.id?'selected':''}>${esc(s.title)}</option>`).join('')}</select>`:''}</div>${candidate?`<div class="base-recommendation"><span class="base-rank">${icon('folder')}</span><div><span class="eyebrow">${manual?'YOUR CHOICE':tied?'JOINT LEAD':'MOST REFERENCED'}</span><h3>${esc(candidate.name)}</h3><p>${candidate.aliases.find(a=>/^Video /i.test(a))?esc(candidate.aliases.find(a=>/^Video /i.test(a)))+' · ':''}${candidate.count} refs · ${candidate.reuse} reuse</p></div>${manual?'<button class="button" data-action="base-auto">Use ranking</button>':''}</div><p class="base-caveat">Reference coverage only. Check the actual project before using it.</p><div class="base-ranking">${ranks.map((p,i)=>`<article class="base-candidate"><div class="base-candidate-head"><span class="base-number">${i+1}</span><div><h3>${esc(p.name)}</h3><span class="base-aliases">${esc(p.aliases.filter(a=>referenceKey(a)!==referenceKey(p.name)).join(' · '))}</span></div><span class="base-count">${p.count}<small>refs</small></span><button class="button ${manual&&candidate.id===p.id?'primary':''}" data-action="base-select" data-id="${esc(p.id)}">${manual&&candidate.id===p.id?'Selected':'Use as base'}</button></div><div class="base-tags">${p.categories.map(c=>`<span class="count-pill">${esc(c)}</span>`).join('')}</div><details ${i===0?'open':''}><summary>${p.references.length} supporting requirements</summary><div class="base-evidence-list">${p.references.map(ref=>`<div class="base-evidence"><span class="base-use ${ref.use_type==='Reuse'?'reuse':''}">${esc(ref.use_type)}</span><strong>${esc(ref.requirement)}</strong><button data-action="source" data-id="${esc(ref.source_id)}" title="Open source">${icon('link')}</button><blockquote>${esc(ref.quote)}</blockquote></div>`).join('')}</div></details></article>`).join('')}</div>`:`<div class="empty"><strong>No verified project references.</strong>Generate an AI plan from a brief that names its source projects.</div>`}</div>`;}
function validateBaseProjects(data){
 if(data.baseProjects===undefined)return;
 if(!Array.isArray(data.baseProjects)||data.baseProjects.length>300)throw Error('Invalid base-project references.');
 const sources=new Set(data.sources.map(s=>s.id));
 for(const p of data.baseProjects){
  if(!p||typeof p.id!=='string'||typeof p.name!=='string'||!Array.isArray(p.aliases)||p.aliases.some(a=>typeof a!=='string')||!Array.isArray(p.references)||p.references.length>300)throw Error('Invalid project reference fields.');
  if(p.scope!==undefined&&typeof p.scope!=='string'||p.brief!==undefined&&typeof p.brief!=='string')throw Error('Invalid reference scope.');
  for(const r of p.references)if(!r||typeof r.requirement!=='string'||typeof r.quote!=='string'||!['Art','Map','Camera','Gameplay','UI','Audio'].includes(r.category)||!['Reuse','Reference'].includes(r.use_type)||!sources.has(r.source_id))throw Error('Invalid project evidence.');
 }
 if(data.baseSelections!==undefined&&(!data.baseSelections||typeof data.baseSelections!=='object'||Array.isArray(data.baseSelections)||Object.values(data.baseSelections).some(x=>typeof x!=='string')))throw Error('Invalid base selection.');
}
document.addEventListener('click',e=>{
 const b=e.target.closest('[data-action]');if(!b)return;
 if(b.dataset.action==='bases'){state.view='bases';persist();render();}
 if(b.dataset.action==='base-select'){state.baseSelections={...state.baseSelections,[activeBaseScope()]:b.dataset.id};persist();render();toast('Base project selected.');}
 if(b.dataset.action==='base-auto'){if(state.baseSelections)delete state.baseSelections[activeBaseScope()];persist();render();}
});
document.addEventListener('change',e=>{if(e.target.id==='base-scope'){state.baseReferenceScope=e.target.value;persist();render();}});
