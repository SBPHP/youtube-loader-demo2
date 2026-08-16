const $ = (s) => document.querySelector(s);
const state = { info: null, mode: 'video', jobs: new Map(), settings: null, suggestions: [], history: [], playlistSelection: new Set(), queued: [] };
const fmt = new Intl.NumberFormat('de-DE', { notation: 'compact', maximumFractionDigits: 1 });

function escapeHtml(v=''){ return String(v).replace(/[&<>'"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function msg(text, error=false){ const el=$('#message'); el.textContent=text; el.className='message'+(error?' error':''); }
function clearMsg(){ $('#message').className='message hidden'; }
function setView(name){
  document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('hidden', el.dataset.view!==name));
  document.querySelectorAll('.nav[data-target]').forEach(el=>el.classList.toggle('active', el.dataset.target===name));
  if(name==='history') loadHistory();
  if(name==='suggestions') renderSuggestionTargets();
  if(name==='runtime') loadRuntime();
}

async function loadSettings(){
  try{
    const r=await fetch('/api/settings'); if(!r.ok) return;
    state.settings=await r.json(); state.mode=state.settings.default_mode || 'video';
    document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.mode===state.mode));
    $('#thumb').checked=!!state.settings.embed_thumbnail; $('#metadata').checked=state.settings.write_metadata!==false; fillContainer();
  }catch(_e){}
}

function fillContainer(){
  const el=$('#container');
  const formats = state.mode==='video' ? ['mp4','mkv','webm'] : ['mp3','m4a','aac','opus','flac','wav'];
  el.innerHTML=formats.map(x=>`<option value="${x}">${x.toUpperCase()}</option>`).join('');
  const preferred=state.mode==='video'?state.settings?.default_video_container:state.settings?.default_audio_container;
  if(preferred && formats.includes(preferred)) el.value=preferred;
  $('#codecLabel').style.display=state.mode==='video'?'grid':'none';
  $('#qualityText').textContent=state.mode==='video'?'Qualität':'Audioqualität'; fillQuality();
}

function fillQuality(){
  const q=$('#quality');
  if(state.mode==='video'){
    const heights=(state.info?.video_heights?.length ? state.info.video_heights : [2160,1440,1080,720,480,360]).slice(0,10);
    q.innerHTML='<option value="best">Beste verfügbar</option>'+heights.map(h=>`<option value="${h}">${h}p</option>`).join('');
    if(state.settings?.default_video_quality && [...q.options].some(o=>o.value===String(state.settings.default_video_quality))) q.value=String(state.settings.default_video_quality);
  }else{
    q.innerHTML=[320,256,192,128].map(b=>`<option value="${b}">${b} kbps</option>`).join('');
    if(state.settings?.default_audio_bitrate) q.value=String(state.settings.default_audio_bitrate);
  }
}

function updatePlaylistSelectionUI(){
  const entries=state.info?.entries || [];
  const selected=state.playlistSelection.size;
  if($('#playlistSelectionText')) $('#playlistSelectionText').textContent=`${selected} von ${entries.length} Videos ausgewählt`;
  if($('#playlistSelectAll')) {
    $('#playlistSelectAll').checked=entries.length>0 && selected===entries.length;
    $('#playlistSelectAll').indeterminate=selected>0 && selected<entries.length;
  }
  document.querySelectorAll('[data-playlist-index]').forEach(row=>{
    const idx=Number(row.dataset.playlistIndex);
    row.classList.toggle('unselected', !state.playlistSelection.has(idx));
    const cb=row.querySelector('input[type="checkbox"]'); if(cb) cb.checked=state.playlistSelection.has(idx);
  });
  if(state.info?.is_playlist) $('#downloadBtn').disabled=selected===0;
}

function renderPlaylist(info){
  const box=$('#playlistPanel');
  if(!info.is_playlist){ box.classList.add('hidden'); state.playlistSelection.clear(); return; }
  box.classList.remove('hidden');
  const entries=info.entries || [];
  state.playlistSelection=new Set(entries.map((_e,i)=>i+1));
  $('#playlistTitle').textContent=`Playlist · ${info.playlist_count || entries.length} Videos`;
  $('#playlistEntries').innerHTML=entries.map((e,i)=>`<div class="playlist-entry selectable" data-playlist-index="${i+1}"><input class="playlist-checkbox" type="checkbox" checked aria-label="Video auswählen"><span>${String(i+1).padStart(2,'0')}</span>${e.thumbnail?`<img src="${escapeHtml(e.thumbnail)}" alt="">`:''}<div><b>${escapeHtml(e.title)}</b><small>${escapeHtml(e.channel||'')} ${e.duration_text?'· '+escapeHtml(e.duration_text):''}</small></div><button class="secondary mini single-playlist-download" data-single-index="${i+1}">Nur dieses</button></div>`).join('') || '<p class="muted">Keine Einträge gefunden.</p>';
  updatePlaylistSelectionUI();
}
function renderInfo(info){
  state.info=info; $('#infoCard').classList.remove('hidden');
  $('#thumbnail').src=info.thumbnail || ''; $('#previewImage').src=info.thumbnail || '';
  $('#title').textContent=info.title || 'Unbekannter Titel'; $('#channel').textContent=info.channel || 'Unbekannter Kanal';
  $('#duration').textContent=info.is_playlist ? `${info.playlist_count || info.entries?.length || 0} Videos` : (info.duration_text || '–');
  $('#views').textContent=info.is_playlist ? 'Batch' : (info.view_count ? fmt.format(info.view_count) : '–');
  $('#previewTitle').textContent=info.title || '–'; $('#previewChannel').textContent=info.channel || '–';
  $('#previewDuration').textContent=info.is_playlist ? `${info.playlist_count || 0} Videos` : (info.duration_text || '–');
  $('#filename').placeholder=info.is_playlist ? 'Bei Playlists automatisch pro Video' : (info.title || 'Automatisch aus Videotitel');
  $('#filename').disabled=!!info.is_playlist;
  $('#videoQualities').innerHTML=info.is_playlist?'<span class="chip selected">pro Video automatisch</span>':((info.video_heights||[]).slice(0,8).map(h=>`<span class="chip">${h}p</span>`).join('')||'<span class="chip">Auto</span>');
  $('#audioQualities').innerHTML=info.is_playlist?'<span class="chip selected">Batch</span>':((info.audio_bitrates||[]).slice(0,8).map(b=>`<span class="chip">${b}kbps</span>`).join('')||'<span class="chip">Best</span>');
  renderPlaylist(info); fillQuality(); $('#downloadBtn').disabled=false; $('#queueBtn').disabled=false;
  $('#downloadBtn').textContent=info.is_playlist?'⇩ Playlist herunterladen':'⇩ Download starten';
}

async function analyze(){
  clearMsg(); const url=$('#url').value.trim(); if(!url) return msg('Bitte zuerst eine YouTube-URL einfügen.', true);
  $('#analyzeBtn').disabled=true; $('#analyzeBtn').textContent='Analysiere …';
  try{
    const r=await fetch('/api/youtube/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const data=await r.json(); if(!r.ok) throw new Error(data.detail || 'Analyse fehlgeschlagen');
    renderInfo(data); msg(data.is_playlist?`Playlist erkannt: ${data.playlist_count || data.entries.length} Videos`:`Gefunden: ${data.title}`);
    loadSuggestions();
  }catch(e){ msg(e.message,true); }
  finally{ $('#analyzeBtn').disabled=false; $('#analyzeBtn').textContent='⌕ Analysieren'; }
}

function jobMarkup(job){
  const title=job.title || state.info?.title || 'Download';
  return `<div class="job" id="job-${job.id}"><div><b class="job-title">${escapeHtml(title)}</b><small class="current-item"></small><div class="job-meta"><span class="status">Wartet</span><span class="metrics"></span></div><div class="bar"><i></i></div></div><div class="action"></div></div>`;
}

function updateJobUI(job){
  state.jobs.set(job.id, job);
  updateActiveJobCount();
  let el=$(`#job-${job.id}`);
  if(!el){ if($('#jobs .muted')) $('#jobs').innerHTML=''; $('#jobs').insertAdjacentHTML('afterbegin',jobMarkup(job)); el=$(`#job-${job.id}`); }
  const names={queued:'Wartet',starting:'Startet',downloading:'Wird heruntergeladen',processing:'Wird verarbeitet',cancelling:'Wird abgebrochen',cancelled:'Abgebrochen',finished:'Fertig',finished_with_errors:'Fertig · mit Fehlern',failed:'Fehlgeschlagen'};
  el.querySelector('.status').textContent=job.status==='failed'?`Fehler: ${job.error||'unbekannt'}`:(names[job.status]||job.status);
  el.querySelector('.job-title').textContent=job.title || state.info?.title || 'Download';
  const current=el.querySelector('.current-item'); current.textContent=job.current_title ? `${job.current_item&&job.playlist_count?`${job.current_item}/${job.playlist_count} · `:''}${job.current_title}` : '';
  const metrics=[]; if(job.percent!=null) metrics.push(`${Number(job.percent).toFixed(job.percent%1?1:0)}%`); if(job.downloaded_text&&job.total_text) metrics.push(`${job.downloaded_text} / ${job.total_text}`); if(job.speed_text) metrics.push(job.speed_text); if(job.eta_text) metrics.push(`${job.eta_text} verbleibend`); if(job.file_size_text) metrics.push(job.file_size_text);
  el.querySelector('.metrics').textContent=metrics.join(' · '); el.querySelector('.bar i').style.width=`${job.percent||0}%`;
  let children=el.querySelector('.job-children');
  if(job.playlist_items?.length){
    if(!children){ children=document.createElement('div'); children.className='job-children'; el.firstElementChild.appendChild(children); }
    children.innerHTML=job.playlist_items.map(item=>`<div class="job-child"><span>${String(item.index||'').padStart(2,'0')} · ${escapeHtml(item.title||'Video')}</span><span>${escapeHtml(item.status||'queued')} · ${Number(item.percent||0).toFixed(0)}%</span></div>`).join('');
  }else if(children){ children.remove(); }
  const terminal=['finished','finished_with_errors','failed','cancelled'].includes(job.status);
  const action=[]; if(job.download_url) action.push(`<a href="${job.download_url}">Datei laden</a>`); if(!terminal) action.push(`<button class="danger mini" data-cancel="${job.id}">✕ Abbrechen</button>`); if(['failed','cancelled','finished_with_errors'].includes(job.status)) action.push(`<button class="secondary mini" data-retry="${job.id}">↻ Retry</button>`); if(job.status==='finished'&&job.is_playlist) action.push('<span class="done-note">Im Downloads-Ordner gespeichert</span>');
  el.querySelector('.action').innerHTML=action.join('');
}

async function pollJob(id){
  const timer=setInterval(async()=>{ try{ const r=await fetch(`/api/youtube/jobs/${id}`); const job=await r.json(); updateJobUI(job); if(['finished','finished_with_errors','failed','cancelled'].includes(job.status)) { clearInterval(timer); loadHistory(); } }catch(_e){ clearInterval(timer); } },900);
}

async function startDownload(){
  const body=buildDownloadBody(); if(!body) return msg('Bitte URL zuerst analysieren.', true);
  if(body.is_playlist && !body.selected_entries.length) return msg('Wähle mindestens ein Playlist-Video aus.', true);
  $('#downloadBtn').disabled=true;
  try{
    await submitDownloadBody(body);
    msg(body.is_playlist?`${body.selected_entries.length} Playlist-Videos gestartet.`:'Download gestartet.');
  }catch(e){ msg(e.message,true); }
  finally{ $('#downloadBtn').disabled=false; updatePlaylistSelectionUI(); }
}

function updateActiveJobCount(){
  const active=[...state.jobs.values()].filter(j=>!['finished','finished_with_errors','failed','cancelled'].includes(j.status)).length;
  if($('#activeJobCount')) $('#activeJobCount').textContent=`${active} aktiv`;
}

function buildDownloadBody(){
  const url=$('#url').value.trim();
  if(!state.info || !url) return null;
  const q=$('#quality').value;
  const body={url,mode:state.mode,container:$('#container').value,filename:$('#filename').value.trim()||null,embed_thumbnail:$('#thumb').checked,write_metadata:$('#metadata').checked,is_playlist:!!state.info.is_playlist,title:state.info.title,selected_entries:state.info.is_playlist?[...state.playlistSelection].sort((a,b)=>a-b):null};
  if(state.mode==='video') body.quality=q; else body.audio_bitrate=Number(q);
  return body;
}

async function findDuplicate(body){
  try{
    const r=await fetch('/api/youtube/duplicates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok) return null;
    const data=await r.json();
    return data.duplicate ? data.items?.[0] : null;
  }catch(_e){ return null; }
}

async function submitDownloadBody(body, {skipDuplicateCheck=false}={}){
  if(!skipDuplicateCheck){
    const duplicate=await findDuplicate(body);
    if(duplicate){
      const again=window.confirm(`Dieser Download existiert bereits als ${String(body.container).toUpperCase()}. Trotzdem erneut herunterladen?`);
      if(!again) throw new Error('Download nicht gestartet – vorhandene Datei bleibt bestehen.');
    }
  }
  const r=await fetch('/api/youtube/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const job=await r.json(); if(!r.ok) throw new Error(job.detail||'Download konnte nicht gestartet werden');
  job.is_playlist=body.is_playlist; updateJobUI(job); pollJob(job.id); return job;
}

async function addToQueue(){
  const body=buildDownloadBody(); if(!body) return msg('Bitte URL zuerst analysieren.', true);
  if(body.is_playlist && !body.selected_entries.length) return msg('Wähle mindestens ein Playlist-Video aus.', true);
  try{
    await submitDownloadBody(body);
    msg(body.is_playlist?`${body.selected_entries.length} Playlist-Videos zur Warteschlange hinzugefügt.`:'Download zur Warteschlange hinzugefügt.');
  }catch(e){ msg(e.message,true); }
}

async function startSinglePlaylistItem(index){
  if(!state.info?.is_playlist) return;
  const previous=new Set(state.playlistSelection);
  state.playlistSelection=new Set([Number(index)]); updatePlaylistSelectionUI();
  const body=buildDownloadBody();
  state.playlistSelection=previous; updatePlaylistSelectionUI();
  try{ await submitDownloadBody(body); msg(`Playlist-Video ${index} gestartet.`); }catch(e){ msg(e.message,true); }
}

async function cancelJob(id){
  try{ const r=await fetch(`/api/youtube/jobs/${id}/cancel`,{method:'POST'}); const d=await r.json(); if(!r.ok) throw new Error(d.detail||'Abbruch fehlgeschlagen'); msg('Abbruch angefordert.'); }
  catch(e){ msg(e.message,true); }
}

async function loadHistory(){
  const host=$('#historyList'); host.innerHTML='<p class="muted">Lade Verlauf …</p>';
  try{
    const r=await fetch('/api/youtube/history?limit=100'); const data=await r.json(); if(!r.ok) throw new Error('Verlauf konnte nicht geladen werden');
    state.history=data.items || [];
    renderHistory();
  }catch(e){ host.innerHTML=`<p class="message error">${escapeHtml(e.message)}</p>`; }
}

function renderHistory(){
  const items=state.history || [];
  $('#statTotal').textContent=items.length;
  $('#statFinished').textContent=items.filter(x=>['finished','finished_with_errors'].includes(x.status)).length;
  $('#statFailed').textContent=items.filter(x=>x.status==='failed').length;
  $('#statPlaylists').textContent=items.filter(x=>x.is_playlist).length;
  const status=$('#historyStatus')?.value || 'all'; const type=$('#historyType')?.value || 'all';
  const filtered=items.filter(j=>(status==='all'||j.status===status) && (type==='all'||(type==='playlist'?j.is_playlist:(!j.is_playlist&&j.mode===type))));
  const host=$('#historyList');
  if(!filtered.length){ host.innerHTML='<div class="history-empty">Für diesen Filter gibt es noch keine Downloads.</div>'; return; }
  const labels={finished:'Fertig',finished_with_errors:'Teilweise',failed:'Fehler',cancelled:'Abgebrochen',queued:'Wartet',starting:'Startet',downloading:'Lädt',processing:'Verarbeitet',cancelling:'Abbruch …'};
  host.innerHTML=filtered.map(j=>`<div class="history-item"><div><b>${escapeHtml(j.title||'Download')}</b><small>${j.is_playlist?'Playlist · ':''}${escapeHtml(j.mode||'')} · ${escapeHtml(j.container||'')}${j.file_size_text?' · '+escapeHtml(j.file_size_text):''}</small></div><span class="history-status ${j.status}">${escapeHtml(labels[j.status]||j.status)}</span><div>${j.download_url?`<a href="${j.download_url}">Datei laden</a>`:''}${['failed','cancelled','finished_with_errors'].includes(j.status)?`<button class="secondary mini" data-retry="${j.id}">↻ Retry</button>`:''}</div></div>`).join('');
}

function suggestionMarkup(item){
  const url=escapeHtml(item.url||'');
  return `<article class="suggestion-card" data-suggestion-url="${url}">${item.thumbnail?`<img src="${escapeHtml(item.thumbnail)}" alt="">`:'<div class="mini-thumb"></div>'}<div class="suggestion-copy"><b>${escapeHtml(item.title||'YouTube Video')}</b><small>${escapeHtml(item.channel||'')}${item.duration_text?' · '+escapeHtml(item.duration_text):''}</small><div class="suggestion-action">Im Downloader öffnen →</div></div></article>`;
}

function renderSuggestionTargets(){
  const html=state.suggestions.length ? state.suggestions.map(suggestionMarkup).join('') : '<p class="muted">Noch keine Vorschläge geladen.</p>';
  if($('#suggestionGrid')) $('#suggestionGrid').innerHTML=html;
  if($('#suggestionPageGrid')) $('#suggestionPageGrid').innerHTML=html;
  if($('#sideSuggestions')) $('#sideSuggestions').innerHTML=state.suggestions.slice(0,3).map(x=>`<div class="mini-item" data-suggestion-url="${escapeHtml(x.url||'')}">${x.thumbnail?`<img src="${escapeHtml(x.thumbnail)}" alt="">`:'<div class="mini-thumb"></div>'}<div><b>${escapeHtml(x.title||'Video')}</b><small>${escapeHtml(x.channel||'')}</small></div></div>`).join('') || '<small>Nach einer Analyse erscheinen hier passende Videos.</small>';
}

async function loadSuggestions(){
  const url=$('#url').value.trim();
  if(!url) return msg('Analysiere zuerst ein Video für passende Vorschläge.', true);
  ['#suggestionGrid','#suggestionPageGrid'].forEach(sel=>{if($(sel)) $(sel).innerHTML='<p class="muted">Lade Vorschläge …</p>';});
  try{
    const r=await fetch(`/api/youtube/suggestions?limit=8&url=${encodeURIComponent(url)}`); const data=await r.json();
    if(!r.ok) throw new Error(data.detail||'Vorschläge konnten nicht geladen werden');
    state.suggestions=(data.items||[]).filter(x=>x.url); renderSuggestionTargets();
  }catch(e){ state.suggestions=[]; renderSuggestionTargets(); msg(e.message,true); }
}

function openSuggestion(url){
  if(!url) return; $('#url').value=url; setView('downloader'); window.scrollTo({top:0,behavior:'smooth'}); analyze();
}


async function retryJob(id){
  try{
    const r=await fetch(`/api/youtube/jobs/${id}/retry`,{method:'POST'});
    const job=await r.json(); if(!r.ok) throw new Error(job.detail||'Retry fehlgeschlagen');
    updateJobUI(job); pollJob(job.id); msg('Retry gestartet.');
  }catch(e){ msg(e.message,true); }
}

function applyPreset(name){
  const presets={
    'mp4-1080':{mode:'video',container:'mp4',quality:'1080'},
    'mp4-best':{mode:'video',container:'mp4',quality:'best'},
    'mp3-320':{mode:'audio',container:'mp3',quality:'320'},
    'm4a-256':{mode:'audio',container:'m4a',quality:'256'}
  };
  const p=presets[name]; if(!p) return;
  state.mode=p.mode;
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.mode===state.mode));
  fillContainer();
  $('#container').value=p.container;
  if([...$('#quality').options].some(o=>o.value===p.quality)) $('#quality').value=p.quality;
  msg(`Preset aktiv: ${p.container.toUpperCase()} · ${p.quality==='best'?'Beste Qualität':p.quality+(p.mode==='video'?'p':' kbps')}`);
}


function setRuntimeCheck(id, info){
  const el=$(id);
  if(!el) return;
  el.textContent=info?.ok ? '✓ Bereit' : '✕ Fehler';
  el.classList.toggle('runtime-ok', !!info?.ok);
  el.classList.toggle('runtime-bad', !info?.ok);
}

function renderRuntime(data, tested=false){
  const checks=data?.checks || {};
  setRuntimeCheck('#rtYtdlp', checks.yt_dlp);
  setRuntimeCheck('#rtFfmpeg', checks.ffmpeg);
  setRuntimeCheck('#rtDownloads', checks.downloads_writable);
  setRuntimeCheck('#rtDatabase', checks.database_writable);
  setRuntimeCheck('#rtYoutubeAuth', checks.youtube_auth);
  setRuntimeCheck('#rtUserAgent', checks.browser_fingerprint);

  if($('#rtYtdlpVersion')) $('#rtYtdlpVersion').textContent=checks.yt_dlp?.version || '–';
  if($('#rtFfmpegVersion')) $('#rtFfmpegVersion').textContent=checks.ffmpeg?.version || '–';
  if($('#rtDownloadPath')) $('#rtDownloadPath').textContent=checks.downloads_writable?.path || '–';
  if($('#rtDatabasePath')) $('#rtDatabasePath').textContent=checks.database_writable?.path || '–';
  if($('#rtYoutubeAuthSource')) $('#rtYoutubeAuthSource').textContent=checks.youtube_auth?.ok ? `Secret aktiv · ${checks.youtube_auth?.source || 'configured'} · ${checks.youtube_auth?.normalized_rows || 0} Cookies normalisiert` : (checks.youtube_auth?.configured ? 'Cookie-Datei gefunden · Normalisierung fehlgeschlagen' : 'Kein Cookie-Secret gefunden');
  if($('#rtUserAgentPreview')) $('#rtUserAgentPreview').textContent=checks.browser_fingerprint?.ok ? (checks.browser_fingerprint?.preview || 'Mozilla/5.0 …') : (checks.browser_fingerprint?.configured ? 'User-Agent gesetzt · Format prüfen' : 'YOUTUBE_USER_AGENT fehlt');
  if($('#rtVersion')) $('#rtVersion').textContent=`v${data?.version || '0.6.4'}`;
  if($('#rtFree')) $('#rtFree').textContent=data?.storage?.free_text || '–';
  if($('#rtJobs')) $('#rtJobs').textContent=String(data?.active_jobs ?? 0);

  const allOk=Object.values(checks).every(x=>x?.ok);
  if($('#rtStatus')){
    $('#rtStatus').textContent=allOk?'Alles grün':'Prüfung nötig';
    $('#rtStatus').className=allOk?'runtime-ok':'runtime-bad';
  }
  if(tested && $('#runtimeMessage')){
    const failures=data?.self_test?.failures || [];
    $('#runtimeMessage').textContent=failures.length
      ? `Self-Test abgeschlossen: ${failures.join(', ')} nicht bereit.`
      : 'Self-Test bestanden: Runtime, FFmpeg, yt-dlp, Downloads, Datenbank und YouTube-Auth sind bereit.';
    $('#runtimeMessage').className=`runtime-message ${failures.length?'bad':'good'}`;
  }
}

async function loadRuntime(){
  try{
    const r=await fetch('/api/runtime');
    const data=await r.json();
    if(!r.ok) throw new Error(data.detail||'Runtime konnte nicht geladen werden');
    renderRuntime(data,false);
  }catch(e){
    if($('#runtimeMessage')) {
      $('#runtimeMessage').textContent=e.message;
      $('#runtimeMessage').className='runtime-message bad';
    }
  }
}

async function runRuntimeSelfTest(){
  const btn=$('#runSelfTest');
  if(btn){btn.disabled=true;btn.textContent='Prüfe …';}
  try{
    const r=await fetch('/api/runtime/self-test',{method:'POST'});
    const data=await r.json();
    if(!r.ok) throw new Error(data.detail||'Self-Test fehlgeschlagen');
    renderRuntime(data,true);
  }catch(e){
    if($('#runtimeMessage')){
      $('#runtimeMessage').textContent=e.message;
      $('#runtimeMessage').className='runtime-message bad';
    }
  }finally{
    if(btn){btn.disabled=false;btn.textContent='▶ Self-Test starten';}
  }
}

async function saveSettings(){
  const payload={default_mode:state.mode,default_video_container:state.mode==='video'?$('#container').value:(state.settings?.default_video_container||'mp4'),default_audio_container:state.mode==='audio'?$('#container').value:(state.settings?.default_audio_container||'mp3'),default_video_quality:state.mode==='video'?$('#quality').value:(state.settings?.default_video_quality||'best'),default_audio_bitrate:state.mode==='audio'?Number($('#quality').value):(state.settings?.default_audio_bitrate||320),embed_thumbnail:$('#thumb').checked,write_metadata:$('#metadata').checked};
  try{ const r=await fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); if(r.ok){ state.settings=await r.json(); msg('Einstellungen gespeichert.'); } }catch(_e){}
}

$('#analyzeBtn').addEventListener('click',analyze); $('#url').addEventListener('keydown',e=>{if(e.key==='Enter') analyze();}); $('#downloadBtn').addEventListener('click',startDownload); $('#queueBtn').addEventListener('click',addToQueue);
document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));btn.classList.add('active');state.mode=btn.dataset.mode;fillContainer();}));
document.querySelectorAll('.nav[data-target]').forEach(btn=>btn.addEventListener('click',()=>setView(btn.dataset.target)));
document.addEventListener('click',e=>{
  const id=e.target?.dataset?.cancel; if(id) cancelJob(id); const retry=e.target?.dataset?.retry; if(retry) retryJob(retry);
  const suggestion=e.target.closest?.('[data-suggestion-url]'); if(suggestion) openSuggestion(suggestion.dataset.suggestionUrl);
  const single=e.target.closest?.('[data-single-index]'); if(single) startSinglePlaylistItem(single.dataset.singleIndex);
});
$('#playlistEntries')?.addEventListener('change',e=>{ if(e.target.matches('.playlist-checkbox')){ const row=e.target.closest('[data-playlist-index]'); const idx=Number(row.dataset.playlistIndex); if(e.target.checked) state.playlistSelection.add(idx); else state.playlistSelection.delete(idx); updatePlaylistSelectionUI(); }});
$('#playlistSelectAll')?.addEventListener('change',e=>{ const entries=state.info?.entries||[]; state.playlistSelection=e.target.checked?new Set(entries.map((_x,i)=>i+1)):new Set(); updatePlaylistSelectionUI(); });
$('#playlistInvert')?.addEventListener('click',()=>{ const entries=state.info?.entries||[]; state.playlistSelection=new Set(entries.map((_x,i)=>i+1).filter(i=>!state.playlistSelection.has(i))); updatePlaylistSelectionUI(); });
$('#playlistOnlySelected')?.addEventListener('click',()=>{ if(!state.playlistSelection.size) return msg('Wähle mindestens ein Playlist-Video aus.',true); addToQueue(); });
$('#saveSettingsBtn').addEventListener('click',saveSettings);
$('#refreshSuggestions')?.addEventListener('click',loadSuggestions); $('#pageRefreshSuggestions')?.addEventListener('click',loadSuggestions); $('#sideRefreshSuggestions')?.addEventListener('click',loadSuggestions);
$('#historyStatus')?.addEventListener('change',renderHistory); $('#historyType')?.addEventListener('change',renderHistory); $('#runSelfTest')?.addEventListener('click',runRuntimeSelfTest); document.querySelectorAll('[data-preset]').forEach(btn=>btn.addEventListener('click',()=>applyPreset(btn.dataset.preset)));

fillContainer(); loadSettings(); loadHistory();
