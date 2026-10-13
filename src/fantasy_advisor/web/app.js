const $ = (selector) => document.querySelector(selector);
const money = (value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? `${new Intl.NumberFormat('es-ES').format(Math.round(Number(value)))} €` : 'n/d';
const number = (value) => typeof value === 'number' ? new Intl.NumberFormat('es-ES').format(value) : 'n/d';
const average = (value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? new Intl.NumberFormat('es-ES',{minimumFractionDigits:1,maximumFractionDigits:1}).format(Number(value)) : 'n/d';
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const posCode = {Portero:'POR', Defensa:'DEF', Medio:'MED', Delantero:'DEL', Entrenador:'ENT'};
const posTone = {Portero:'por', Defensa:'def', Medio:'med', Delantero:'del', Entrenador:'ent'};
const positionClass = (position) => `pos-${posTone[position]||'other'}`;
const WATCHLIST_KEY='fantasy-advisor-price-watchlist-v1';
const RESERVE_KEY='fantasy-advisor-budget-reserve-v1';
const BENCH_KEY='fantasy-advisor-reserves-v1';
const DASHBOARD_CACHE_KEY='fantasy-advisor-dashboard-cache-v2';
const formations = {'3-4-3':[1,3,4,3],'3-5-2':[1,3,5,2],'4-3-3':[1,4,3,3],'4-4-2':[1,4,4,2],'4-5-1':[1,4,5,1],'5-3-2':[1,5,3,2],'5-4-1':[1,5,4,1],'3-6-1':[1,3,6,1],'3-3-4':[1,3,3,4],'4-2-4':[1,4,2,4],'4-6-0':[1,4,6,0],'5-2-3':[1,5,2,3]};
let data = null;
let dashboardLoading = false;
let selectedFixture = 0;
let selectedIds = [];
let captainId = null;
let pendingPlayer = null;
let suppressDragClickUntil = 0;
let currentFormation = '4-3-3';
let activityRows = [];
let activityPage = 0;
let activityVisible = 14;
let activityHistoryComplete = false;
let activityScanPromise = null;
let reserveIds = (()=>{try{return JSON.parse(localStorage.getItem(BENCH_KEY)||'{}')}catch{return {}}})();
let radarVisible = 12;
const playerDetailCache = new Map();

function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2300)}
function watchlist(){try{return JSON.parse(localStorage.getItem(WATCHLIST_KEY)||'{}')}catch{return {}}}
function toggleWatch(id){const items=watchlist(),key=String(id),player=dashboardPlayer(key);if(items[key]){delete items[key];toast('Jugador quitado de favoritos.')}else{items[key]={name:player?.name||'Jugador',lastValue:player?.ffValue??player?.ff?.value??null};toast('Jugador añadido a favoritos.')}localStorage.setItem(WATCHLIST_KEY,JSON.stringify(items));renderDashboard();if(data)renderBuilder()}
function watchButton(id){const active=Boolean(watchlist()[String(id)]);return `<button type="button" class="watch-toggle ${active?'watching':''}" data-watch-id="${esc(id)}" aria-label="${active?'Quitar de favoritos':'Añadir a favoritos'}" title="${active?'En favoritos':'Añadir a favoritos'}">${active?'★':'☆'}</button>`}
function trendMatches(player,filter){if(filter==='all')return true;const trend=player.dailyChange!=null?Number(player.dailyChange):player.trend7d!=null?Number(player.trend7d):null;if(trend==null)return false;return filter==='up'?trend>0:trend<0}
function checkWatchlistAlerts(){const items=watchlist(),all=[...(data?.market||[]),...(data?.roster||[]),...(data?.clauseRadar||[]),...(data?.sellRecommendations||[]),...(data?.rivals||[]).flatMap(r=>r.roster||[])],alerts=[];for(const [id,item] of Object.entries(items)){const player=all.find(p=>String(p.masterId)===id),value=player?.ffValue??player?.ff?.value;if(value==null||item.lastValue==null||Number(value)===Number(item.lastValue))continue;const delta=Number(value)-Number(item.lastValue);alerts.push(`${item.name} ${delta>0?'↑':'↓'} ${money(Math.abs(delta))}`);item.lastValue=value}if(alerts.length){localStorage.setItem(WATCHLIST_KEY,JSON.stringify(items));toast(`Cambios FF: ${alerts.slice(0,3).join(' · ')}${alerts.length>3?` y ${alerts.length-3} más`:''}`)}}
function budgetReserve(){const amount=Number(localStorage.getItem(RESERVE_KEY));return Number.isFinite(amount)&&amount>0?amount:0}
function saveBudgetReserve(value){const amount=Math.max(0,Number(value)||0);localStorage.setItem(RESERVE_KEY,String(amount));renderBudgetPlanner()}
function playerById(id){return data?.roster.find(p=>p.id===String(id))}
function initials(name){return String(name||'').split(/\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase()}
function portrait(url,name,cls='',clubImage=null){const photo=url?`<img class="${cls}" src="${esc(url)}" alt="Foto de ${esc(name)}" loading="lazy" onload="this.parentElement.querySelector('.portrait-fallback')?.style.setProperty('display','none')" onerror="this.style.display='none';this.parentElement.querySelector('.portrait-fallback')?.style.setProperty('display','grid')">`:'';const crest=clubImage?`<img class="club-crest" src="${esc(clubImage)}" alt="" aria-hidden="true" loading="lazy" onerror="this.remove()">`:'';return photo+crest}
function showError(message){const banner=$('#error-banner');if(!banner)return;banner.textContent=message;banner.classList.remove('hidden')}
function setLoadingOverlay(visible,message='Actualizando tu liga…'){
  const overlay=$('#loading-overlay');if(!overlay)return;
  $('#loading-message').textContent=message;overlay.classList.toggle('hidden',!visible);
  overlay.setAttribute('aria-busy',String(visible));document.body.classList.toggle('is-loading',visible);
}
function readDashboardCache(){try{const saved=JSON.parse(localStorage.getItem(DASHBOARD_CACHE_KEY)||'null');return saved?.version===2&&Array.isArray(saved.data?.roster)&&Array.isArray(saved.data?.market)?saved:null}catch{return null}}
function saveDashboardCache(snapshot){try{localStorage.setItem(DASHBOARD_CACHE_KEY,JSON.stringify({version:2,savedAt:Date.now(),data:snapshot}))}catch(error){console.warn('No se pudo guardar la copia local del panel:',error)}}
function showCachedDashboard(){
  const saved=readDashboardCache();if(!saved)return false;
  data=saved.data;activityRows=data.activity||[];activityPage=Number(data.activityLastPage)||0;activityVisible=14;activityHistoryComplete=Boolean(data.activityComplete);
  renderDashboard();useRecommended(false);
  const savedAt=new Date(saved.savedAt);$('#updated-at').textContent=`Datos guardados · ${savedAt.toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'})}`;
  return true;
}

async function loadDashboard(force=false,background=false){
  if(dashboardLoading)return;dashboardLoading=true;
  $('#refresh').classList.add('busy');
  if(!background)setLoadingOverlay(true,data?'Actualizando tu liga…':'Preparando tu equipo…');
  try{
    if(force)await fetch('/api/refresh',{method:'POST'});
    let res=await fetch('/api/dashboard',{cache:'no-store'});let payload=await res.json();
    while(res.status===202){$('#updated-at').textContent='Consultando liga y fuentes…';$('#loading-message').textContent='Sincronizando plantilla, mercado y precios…';await new Promise(resolve=>setTimeout(resolve,900));res=await fetch('/api/dashboard',{cache:'no-store'});payload=await res.json()}
    if(!res.ok)throw new Error(payload.error||'No se pudieron cargar los datos.');
    let draft=null;try{const draftRes=await fetch('/api/draft',{cache:'no-store'});if(draftRes.ok)draft=await draftRes.json()}catch{}
    data=payload;saveDashboardCache(data);activityRows=data.activity||[];activityPage=Number(data.activityLastPage)||0;activityVisible=14;activityHistoryComplete=Boolean(data.activityComplete);activityScanPromise=null;renderDashboard();checkWatchlistAlerts();
    if(draft.player_ids?.length){$('#formation').value=draft.formation;currentFormation=draft.formation;selectedIds=draft.player_ids.map(id=>id&&playerById(String(id))?String(id):null).slice(0,11);captainId=draft.captain_id&&selectedIds.includes(String(draft.captain_id))?String(draft.captain_id):null;$('#draft-status').textContent=`Guardado ${new Date(draft.updated_at).toLocaleString('es-ES',{dateStyle:'short',timeStyle:'short'})}`;renderBuilder()}
    else if(!selectedIds.some(Boolean))useRecommended(false);else renderBuilder();
    void loadAllActivity();
    $('#error-banner').classList.add('hidden');
    $('#updated-at').textContent=`Actualizado ${new Date(data.updatedAt).toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'})}`;
  }catch(error){
    console.error('No se pudo actualizar el panel:',error);
    showError(data?`No se pudo actualizar. Conservamos los datos guardados: ${error.message||'error de conexión'}`:`No se pudieron cargar los datos de la liga: ${error.message||'comprueba la sesión y vuelve a actualizar.'}`);
    if(!data){const leagueGrid=$('#league-market-grid'),managerGrid=$('#manager-market-grid');if(leagueGrid)leagueGrid.innerHTML='<div class="empty-state">No se pudieron cargar los datos. Comprueba la sesión y vuelve a actualizar.</div>';if(managerGrid)managerGrid.innerHTML=''}
  }
  finally{dashboardLoading=false;if(!background)setLoadingOverlay(false);$('#refresh').classList.remove('busy')}
}

function renderDashboard(){
  $('#league-name').textContent=data.league;
  $('#updated-at').textContent=`Actualizado ${new Date(data.updatedAt).toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'})}`;
  $('#hero-manager-name').textContent=data.ownManagerName?.trim()||'de nuevo';
  $('.avatar').textContent=initials(data.ownManagerName||'Mi equipo');
  $('#balance').textContent=money(data.balance);
  $('#overview-activity-list').innerHTML=(data.activity||[]).slice(0,5).map(row=>`<div class="movement-row"><span><b>${esc(row.player||'Jugador')}</b><small>${esc(row.operation||'Movimiento')} · ${esc(row.manager||'Liga')}${row.otherManager?` ↔ ${esc(row.otherManager)}`:''}</small></span><strong>${money(row.amount)}</strong></div>`).join('')||'<div class="movement-empty">Todavía no hay movimientos publicados.</div>';
  $('#overview-position').textContent=data.teamPosition?`#${data.teamPosition}`:'n/d';
  $('#overview-value').textContent=money(data.teamValue);
  $('#overview-points').textContent=data.fixturePoints==null?'n/d':`${number(data.fixturePoints)} pts`;
  $('#overview-market').textContent=`${(data.market||[]).length} jugadores`;
  renderFixtures();
  const fieldCount=data.roster.filter(p=>p.position!=='Entrenador').length;const coachCount=data.roster.length-fieldCount;
  $('#bench-count').textContent=`${fieldCount} jugadores${coachCount?` · ${coachCount} entrenadores`:''}`;
  renderRecommendations();renderMarket();renderRivals();renderActivity();renderClauseRadar();renderSales();renderFavorites();renderBudgetPlanner();
}

function renderSquadTotals(){
  if(!data)return;
  const movement=data.teamPriceMovement||{},delta=movement.dailyChange;
  const summary=`<div><small>PLANTILLA COMPLETA · JUGADORES Y ENTRENADORES</small><b>${money(data.teamValue)}</b><span title="${esc(movement.source||'Variación estimada')}" class="${delta==null?'muted':delta>0?'trend-up':delta<0?'trend-down':'muted'}">${delta==null?'Cambio diario n/d':`${movement.source?.startsWith('Suma parcial')?'≈ ':''}${delta>0?'↑':delta<0?'↓':'•'} ${money(Math.abs(delta))} / día`}</span></div>`;
  const xi=$('#xi-value-summary'),rest=$('#bench-value-summary');if(xi)xi.innerHTML=summary;if(rest)rest.innerHTML='';
}

function renderBudgetPlanner(){
  if(!data)return;const root=$('#budget-planner');if(!root)return;
  const reserve=budgetReserve(),balance=Number(data.balance)||0,available=Math.max(0,balance-reserve),premium=data.leaguePremiums||{};
  const picks=(data.market||[]).filter(p=>p.recommended&&p.recommendedBidCeiling!=null).slice(0,3);
  root.innerHTML=`<section class="budget-panel budget-panel-simple"><div class="budget-head"><div><span class="eyebrow">TU PRESUPUESTO</span><h3>Saldo y pujas sugeridas</h3></div><label>RESERVAR <input id="budget-reserve" type="number" min="0" step="100000" value="${reserve}" aria-label="Saldo que quieres reservar"></label></div><div class="budget-stats"><article><small>SALDO</small><b>${money(data.balance)}</b></article><article><small>TRAS RESERVA</small><b>${money(available)}</b></article><article><small>SOBREPRECIO MEDIANO LIGA</small><b>${premium.medianPct==null?'n/d':`${premium.medianPct>0?'+':''}${average(premium.medianPct)}%`}</b><em>${premium.sample||0} operaciones</em></article></div><p class="budget-explainer">Las pujas se calculan por precio de mercado, compras observadas, calidad y tendencia. El saldo se muestra aparte y no reduce el objetivo recomendado.</p><div class="budget-picks">${picks.map(p=>`<button type="button" class="budget-pick" data-budget-player="${esc(p.masterId)}"><span>★ ${esc(p.name)}<small>${p.bidGuidance?.reason?esc(p.bidGuidance.reason):'Oportunidad recomendada'}</small></span><b>${money(p.recommendedBidTarget??p.recommendedBidCeiling)}<small>objetivo</small></b></button>`).join('')||'<span class="muted">Sin compras recomendadas con los datos actuales.</span>'}</div></section>`;
  $('#budget-reserve')?.addEventListener('change',event=>saveBudgetReserve(event.target.value));root.querySelectorAll('[data-budget-player]').forEach(button=>button.addEventListener('click',()=>openPlayer(button.dataset.budgetPlayer)));
}

function renderRecommendations(){
  const report=data.report||{};const lineup=report.lineup;const rising=(report.rising||[]).length;const falling=(report.falling||[]).length;const bargains=(data.market||[]).filter(p=>p.recommended).length;
  const formation=lineup?.formation?.join('-')||'—';
  const items=[
    {icon:'✦',tone:'',label:'XI RECOMENDADO',title:lineup?`Formación ${formation}`:'Alineación pendiente',copy:lineup?`${lineup.players.length}/11 jugadores · ${lineup.complete?'once completo':'falta cubrir '+Object.entries(lineup.missing||{}).map(([position,count])=>`${count} ${position.toLowerCase()}`).join(' o ')}.`:'Sin datos suficientes de posibles alineaciones.'},
    {icon:'↗',tone:'purple',label:'TENDENCIA DE TU PLANTILLA',title:rising?`${rising} jugador${rising===1?'':'es'} al alza`:falling?`${falling} jugador${falling===1?'':'es'} a revisar`:'Plantilla estable',copy:rising?'Hay jugadores propios que suben de valor; buena señal para vigilar su evolución.':falling?'Algunos valores caen: revisa puntos, minutos y necesidad antes de decidir.':'Sin variaciones destacadas de ±5% esta semana.'},
    {icon:'⌕',tone:'blue',label:'OPORTUNIDADES EN MERCADO',title:bargains?`${bargains} candidato${bargains===1?'':'s'} priorizado${bargains===1?'':'s'}`: 'Revisar el mercado',copy:bargains?'Prioridad por XI, precio, sobreprecio de liga, puntos y tendencia.':`${data.market.length} jugadores disponibles. Compara precio, puntos y posición.`},
  ];
  $('#recommendations').innerHTML=items.map(i=>`<article class="recommend-card"><div class="rec-icon ${i.tone}">${i.icon}</div><div class="rec-content"><div class="rec-label">${i.label}</div><div class="rec-title">${esc(i.title)}</div><div class="rec-copy">${esc(i.copy)}</div></div></article>`).join('');
}

function renderMarket(){
  const q=$('#market-search').value.trim().toLocaleLowerCase('es');const pos=$('#position-filter').value;const sort=$('#market-sort').value;
  const trend=$('#market-trend-filter')?.value||'all';let rows=data.market.filter(p=>(!q||p.name.toLocaleLowerCase('es').includes(q)||p.club.toLocaleLowerCase('es').includes(q))&&(!pos||p.position===pos)&&trendMatches(p,trend));
  if(sort==='trend')rows.sort((a,b)=>(b.trend7d??-999)-(a.trend7d??-999));else if(sort==='points')rows.sort((a,b)=>(b.points??-1)-(a.points??-1));else if(sort==='value')rows.sort((a,b)=>(a.asking??a.value??0)-(b.asking??b.value??0));
  $('#market-count').textContent=`${rows.length} jugadores`;
  const leagueRows=rows.filter(p=>p.listingType==='marketPlayerLeague');const managerRows=rows.filter(p=>p.listingType==='marketPlayerTeam');
  $('#league-market-count').textContent=`${leagueRows.length}`;$('#manager-market-count').textContent=`${managerRows.length}`;
  const card=p=>{
    const daily=typeof p.dailyChange==='number'?`${p.dailyChange>0?'↑':p.dailyChange<0?'↓':'•'} ${money(Math.abs(p.dailyChange))} · ${p.dailyPct==null?'':`${p.dailyPct>0?'+':''}${p.dailyPct.toFixed(1)}%`} · 24 h`:'Cambio diario n/d';
    const trendClass=(p.dailyChange??0)>0?'trend-up':(p.dailyChange??0)<0?'trend-down':'';
    const owner=p.owner?`<span class="owner-badge">En venta por ${esc(p.owner)}</span>`:'<span class="owner-badge free">Puja al mercado de LALIGA</span>';
    const star=p.recommended?'<span class="recommended-star" title="Compra recomendada">★ RECOMENDADO</span>':'';
    const bid=p.recommended&&p.recommendedBidCeiling!=null?`<div class="bid-guide bid-guide-compact"><small>${p.recommendedBidFloor===p.recommendedBidCeiling?'PUJA ORIENTATIVA':'RANGO SUGERIDO'}</small><b>${p.recommendedBidFloor===p.recommendedBidCeiling?money(p.recommendedBidCeiling):`${money(p.recommendedBidTarget??p.recommendedBidFloor)} – ${money(p.recommendedBidCeiling)}`}</b><span class="bid-evidence">${esc(p.bidGuidance?.confidence||'Baja')} · ${esc(p.bidGuidance?.historicalSample||0)} operaciones comparables</span></div>`:'';
    const clause=p.owner?`<div class="clause-card">Cláusula ${money(p.buyoutClause)}${p.shielded?' · protegido':''}</div>`:'';
    const priceLabel=p.owner?'PRECIO DE SALIDA':'PRECIO LALIGA';
    return `<article class="player-card market-card ${p.recommended?'is-recommended':''}" role="button" tabindex="0" data-player-id="${esc(p.masterId)}" aria-label="Ver ficha de ${esc(p.name)}"><div class="player-card-top"><span class="position-tag ${positionClass(p.position)}">${posCode[p.position]||'—'}</span>${watchButton(p.masterId)}<span class="trend-tag ${trendClass}">${esc(daily)}</span></div><div class="player-main"><div class="player-art">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback">${esc(initials(p.name))}</span></div><div><div class="player-name">${esc(p.name)}</div><div class="player-club">${esc(p.club)} · ${number(p.points)} pts · media ${average(p.averagePoints)}</div></div></div>${owner}${star}<div class="player-price-row"><div><div class="price-label">${priceLabel}</div><div class="price-value">${money(p.asking??p.value)}</div></div></div>${clause}${bid}</article>`;
  };
  $('#league-market-grid').innerHTML=leagueRows.slice(0,60).map(card).join('')||'<div class="empty-state">No hay jugadores del mercado LALIGA con estos filtros.</div>';
  $('#manager-market-grid').innerHTML=managerRows.slice(0,60).map(card).join('')||'<div class="empty-state">No hay jugadores puestos en venta por managers con estos filtros.</div>';
  wirePlayerCards('#league-market-grid, #manager-market-grid');wireWatchButtons('#league-market-grid, #manager-market-grid');
}

function renderClauseRadar(){
  const trend=$('#radar-trend-filter')?.value||'all';const rows=[...(data.clauseRadar||[])].filter(p=>trendMatches(p,trend)).sort((a,b)=>{
    const now=Date.now();
    const aUntil=a.clauseLockedUntil?new Date(a.clauseLockedUntil).getTime():0,bUntil=b.clauseLockedUntil?new Date(b.clauseLockedUntil).getTime():0;
    const aLocked=Boolean(a.shielded||(Number.isFinite(aUntil)&&aUntil>now)),bLocked=Boolean(b.shielded||(Number.isFinite(bUntil)&&bUntil>now));
    if(aLocked!==bLocked)return aLocked?1:-1;
    const aNoClause=a.buyoutClause==null,bNoClause=b.buyoutClause==null;
    if(aNoClause!==bNoClause)return aNoClause?-1:1;
    const aTime=Number.isFinite(aUntil)?Math.max(0,aUntil-now):0,bTime=Number.isFinite(bUntil)?Math.max(0,bUntil-now):0;
    if(aLocked&&bLocked&&aTime!==bTime)return bTime-aTime;
    return (b.recommendation==='Recomendable')-(a.recommendation==='Recomendable')||(b.signals?.length||0)-(a.signals?.length||0);
  });$('#radar-count').textContent=`${Math.min(rows.length,radarVisible)} de ${rows.length}`;
  $('#clause-radar-grid').innerHTML=rows.slice(0,radarVisible).map(p=>{
    const daily=p.dailyChange==null?'Cambio diario n/d':`${p.dailyChange>0?'↑':p.dailyChange<0?'↓':'•'} ${money(Math.abs(p.dailyChange))} · ${p.dailyPct==null?'':`${p.dailyPct>0?'+':''}${p.dailyPct.toFixed(1)}%`} · 24 h`;
    const until=p.clauseLockedUntil?lockCountdown(p.clauseLockedUntil):'';
    const timeLocked=Boolean(p.clauseLockedUntil&&new Date(p.clauseLockedUntil).getTime()>Date.now());const blocked=Boolean(p.shielded||timeLocked);
    const lockBadge=`<span class="clause-lock ${blocked?'locked':'unlocked'}" title="${esc(p.shielded?'Jugador protegido':until||'Sin bloqueo informado')}">${p.shielded?'🔒 Protegido':blocked?'🔒 Bloqueado':'🔓 Disponible'}${timeLocked?` · ${esc(until.replace('Bloqueada ',''))}`:''}</span>`;
    const premium=p.premiumAmount==null?'':`${money(p.premiumAmount)} ${p.premiumPct==null?'':`(${p.premiumPct>0?'+':''}${p.premiumPct.toFixed(1)}%)`} sobre valor de liga`;
    const recovery=p.premiumAmount==null?'Datos insuficientes':p.premiumAmount<=0?'Sin sobreprecio':p.dailyChange>0?`≈ ${Math.ceil(p.premiumAmount/p.dailyChange)} días`:'No estimable con tendencia actual';
    const status=p.recommendation==='Recomendable'?'good':p.recommendation==='Vigilar'?'watch':'muted';
    return `<article class="insight-card" role="button" tabindex="0" data-player-id="${esc(p.masterId)}"><div class="insight-card-top"><span class="decision ${status}">${esc(p.recommendation)}</span><span class="trend-tag ${(p.dailyChange??0)>=0?'trend-up':'trend-down'}">${esc(daily)}</span></div><div class="insight-player"><div class="mini-portrait">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback">${esc(initials(p.name))}</span></div><div><b>${esc(p.name)}</b><small><span class="position-tag ${positionClass(p.position)}">${posCode[p.position]||'—'}</span> ${esc(p.club)} · ${esc(p.manager)}</small></div>${watchButton(p.masterId)}</div>${lockBadge}<div class="signal-tags">${(p.signals||[]).map(s=>`<span>${esc(s)}</span>`).join('')||'<span>Sin señales</span>'}</div><div class="insight-numbers"><div><small>CLÁUSULA</small><b>${money(p.buyoutClause)}</b><small>${esc(p.shielded?'Protegido':until||'Sin bloqueo informado')}</small></div><div><small>VALOR LIGA</small><b>${money(p.value)}</b><small>${esc(premium||'Diferencia no calculable')}</small></div></div><div class="recovery-mini"><small>RECUPERACIÓN DEL SOBREPRECIO · LINEAL</small><b>${esc(recovery)}</b><span>${money(p.premiumAmount)} ÷ ${p.dailyChange>0?`${money(p.dailyChange)} al día`:'variación diaria n/d'}</span></div><div class="insight-foot">${esc(p.recommendationReason)}</div></article>`;
  }).join('')||'<div class="empty-state">No hay señales de subida, rendimiento o titularidad en jugadores rivales.</div>';
  $('#radar-more').classList.toggle('hidden',rows.length<=radarVisible);
  wireInsightCards();
}

function renderSales(){
  const rows=(data.sellRecommendations||[]).filter(p=>trendMatches(p,$('#sell-trend-filter')?.value||'all'));$('#sell-count').textContent=`${rows.length} por revisar`;
  $('#sell-grid').innerHTML=rows.map(p=>`<article class="insight-card sale-card" role="button" tabindex="0" data-player-id="${esc(p.masterId)}"><div class="insight-card-top"><span class="decision bad">Evaluar venta</span><span class="trend-tag ${(p.dailyChange??0)>=0?'trend-up':'trend-down'}">${p.dailyChange==null?'FF n/d':`${p.dailyChange>0?'↑':'↓'} ${money(Math.abs(p.dailyChange))} · ${p.dailyPct==null?'':`${p.dailyPct>0?'+':''}${p.dailyPct.toFixed(1)}%`} / 24 h`}</span></div><div class="insight-player"><div class="mini-portrait">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback">${esc(initials(p.name))}</span></div><div><b>${esc(p.name)}</b><small><span class="position-tag ${positionClass(p.position)}">${posCode[p.position]||'—'}</span> ${esc(p.club)}</small></div>${watchButton(p.masterId)}</div><div class="insight-numbers"><div><small>TITULARIDAD</small><b>${p.probability==null?'n/d':`${number(p.probability)}%`}</b></div><div><small>MEDIA</small><b>${average(p.averagePoints)} pts</b></div><div><small>PRECIO ACTUAL</small><b>${money(p.value)}</b></div></div><div class="signal-tags">${p.signals.map(s=>`<span>${esc(s)}</span>`).join('')}</div></article>`).join('')||'<div class="empty-state">No hay señales suficientes para sugerir ventas ahora.</div>';
  wireInsightCards();
}

function renderFavorites(){
  const favorites=watchlist(),all=[...(data.market||[]),...(data.roster||[]),...(data.clauseRadar||[]),...(data.sellRecommendations||[]),...(data.rivals||[]).flatMap(r=>r.roster||[])];
  const unique=new Map();for(const p of all)if(p.masterId&&favorites[String(p.masterId)]){const key=String(p.masterId),previous=unique.get(key)||{},merged={...previous,...p};for(const field of ['buyoutClause','clauseLockedUntil','manager','owner','dailyChange','dailyPct','image','value'])if(merged[field]==null&&previous[field]!=null)merged[field]=previous[field];unique.set(key,merged)}
  const rows=[...unique.values()].filter(p=>trendMatches(p,$('#favorites-trend-filter')?.value||'all'));$('#favorites-count').textContent=`${rows.length} jugadores`;
  $('#favorites-grid').innerHTML=rows.map(p=>{const daily=p.dailyChange==null?'FF n/d':`${p.dailyChange>0?'↑':p.dailyChange<0?'↓':'•'} ${money(Math.abs(p.dailyChange))} · ${p.dailyPct==null?'':`${p.dailyPct>0?'+':''}${p.dailyPct.toFixed(1)}%`} / 24 h`;const own=data.roster.some(row=>String(row.masterId)===String(p.masterId));const clause=p.buyoutClause!=null?`<b>${money(p.buyoutClause)}</b>${p.clauseLockedUntil?`<small> ${esc(lockCountdown(p.clauseLockedUntil))}</small>`:''}`:own?'<b>No aplica · jugador propio</b>':p.manager||p.owner?'<b>No publicada</b>':'<b>Sin dueño · no aplica</b>';const premium=p.buyoutClause!=null&&p.value!=null?Number(p.buyoutClause)-Number(p.value):null;const recovery=premium==null?'':premium<=0?' · sin sobreprecio':p.dailyChange>0?` · recuperas diferencial en ≈ ${Math.ceil(premium/p.dailyChange)} días`:' · recuperación no estimable';const clauseHtml=`<div class="favorite-clause"><span>CLÁUSULA</span>${clause}<small class="favorite-recovery">${esc(recovery)}</small></div>`;return `<article class="player-card favorite-card" role="button" tabindex="0" data-player-id="${esc(p.masterId)}"><div class="player-card-top"><span class="position-tag ${positionClass(p.position)}">${posCode[p.position]||'—'}</span>${watchButton(p.masterId)}<span class="trend-tag ${(p.dailyChange??0)>=0?'trend-up':'trend-down'}">${esc(daily)}</span></div><div class="player-main"><div class="player-art">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback">${esc(initials(p.name))}</span></div><div><div class="player-name">${esc(p.name)}</div><div class="player-club">${esc(p.club||'—')} · ${esc(p.manager||p.owner||'')}</div></div></div><div class="player-price-row"><div><div class="price-label">PRECIO ACTUAL</div><div class="price-value">${money(p.value)}</div><small class="reference-difference">${p.dailyChange==null?'Rentabilidad diaria n/d':`Rentabilidad estimada ${p.dailyChange>0?'+':''}${money(p.dailyChange)} / día`}</small>${clauseHtml}</div></div></article>`}).join('')||'<div class="empty-state">Aún no hay favoritos. Pulsa ☆ en una tarjeta para seguir su precio.</div>';
  wirePlayerCards('#favorites-grid');wireWatchButtons('#favorites-grid');
}

function wireInsightCards(){wirePlayerCards('.insight-grid');wireWatchButtons('.insight-grid')}
function wirePlayerCards(selector){document.querySelectorAll(`:is(${selector}) [data-player-id]`).forEach(el=>{if(el.dataset.wired==='1')return;el.dataset.wired='1';el.addEventListener('click',event=>{if(event.target.closest('.watch-toggle'))return;const id=el.dataset.playerId;if(id)openPlayer(id)});el.addEventListener('keydown',event=>{if(event.target.closest('.watch-toggle'))return;if(event.key==='Enter'||event.key===' '){event.preventDefault();openPlayer(el.dataset.playerId)}})})}
function wireWatchButtons(selector){document.querySelectorAll(`:is(${selector}) .watch-toggle[data-watch-id]`).forEach(button=>{if(button.dataset.wired==='1')return;button.dataset.wired='1';button.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();toggleWatch(button.dataset.watchId)})})}
function lockCountdown(value){const ms=new Date(value).getTime()-Date.now();if(Number.isNaN(ms))return 'Bloqueo informado';if(ms<=0)return 'Disponible según fecha API';const hours=Math.ceil(ms/3600000);return hours>=24?`Bloqueada ${Math.floor(hours/24)} d ${hours%24} h`:`Bloqueada ${hours} h`;}

function historyChart(sourceRows,localRows){
  const points=[...(sourceRows||[]).map(row=>({date:row.date,value:row.value==null?NaN:Number(row.value),source:'FutbolFantasy'})),...(localRows||[]).map(row=>({date:row.observed_at,value:row.current_value==null?NaN:Number(row.current_value),source:'Captura local'}))].filter(p=>Number.isFinite(p.value)&&!Number.isNaN(new Date(p.date).getTime())).sort((a,b)=>new Date(a.date)-new Date(b.date));
  const distinct=points.filter((point,index)=>index===0||new Date(point.date).toDateString()!==new Date(points[index-1].date).toDateString());
  if(!distinct.length)return '<div class="chart-empty">Todavía no hay valores históricos disponibles.</div>';
  if(distinct.length===1)return `<div class="chart-empty">Una observación real: ${money(distinct[0].value)} · ${dateLabel(distinct[0].date)}. Se ampliará al guardar nuevos valores.</div>`;
  const W=680,H=210,L=70,R=20,T=18,B=42,values=distinct.map(p=>p.value),min=Math.min(...values),max=Math.max(...values),range=max-min||1;
  const first=new Date(distinct[0].date).getTime(),last=new Date(distinct.at(-1).date).getTime(),span=last-first||1;
  const xy=distinct.map(p=>({x:L+(new Date(p.date).getTime()-first)*(W-L-R)/span,y:T+(max-p.value)*(H-T-B)/range,p}));
  const line=xy.map(p=>`${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),area=`${L},${H-B} ${line} ${W-R},${H-B}`;
  const guides=[0,1,2,3].map(i=>{const y=T+i*(H-T-B)/3,val=max-i*range/3;return `<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" class="chart-gridline"/><text x="${L-8}" y="${y+4}" text-anchor="end">${money(val)}</text>`}).join('');
  const dots=xy.map(({x,y,p},index)=>{const previous=distinct[index-1],delta=previous?p.value-previous.value:null,pct=previous&&previous.value?delta/previous.value*100:null;const change=delta==null?'Sin punto anterior':`${delta>0?'+':''}${money(delta)} (${pct>0?'+':''}${pct?.toFixed(2)}%)`;const label=`${dateLabel(p.date)} · ${money(p.value)} · ${change} · ${p.source}`;return `<circle cx="${x}" cy="${y}" r="12" class="chart-hit" tabindex="0" role="graphics-symbol" aria-label="${esc(label)}" data-chart-tip="${esc(label)}"/><circle cx="${x}" cy="${y}" r="5" class="chart-point" aria-hidden="true"/>`}).join('');
  return `<div class="history-chart-wrap"><svg class="history-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Historial de valores reales de FutbolFantasy">${guides}<polygon points="${area}" class="chart-area"/><polyline points="${line}" class="chart-line"/>${dots}<text x="${L}" y="${H-12}">${esc(new Date(distinct[0].date).toLocaleDateString('es-ES',{day:'2-digit',month:'short'}))}</text><text x="${W-R}" y="${H-12}" text-anchor="end">${esc(new Date(distinct.at(-1).date).toLocaleDateString('es-ES',{day:'2-digit',month:'short'}))}</text></svg><div class="chart-tooltip" role="status" aria-live="polite"></div></div>`;
}

function wireHistoryTooltip(){
  const wrap=$('.history-chart-wrap'),tooltip=wrap?.querySelector('.chart-tooltip'),svg=wrap?.querySelector('.history-chart');
  if(!wrap||!tooltip||!svg)return;
  const show=point=>{if(!point)return;tooltip.textContent=point.dataset.chartTip||'';tooltip.classList.add('visible');const wrapRect=wrap.getBoundingClientRect(),pointRect=point.getBoundingClientRect(),tipWidth=tooltip.offsetWidth;const x=pointRect.left+pointRect.width/2-wrapRect.left,y=pointRect.top-wrapRect.top;tooltip.style.left=`${Math.max(tipWidth/2+8,Math.min(wrapRect.width-tipWidth/2-8,x))}px`;tooltip.style.top=`${Math.max(8,y-8)}px`};
  const hide=()=>tooltip.classList.remove('visible');
  svg.addEventListener('pointerover',event=>{const point=event.target.closest('.chart-hit');if(point)show(point)});
  svg.addEventListener('pointerout',event=>{if(event.target.closest('.chart-hit'))hide()});
  svg.addEventListener('focusin',event=>{const point=event.target.closest('.chart-hit');if(point)show(point)});
  svg.addEventListener('focusout',hide);
}

function dashboardPlayer(id){return [...(data?.market||[]),...(data?.roster||[]),...(data?.clauseRadar||[]),...(data?.sellRecommendations||[]),...(data?.rivals||[]).flatMap(r=>r.roster||[])].find(p=>String(p.masterId)===String(id))}
function shortDate(value){const d=new Date(`${value}T12:00:00`);return Number.isNaN(d.getTime())?value:d.toLocaleDateString('es-ES',{day:'2-digit',month:'short'})}
function renderPlayerDetail(p){
  const rec=p.recommendation||{},stats=p.transactionStats||{},ff=p.ff||{},priceStats=p.priceStats30d||{};
  const salePrice=p.asking!=null&&p.asking!==p.value?p.asking:null;
  const ownedPlayer=(data?.roster||[]).find(row=>String(row.masterId)===String(p.id));
  const rivalPurchase=!ownedPlayer&&p.owner&&p.purchase?.amount!=null?p.purchase.amount:null;
  const purchasePrice=ownedPlayer?(p.purchase?.amount??ownedPlayer.purchase?.amount??null):rivalPurchase??(p.owner?null:(p.asking??p.value));
  const profit=purchasePrice!=null&&p.value!=null?Number(p.value)-Number(purchasePrice):null;
  const premium=profit==null?null:Math.max(0,-profit);
  const recoveryDays=premium===0?0:premium!=null&&ff.dailyChange>0?Math.ceil(premium/ff.dailyChange):null;
  const recoveryHtml=premium==null?'<div class="recovery-card muted"><b>Recuperación no calculable</b><small>Falta un coste de compra o valor de mercado verificable.</small></div>':premium===0?`<div class="recovery-card recovered"><b>${ownedPlayer?'0 días · inversión recuperada':'0 días · sin sobreprecio'}${ownedPlayer&&profit>0?` · beneficio actual ${money(profit)}`:''}</b><small>${ownedPlayer?`Coste ${money(purchasePrice)} · ${esc(p.purchase?.confidence||ownedPlayer.purchase?.confidence||'Coste estimado')}`:'La compra no parte con una diferencia que recuperar.'}</small></div>`:recoveryDays!=null?`<div class="recovery-card"><div><small>DÍAS PARA RECUPERAR LA INVERSIÓN</small><b>≈ ${recoveryDays} días</b></div><span>${money(premium)} pendientes ÷ ${money(ff.dailyChange)} de subida diaria</span><small>Inversión ${money(purchasePrice)} · precio actual ${money(p.value)}. Proyección lineal con el cambio FF de 24 h; no garantiza que continúe.</small></div>`:`<div class="recovery-card muted"><b>Sin recuperación estimable con la tendencia actual</b><small>${ff.dailyChange==null?'FutbolFantasy no publica variación diaria para este jugador.':'El precio no está subiendo ahora.'} Coste estimado ${money(purchasePrice)} · valor actual ${money(p.value)}.</small></div>`;
  const clause=p.owner?`<div class="detail-stat"><small>DUEÑO</small><b>${esc(p.owner)}</b></div><div class="detail-stat"><small>CLÁUSULA</small><b>${money(p.buyoutClause)}</b>${p.clausePremiumAmount!=null?`<small>${money(p.clausePremiumAmount)} (${p.clausePremiumPct>0?'+':''}${p.clausePremiumPct?.toFixed(1)}%) vs valor liga</small>`:''}${p.clauseLockedUntil?`<small>${esc(lockCountdown(p.clauseLockedUntil))}</small>`:''}${p.shielded?'<small>Jugador protegido</small>':''}</div>`:'<div class="detail-stat"><small>ORIGEN</small><b>Mercado LALIGA</b></div>';
  const transactionRows=(p.transactions||[]).map(t=>`<div class="transaction-row"><div><b>${esc(t.operation||'Movimiento sin tipo confirmado')}</b><small>${esc(t.manager)}${t.otherManager?` ↔ ${esc(t.otherManager)}`:''} · Evento #${esc(t.type??'?')} · ${dateLabel(t.date)}</small></div><strong>${money(t.amount)}</strong></div>`).join('')||'<div class="chart-empty">No aparecen operaciones recientes para este jugador.</div>';
  const floor=rec.offerFloor,ceiling=rec.offerCeiling;
  const offerSources=`Precio LALIGA ${money(p.value)}${stats.count>=3?` · mediana (${stats.count} operaciones) ${money(stats.median)}`:''}`;
  const basis=rec.budgetBasis||{};
  const target=rec.offerTarget??rec.bidTarget??null;
  const sourceNote=`${offerSources}. El rango combina operaciones observadas, rendimiento y tendencia diaria.`;
  const priceRange=ceiling==null?`<div class="range-box"><small>PUJA ORIENTATIVA</small><b>Sin recomendación de compra</b><span>${esc(rec.reason||'Faltan referencias comparables para proponer un importe.')}</span></div>`:`<div class="range-box ${floor>ceiling?'over-limit':''}"><small>${rec.recommended?'PUJA OBJETIVO · RANGO COMPETITIVO':'PRECIO FUERA DEL RANGO HABITUAL'}</small><b>${target!=null?`${money(target)} objetivo · `:''}${money(floor)} – ${money(ceiling)}</b><span>${esc(rec.reason||'Importe estimado con compras observadas y señales del jugador.')}</span><small class="bid-source-note">${esc(sourceNote)}</small></div>`;
  const rows=(p.priceDays||[]).map(day=>`<tr><td>${shortDate(day.date)}</td><td>${day.value==null?'<span class="no-capture">Sin captura</span>':money(day.value)}</td><td class="${day.dailyChange==null?'':'price-delta '+(day.dailyChange>0?'trend-up':day.dailyChange<0?'trend-down':'')}">${day.dailyChange==null?'—':`${day.dailyChange>0?'+':''}${money(day.dailyChange)}`}</td></tr>`).join('');
  const daily=ff.dailyChange==null?'Sin cambio diario disponible':`${ff.dailyChange>0?'Subió':'Bajó'} ${money(Math.abs(ff.dailyChange))} en 24 h`;
  $('#modal-player-name').textContent=p.name;
  const recommendationPanel=ownedPlayer?'':`<section class="recommend-panel"><div class="recommend-title"><span class="decision ${rec.decision==='Recomendable'?'good':rec.decision==='No recomendable ahora'?'bad':''}">${esc(rec.decision||'Datos insuficientes')}</span><span>Confianza ${esc(rec.confidence||'Baja')}</span></div>${priceRange}<ul>${(rec.reasons||[]).slice(0,2).map(reason=>`<li>${esc(reason)}</li>`).join('')}</ul>${p.owner?`<div class="clause-advice">${rec.clauseActionable?`Cláusula disponible: ${money(p.buyoutClause)}.`:'Cláusula bloqueada o no confirmada.'}</div>`:''}</section>`;
  $('#player-detail').innerHTML=`<div class="detail-player"><div class="player-art">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback">${esc(initials(p.name))}</span></div><div class="detail-player-copy"><div class="detail-player-kicker"><span class="position-tag ${positionClass(p.position)}">${posCode[p.position]||'—'}</span><span>${esc(p.club)}</span></div><b>${number(p.points)} puntos · ${average(p.averagePoints)} media</b><small>${esc(p.status||'Estado n/d')} · ${esc(daily)}${ff.name?` · FF: ${esc(ff.name)}`:''}</small></div></div><div class="detail-stats"><div class="detail-stat"><small>PRECIO ACTUAL</small><b>${money(p.value)}</b><small>${ff.observedAt?`FF · ${esc(dateLabel(ff.observedAt))}`:'Tendencia diaria FF'}</small></div>${purchasePrice!=null?`<div class="detail-stat"><small>${ownedPlayer?'COSTE DE COMPRA':rivalPurchase?'COSTE ATRIBUIDO AL MANAGER':'PRECIO DE SALIDA'}</small><b>${money(purchasePrice)}</b><small>${ownedPlayer||rivalPurchase?esc(p.purchase?.confidence||ownedPlayer.purchase?.confidence||'Orientativo'):'Estimación del mercado'}</small></div>`:''}${salePrice!=null?`<div class="detail-stat"><small>PRECIO DE SALIDA</small><b>${money(salePrice)}</b></div>`:''}<div class="detail-stat price-extreme"><small>MÁXIMO · 30 DÍAS</small><b>${money(priceStats.high)}</b></div><div class="detail-stat price-extreme"><small>MÍNIMO · 30 DÍAS</small><b>${money(priceStats.low)}</b></div>${clause}</div>${recommendationPanel}<section class="detail-section investment-detail"><div class="detail-section-head"><div><h3>Inversión</h3><span class="section-subtitle">${purchasePrice==null?'Sin coste de compra atribuible':`Coste ${money(purchasePrice)} · precio actual ${money(p.value)}`}</span></div></div>${recoveryHtml}</section><section class="detail-section"><div class="detail-section-head"><div><h3>Valor y tendencia</h3><span class="section-subtitle">${priceStats.observedDays||0} precios · últimos 30 días</span></div><span>${p.history?.length||0} capturas locales</span></div>${historyChart(p.ffHistory,p.history)}</section><section class="detail-section"><div class="detail-section-head"><div><h3>Precio diario</h3><span class="section-subtitle">${Math.max(0,(priceStats.observedDays||0)-1)} cambios con dato</span></div></div><div class="price-log-wrap"><table class="price-log"><thead><tr><th>DÍA</th><th>VALOR</th><th>CAMBIO</th></tr></thead><tbody>${rows}</tbody></table></div></section><section class="detail-section"><div class="detail-section-head"><div><h3>Operaciones</h3><span class="section-subtitle">${stats.count||0} importes publicados</span></div><span>${stats.count||0}</span></div>${stats.count?`<div class="transaction-summary">Mediana ${money(stats.median)} · rango ${money(stats.q1)} – ${money(stats.q3)}</div>`:''}<div class="transaction-list">${transactionRows}</div></section>`;
  wireHistoryTooltip();
}

async function loadDetailedPriceHistory(masterId,player){
  if(!player.ff?.playerId)return;
  const fetched=Date.parse(player.priceHistoryCachedAt||'');
  if(Number.isFinite(fetched)&&Date.now()-fetched<12*60*60*1000&&(player.priceStats30d?.observedDays||0)>=25)return;
  try{
    const response=await fetch(`/api/ff-prices/${encodeURIComponent(player.ff.playerId)}`,{cache:'no-store'}),payload=await response.json();
    if(!response.ok)throw new Error(payload.error||'No se pudo actualizar el historial diario.');
    const exactDays=payload.priceDays||[],exactDates=new Set(exactDays.map(day=>day.date));
    const coarseDays=(player.priceDays||[]).filter(day=>day.value!=null&&!exactDates.has(day.date));
    player.priceDays=[...exactDays,...coarseDays].sort((a,b)=>b.date.localeCompare(a.date));
    const observedValues=player.priceDays.filter(day=>day.value!=null).map(day=>Number(day.value));
    player.priceStats30d={...payload.priceStats30d,high:observedValues.length?Math.max(...observedValues):null,low:observedValues.length?Math.min(...observedValues):null,observedDays:observedValues.length};player.priceHistoryCachedAt=payload.priceHistoryCachedAt;
    player.ffHistory=(payload.priceDays||[]).filter(day=>day.value!=null).map(day=>({date:`${day.date}T12:00:00Z`,value:day.value,daysAgo:Math.round((Date.now()-new Date(`${day.date}T12:00:00Z`).getTime())/86400000)}));
    playerDetailCache.set(String(masterId),{player,at:Date.now()});
    if($('#player-modal').open&&$('#player-modal').dataset.playerId===String(masterId))renderPlayerDetail(player);
  }catch(error){const note=$('#player-detail .price-log-wrap')?.parentElement?.querySelector('.section-subtitle');if($('#player-modal').open&&$('#player-modal').dataset.playerId===String(masterId)&&note)note.textContent='Mostrando capturas disponibles; no se pudo ampliar el historial.'}
}

async function openPlayer(id,managerId=null){
  if(!id)return;const modal=$('#player-modal'),cacheKey=`${id}:${managerId||''}`,cached=playerDetailCache.get(cacheKey),preview=dashboardPlayer(id);$('#manager-modal')?.close();modal.dataset.playerId=String(id);modal.dataset.managerId=managerId||'';modal.showModal();
  if(cached&&Date.now()-cached.at<300000){renderPlayerDetail(cached.player);loadDetailedPriceHistory(id,cached.player);return}
  $('#modal-player-name').textContent=preview?.name||'Cargando ficha';
  $('#player-detail').innerHTML=preview?`<div class="player-loading-preview"><div class="detail-player"><div class="player-art">${portrait(preview.image,preview.name,'',preview.clubImage)}<span class="portrait-fallback">${esc(initials(preview.name))}</span></div><div><b>${esc(preview.name)}</b><small>${esc(preview.club)} · cargando análisis y precios</small></div></div><div class="loading-meter"><span></span></div></div>`:'<div class="player-loading-preview"><div class="loading-title"></div><div class="loading-meter"><span></span></div></div>';
  try{const query=managerId?`?managerId=${encodeURIComponent(managerId)}`:'';const response=await fetch(`/api/player/${encodeURIComponent(id)}${query}`,{cache:'no-store'});const p=await response.json();if(!response.ok)throw new Error(p.error||'No se pudo cargar la ficha.');playerDetailCache.set(cacheKey,{player:p,at:Date.now()});renderPlayerDetail(p);loadDetailedPriceHistory(id,p)}catch(error){$('#player-detail').innerHTML=`<div class="empty-state">${esc(error.message)}</div>`}
}

function rivalBuySignal(player){
  const clause=player.buyoutClause,value=player.value,until=player.clauseLockedUntil?new Date(player.clauseLockedUntil).getTime():0;
  if(clause==null)return {icon:'—',tone:'neutral',label:'Sin cláusula disponible'};
  if(player.shielded||(Number.isFinite(until)&&until>Date.now()))return {icon:'🔒',tone:'blocked',label:player.shielded?'Jugador protegido':lockCountdown(player.clauseLockedUntil)};
  if(value==null)return {icon:'?',tone:'neutral',label:'Falta el precio LALIGA para comparar'};
  const premium=clause-value,daily=Number(player.dailyChange)||0;
  if(premium<=0&&daily>0)return {icon:'✓',tone:'good',label:'Cláusula bajo el valor actual y precio al alza'};
  if(premium<=0)return {icon:'✓',tone:'good',label:'Cláusula igual o inferior al valor actual'};
  if(daily>0){const days=Math.ceil(premium/daily);return days<=7?{icon:'↗',tone:'watch',label:`Sobreprecio recuperable en ≈ ${days} días si mantiene la subida`}:{icon:'↗',tone:'watch',label:`Sube de precio; sobreprecio estimado en ≈ ${days} días`}}
  return {icon:'×',tone:'bad',label:'Cláusula por encima del valor y precio sin subida diaria'};
}

function renderRivals(){
  const rivals=[...(data.rivals||[])].sort((a,b)=>(a.position??99)-(b.position??99));
  $('#rival-count').textContent=`${rivals.length} managers`;
  $('#rival-list').innerHTML=rivals.map((r,i)=>{
    const players=r.roster||[];
    const direction=r.dailyValueChange>0?'up':r.dailyValueChange<0?'down':'flat';
    const daily=r.dailyValueChange==null?'Cambio diario n/d':`${r.dailyValueChange>0?'↑':r.dailyValueChange<0?'↓':'•'} ${money(Math.abs(r.dailyValueChange))} / día`;
    return `<button type="button" class="manager-team-card ${r.isOwn?'own-manager':''}" data-manager-id="${esc(r.managerId)}" aria-label="Ver equipo de ${esc(r.name)}"><span class="rank-badge">${r.position??i+1}</span><span class="manager-card-main"><b>${esc(r.name)}${r.isOwn?'<em class="own-label">Tú</em>':''}</b><small>${number(r.teamPoints)} pts · ${players.length} jugadores · ${r.pricedPlayers??0}/${r.eligiblePlayers??players.length} con FF</small><span class="manager-card-value">${money(r.teamValue)} <small>valor de plantilla</small></span></span><span class="manager-card-metrics"><b class="team-value-delta ${direction}">${esc(daily)}</b><small>Ver equipo <span aria-hidden="true">→</span></small></span></button>`
  }).join('')||'<div class="empty-state">No hay equipos de liga.</div>';
  $('#rival-list').querySelectorAll('[data-manager-id]').forEach(card=>card.addEventListener('click',()=>{const manager=(data.rivals||[]).find(row=>String(row.managerId)===card.dataset.managerId);if(manager?.isOwn)location.hash='#plantilla';else openManagerModal(card.dataset.managerId)}));
}

function openManagerModal(managerId){
  const manager=(data?.rivals||[]).find(row=>String(row.managerId)===String(managerId));if(!manager)return;
  if(manager.isOwn){location.hash='#plantilla';return}
  const modal=$('#manager-modal'),root=$('#manager-modal-detail'),roster=manager.roster||[];
  const daily=manager.dailyValueChange,trendClass=daily==null?'muted':daily>0?'trend-up':daily<0?'trend-down':'muted';
  $('#manager-modal-name').textContent=manager.name;
  const groups=['Portero','Defensa','Medio','Delantero','Entrenador'];
  const groupHtml=groups.map(position=>{
    const players=roster.filter(p=>p.position===position&&trendMatches(p,$('#manager-trend-filter')?.value||'all'));
    if(!players.length)return '';
    return `<section class="manager-roster-group"><h3><span class="position-tag ${positionClass(position)}">${posCode[position]||'—'}</span>${position==='Entrenador'?'Entrenadores':`${position}${players.length===1?'':'s'}`}<small>${players.length}</small></h3><div class="manager-modal-player-grid">${players.map(p=>{
      const signal=rivalBuySignal(p),change=p.dailyChange==null?'FF n/d':`${p.dailyChange>0?'↑':p.dailyChange<0?'↓':'•'} ${money(Math.abs(p.dailyChange))} / día`;
      const clause=p.buyoutClause==null?'Sin cláusula':`${money(p.buyoutClause)} · ${p.shielded?'Protegido':p.clauseLockedUntil?lockCountdown(p.clauseLockedUntil):'Disponible'}`;
      const investment=investmentData(p);
      return `<article class="manager-modal-player" role="button" tabindex="0" data-rival-player="${esc(p.masterId)}" data-manager-id="${esc(manager.managerId)}" aria-label="Abrir ficha de ${esc(p.name)}"><div class="manager-modal-player-head"><div class="mini-portrait">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback">${esc(initials(p.name))}</span></div><div class="manager-modal-player-name"><b>${esc(p.name)}</b><small>${esc(p.club||'Club n/d')} · ${esc(p.position)}</small></div>${watchButton(p.masterId)}</div><div class="manager-modal-player-value"><b>${money(p.value)}</b><span class="${p.dailyChange==null?'muted':p.dailyChange>=0?'trend-up':'trend-down'}">${esc(change)}</span></div><div class="manager-modal-player-stats"><span>${number(p.points)} pts</span><span>Media ${average(p.averagePoints)}</span><span>${p.probability==null?'Titularidad n/d':`${number(p.probability)}% titularidad`}</span></div><div class="manager-modal-player-bottom"><span class="manager-modal-clause"><small>CLÁUSULA</small><b>${esc(clause)}</b></span><span class="manager-player-investment"><small>${investment?'P/L ESTIMADO':'P/L'}</small>${investment?investmentProfit(p):'<b class="investment-profit unknown">n/d</b>'}</span><span class="buy-signal ${signal.tone}" title="${esc(signal.label)}">${signal.icon}</span></div>${investment?`<small class="manager-investment-note">Compra ${money(investment.cost)} · actual ${money(investment.current)} · ${investment.confidence==='Confirmada por ti'?'coste confirmado':'coste inferido de actividad'}</small>`:'<small class="manager-investment-note">Sin compra atribuible a este manager en la actividad cargada.</small>'}</article>`
    }).join('')}</div></section>`
  }).join('');
  root.innerHTML=`<section class="manager-overview"><article><small>VALOR DE PLANTILLA</small><b>${money(manager.teamValue)}</b></article><article><small>CAMBIO DIARIO DEL EQUIPO</small><b class="${trendClass}">${daily==null?'n/d':`${daily>0?'↑':'↓'} ${money(Math.abs(daily))}`}</b><em>Estimado con precios FF visibles</em></article><article><small>LIGA</small><b>${number(manager.teamPoints)} puntos · puesto ${manager.position??'n/d'}</b><em>${roster.length} jugadores · saldo rival no disponible</em></article></section><p class="manager-modal-note">El coste usa la actividad más reciente vinculada al manager; la API no confirma si compró o vendió. El P/L aparece por jugador. Toca una ficha para ver el detalle.</p><div class="manager-modal-roster">${groupHtml||'<div class="empty-state">No hay jugadores para este filtro.</div>'}</div>`;
  root.querySelectorAll('[data-rival-player]').forEach(card=>{
    card.addEventListener('click',event=>{if(event.target.closest('.watch-toggle'))return;modal.close();openPlayer(card.dataset.rivalPlayer,card.dataset.managerId)});
    card.addEventListener('keydown',event=>{if(event.target.closest('.watch-toggle'))return;if(event.key==='Enter'||event.key===' '){event.preventDefault();modal.close();openPlayer(card.dataset.rivalPlayer,card.dataset.managerId)}})
  });
  wireWatchButtons('#manager-modal-detail');modal.showModal();
}
function dateLabel(value){const d=new Date(value);return Number.isNaN(d.getTime())?'Fecha n/d':d.toLocaleString('es-ES',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}
function activityKey(row){return [row.playerMasterId,row.type,row.date,row.managerId,row.otherManagerId,row.amount].map(value=>String(value??'')).join('|')}
function renderActivity(){
  $('#activity-list').innerHTML=activityRows.slice(0,activityVisible).map(a=>`<div class="activity-row"><div class="activity-icon">↔</div><div class="activity-text"><b>${esc(a.player)}</b><small>${esc(a.operation||'Movimiento de liga')} · ${esc(a.manager)}${a.otherManager?` ↔ ${esc(a.otherManager)}`:''} · Evento #${esc(a.type??'?')} · ${dateLabel(a.date)}</small></div><div class="activity-amount">${money(a.amount)}<small>${a.directionKnown?'sentido confirmado':'sentido no publicado'}</small></div></div>`).join('')||'<div class="empty-state">Sin movimientos recientes en el feed.</div>';
  const button=$('#activity-more');
  button.classList.toggle('hidden',activityRows.length===0||(activityVisible>=activityRows.length&&activityHistoryComplete));
  button.textContent=activityRows.length>activityVisible?'Mostrar más movimientos ↓':activityHistoryComplete?'Historial completo cargado':'Cargando historial completo…';
}

async function loadMoreActivity(){
  if(activityRows.length>activityVisible){activityVisible+=14;renderActivity();renderBuilder();return}
  if(!activityHistoryComplete){await loadAllActivity();if(activityRows.length>activityVisible){activityVisible+=14;renderActivity();renderBuilder()}}
}

async function loadAllActivity(){
  if(activityHistoryComplete)return;
  if(activityScanPromise)return activityScanPromise;
  $('#activity-more').textContent='Cargando historial completo…';
  activityScanPromise=(async()=>{
    const known=new Set(activityRows.map(activityKey));
    try{
      for(let page=activityPage+1;page<=100;page++){
        const response=await fetch(`/api/activity?page=${page}`,{cache:'no-store'});
        const payload=await response.json();
        if(!response.ok)throw new Error(payload.error||'No se pudo consultar el historial.');
        const rows=Array.isArray(payload.items)?payload.items:[];
        if(!rows.length){activityHistoryComplete=true;break}
        let added=0;
        for(const row of rows){const key=activityKey(row);if(!known.has(key)){known.add(key);activityRows.push(row);added++}}
        activityPage=Number(payload.page)||page;
        if(added===0){activityHistoryComplete=true;break}
      }
      if(activityPage>=100)activityHistoryComplete=true;
      if(data){data.activity=activityRows.slice();data.activityLastPage=activityPage;data.activityComplete=activityHistoryComplete;saveDashboardCache(data)}
      renderActivity();renderBuilder();
    }catch(error){
      console.warn('No se pudo completar la carga del historial:',error);
      $('#activity-more').classList.remove('hidden');$('#activity-more').textContent='Reintentar carga del historial';
      toast('No se pudo completar la carga del historial; puedes reintentar.')
    }finally{activityScanPromise=null}
  })();
  return activityScanPromise;
}

function signedMoney(value){return `${value>0?'+':value<0?'−':''}${money(Math.abs(value))}`}
function daysText(value){return `${value} ${value===1?'día':'días'}`}

function investmentData(player){
  let cost,date,confidence;
  if(player.purchase&&player.purchase.amount!=null&&Number.isFinite(Number(player.purchase.amount))){
    cost=Number(player.purchase.amount);date=player.purchase.date;confidence=player.purchase.confidence;
  }else{
    const ownManagerIds=new Set([...(data?.ownManagerIds||[]),data?.ownManagerId,data?.ownTeamId].filter(Boolean).map(String));
    const normalizeName=value=>String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim().toLocaleLowerCase('es');
    const ownManagerName=normalizeName(data?.ownManagerName);
    const playerEvents=activityRows.filter(row=>String(row.playerMasterId)===String(player.masterId)
      &&[1,31].includes(Number(row.type))&&Number(row.amount)>0);
    let events=playerEvents.filter(row=>ownManagerIds.has(String(row.managerId||''))||ownManagerIds.has(String(row.otherManagerId||''))
      ||(ownManagerName&&(normalizeName(row.manager)===ownManagerName||normalizeName(row.otherManager)===ownManagerName)));
    // Older already-running servers omit manager IDs/names. For a player still
    // present in our roster, use that player's latest priced transfer as an inference.
    if(!events.length&&!ownManagerIds.size&&!ownManagerName&&data?.roster?.some(row=>String(row.masterId)===String(player.masterId)))events=playerEvents;
    events.sort((a,b)=>new Date(b.date||0)-new Date(a.date||0));
    if(!events[0])return null;
    cost=Number(events[0].amount);date=events[0].date;confidence='Inferida';
  }
  if(player.value==null)return null;
  const current=Number(player.value),profit=current-cost,daily=player.dailyChange==null?null:Number(player.dailyChange);
  const daysToBreakeven=profit<0&&daily>0?Math.ceil(-profit/daily):profit>0&&daily<0?Math.ceil(profit/Math.abs(daily)):null;
  const projected7=daily==null?null:profit+daily*7;
  const advice=profit>0&&daily<0?'Vas ganando, pero el precio baja: considera vender para proteger el beneficio.'
    :profit<0&&daily<0?'La pérdida aumenta con la tendencia actual; revisa si conviene vender.'
    :profit<0&&daily>0?`Al ritmo actual recuperarías el coste en ${daysText(daysToBreakeven)}.`
    :profit>=0&&daily>0?'Beneficio actual y precio al alza.'
    :'Sin tendencia diaria suficiente para proyectar.';
  return {cost,current,profit,daily,daysToBreakeven,projected7,date,confidence,advice};
}

function investmentProfit(player){
  const item=investmentData(player);
  if(!item)return `<span class="investment-profit unknown" title="El historial de actividad aún no ofrece un coste de compra atribuible a tu manager.">P/L n/d</span>`;
  const signClass=item.profit>0?'positive':item.profit<0?'negative':'even';
  const direction=item.daily==null?'':item.daily>0?'Precio subiendo':item.daily<0?'Precio bajando':'Precio estable';
  const projected=item.projected7==null?'Proyección a 7 días no disponible':`Si sigue el cambio diario, P/L en 7 días: ${signedMoney(item.projected7)}`;
  const timing=item.daysToBreakeven==null?'':item.profit<0?`Recuperación estimada en ${daysText(item.daysToBreakeven)}.`:`El margen actual podría agotarse en ${daysText(item.daysToBreakeven)}.`;
  const title=`Coste ${money(item.cost)} (${item.confidence||'inferido'}) · valor actual ${money(item.current)} · ${signedMoney(item.profit)} de P/L · ${item.daily==null?'Cambio diario n/d':`${signedMoney(item.daily)}/día (${direction})`} · ${timing} ${projected} ${item.advice||''}`;
  const trendIcon=item.daily>0?'↗':item.daily<0?'↘':'';
  return `<span class="investment-profit ${signClass}" title="${esc(title)}" aria-label="Profit o pérdida estimada ${esc(signedMoney(item.profit))}"><b>${item.confidence==='Confirmada por ti'?'':'≈ '}${esc(signedMoney(item.profit))}</b>${trendIcon?`<i class="${item.daily<0&&item.profit>0?'falling':''}" aria-hidden="true">${trendIcon}</i>`:''}</span>`;
}

function pitchMetric(player){
  const mode=$('#pitch-metric')?.value||'daily';
  if(mode==='profit')return investmentProfit(player);
  if(mode==='points'){
    const weeks=data?.latestWeeks||[],scores=new Map((player.weekPoints||[]).map(row=>[Number(row.weekNumber),row.points]));
    return weeks.length?`<span class="pitch-week-points" title="Mismas tres jornadas para toda la plantilla; — indica dato ausente">${weeks.map(week=>`<span>J${number(week)} <b>${scores.get(week)!=null&&Number.isFinite(Number(scores.get(week)))?number(Number(scores.get(week))):'—'}</b></span>`).join('')}</span>`:'<span class="pitch-metric muted">Jornadas n/d</span>';
  }
  return `<span class="pitch-metric ${player.dailyChange==null?'muted':player.dailyChange>0?'trend-up':player.dailyChange<0?'trend-down':'muted'}">${player.dailyChange==null?'Cambio n/d':`${player.dailyChange>0?'↑':player.dailyChange<0?'↓':'•'} ${money(Math.abs(player.dailyChange))}/día`}</span>`;
}

function renderBuilder(){
  const formation=$('#formation').value;const [gk,def,mid,fwd]=formations[formation]||formations['4-3-3'];const rows=[['Delantero',fwd,gk+def+mid],['Medio',mid,gk+def],['Defensa',def,gk],['Portero',gk,0]];
  const selected=selectedIds.filter(Boolean).map(id=>playerById(id)).filter(Boolean);
  const completeness=$('#xi-completeness');if(completeness){const missing=11-selected.length;completeness.textContent=missing?`${selected.length}/11 · faltan ${missing} jugador${missing===1?'':'es'}`:'11/11 · listo para competir';completeness.classList.toggle('incomplete',missing>0)}
  const ranked=[...selected].filter(p=>p.position!=='Entrenador'&&p.available!==false&&p.probability!==0).sort((a,b)=>captainScore(b)-captainScore(a));
  const suggestion=ranked[0]&&captainScore(ranked[0])>0?ranked[0]:null;
  if(!captainId||!selectedIds.includes(captainId))captainId=suggestion?.id||null;
  $('#pitch-rows').innerHTML=rows.map(([pos,count])=>`<div class="pitch-row ${count>=5?'crowded':''}">${Array.from({length:count},(_,i)=>{
    const index=rows.find(r=>r[0]===pos)[2]+i;const p=playerById(selectedIds[index]);
    return `<div class="pitch-slot ${p?'filled':'empty'}" data-index="${index}" data-position="${pos}" ${p?`data-player-id="${esc(p.id)}" data-master-id="${esc(p.masterId)}" role="button" tabindex="0" aria-label="Abrir ficha de ${esc(p.name)}"`:''} title="${p?`${esc(p.name)} · arrastra para mover`:`Arrastra un ${pos.toLowerCase()} aquí`}" draggable="${p?'true':'false'}">${p?`<div class="player-chip">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback chip-fallback">${esc(initials(p.name))}</span></div>${captainId===p.id?'<span class="captain-star selected" title="Capitán" aria-label="Capitán">★</span>':''}<span class="pitch-name">${esc(p.name)}</span><span class="pitch-price" aria-label="Valor ${money(p.value)}">${money(p.value).replace(' €','')}<span class="pitch-currency"> €</span></span>${pitchMetric(p)}`:'<span class="player-chip">+</span><span class="pitch-name">'+pos+'</span>'}</div>`
  }).join('')}</div>`).join('');
  const xi=new Set(selectedIds);const groups=['Portero','Defensa','Medio','Delantero','Entrenador'];
  $('#bench-list').innerHTML=groups.map(position=>{const roster=data.roster.filter(p=>p.position===position&&trendMatches(p,$('#bench-trend-filter')?.value||'all')).sort((a,b)=>a.name.localeCompare(b.name,'es'));if(!roster.length)return '';return `<section class="bench-group"><div class="bench-group-title">${esc(position)} <span>${roster.length}</span></div>${roster.map(p=>`<div class="bench-player ${xi.has(p.id)?'in-xi':''}" role="button" tabindex="0" aria-label="Abrir ficha de ${esc(p.name)}; arrastra para cambiar su posición" title="Abre la ficha · arrastra para alinear" draggable="true" data-player-id="${esc(p.id)}"><div class="mini-portrait">${portrait(p.image,p.name,'',p.clubImage)}<span class="portrait-fallback">${esc(initials(p.name))}</span></div><div class="bench-info"><div class="bench-player-title"><b>${esc(p.name)}</b>${p.dailyChange==null?'':`<em class="${p.dailyChange>0?'trend-up':p.dailyChange<0?'trend-down':'muted'}">${p.dailyChange>0?'↑':'↓'} ${money(Math.abs(p.dailyChange))}</em>`}${watchButton(p.masterId)}${investmentProfit(p)}</div><small>${p.probability==null?'Titularidad n/d':`${p.probability}% titularidad`}${p.id===captainId?' · CAPITÁN':''}</small></div><div class="bench-value">${money(p.value)}<small>${number(p.points)} pts</small>${p.position!=='Entrenador'&&!xi.has(p.id)?`<button type="button" class="reserve-toggle ${reserveIds[p.position]===p.id?'selected':''}" data-reserve-player="${esc(p.id)}" data-reserve-position="${esc(p.position)}">${reserveIds[p.position]===p.id?'✓ Reserva':'+ Reserva'}</button>`:''}</div></div>`).join('')}</section>`}).join('');
  const suggestedPlayer=suggestion?.name||'Aún no hay candidato claro';const chosen=playerById(captainId);
  $('#captain-recommendation').innerHTML=`<span class="captain-medal">★</span><div><b>${chosen?`Capitán Premium: ${esc(chosen.name)}`:`Sugerencia: ${esc(suggestedPlayer)}`}</b><small>${chosen?captainReason(chosen):'Faltan datos de titularidad o media para elegir capitán.'}</small></div>${suggestion?`<button type="button" class="text-button" id="set-suggested-captain">Usar sugerencia</button>`:''}`;
  const pitchHint=$('.pitch-hint');if(pitchHint){const missing={};for(const [pos,count,offset] of rows){for(let i=0;i<count;i++)if(!selectedIds[offset+i])missing[pos]=(missing[pos]||0)+1}const position=Object.keys(missing)[0],candidates=(data?.market||[]).filter(p=>p.position===position&&p.bidGuidance?.recommended).sort((a,b)=>(a.asking??a.value??0)-(b.asking??b.value??0)).slice(0,2);pitchHint.innerHTML=Object.keys(missing).length?`<span class="hint-reason">Falta ${Object.entries(missing).map(([name,count])=>`${count} ${esc(name.toLowerCase())}`).join(' · ')} para completar tu XI.</span>${candidates.map(p=>`<button type="button" class="pitch-candidate" data-candidate-id="${esc(p.masterId)}">${esc(p.name)} · ${money(p.asking??p.value)}</button>`).join('')}`:'<span class="hint-reason">Once completo. Compara titularidad y puntos para elegir el mejor.</span>';pitchHint.querySelectorAll('.pitch-candidate').forEach(button=>button.addEventListener('click',()=>openPlayer(button.dataset.candidateId)))}
  $('#set-suggested-captain')?.addEventListener('click',()=>{captainId=suggestion.id;renderBuilder();$('#draft-status').textContent='Capitán modificado · pendiente de guardar'});
  const picker=$('#captain-picker');picker.innerHTML=`<small>CAPITÁN DEL ONCE</small>${selected.map(p=>`<button type="button" data-captain-choice="${esc(p.id)}">${esc(p.name)}${suggestion?.id===p.id?' · sugerido':''}${captainId===p.id?' ★':''}</button>`).join('')||'<span>Completa tu once para elegir capitán.</span>'}`;
  $('#choose-captain').onclick=event=>{event.stopPropagation();picker.hidden=!picker.hidden};
  picker.querySelectorAll('[data-captain-choice]').forEach(button=>button.addEventListener('click',event=>{event.stopPropagation();captainId=button.dataset.captainChoice;picker.hidden=true;renderBuilder();$('#draft-status').textContent='Capitán modificado · pendiente de guardar'}));
  wireBuilderEvents();wireWatchButtons('#pitch-rows, #bench-list');document.querySelectorAll('.reserve-toggle').forEach(button=>button.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();const position=button.dataset.reservePosition,id=button.dataset.reservePlayer;if(reserveIds[position]===id)delete reserveIds[position];else reserveIds[position]=id;localStorage.setItem(BENCH_KEY,JSON.stringify(reserveIds));renderBuilder()}));
  renderSquadTotals();renderBudgetPlanner();
  const fieldScroll=document.querySelector('.pitch-wrap');if(fieldScroll&&window.innerWidth<=700)fieldScroll.scrollLeft=Math.max(0,(fieldScroll.scrollWidth-fieldScroll.clientWidth)/2);
}

function captainScore(player){const avg=Number(player.averagePoints)||0;const chance=player.probability==null?0.5:Math.max(0,Math.min(100,player.probability))/100;return avg*chance;}
function captainReason(player){const avg=Number(player.averagePoints);if(player.averagePoints==null||player.averagePoints===''||!Number.isFinite(avg))return 'Sin media suficiente para estimar su aportación.';if(player.probability==null)return `Media ${average(avg)} pts; titularidad aún sin dato.`;return `${average(captainScore(player))} pts esperados · ${average(avg)} de media × ${number(player.probability)}% probable.`}

function wireBuilderEvents(){
  document.querySelectorAll('.bench-player').forEach(card=>{
    card.addEventListener('dragstart',event=>{if(event.target.closest('button')){event.preventDefault();return}pendingPlayer=card.dataset.playerId;suppressDragClickUntil=Date.now()+900;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',pendingPlayer)});
    card.addEventListener('dragend',()=>{pendingPlayer=null});
    card.addEventListener('click',event=>{if(event.target.closest('button')||Date.now()<suppressDragClickUntil)return;openPlayer(playerById(card.dataset.playerId)?.masterId)});
    card.addEventListener('keydown',event=>{if(event.target.closest('button'))return;if(event.key==='Enter'||event.key===' '){event.preventDefault();openPlayer(playerById(card.dataset.playerId)?.masterId)}});
  });
  document.querySelectorAll('.pitch-slot').forEach(slot=>{
    slot.addEventListener('dragstart',event=>{if(!slot.dataset.playerId)return;pendingPlayer=slot.dataset.playerId;suppressDragClickUntil=Date.now()+900;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',pendingPlayer)});
    slot.addEventListener('dragover',event=>{event.preventDefault();slot.classList.add('drag-over')});
    slot.addEventListener('dragleave',()=>slot.classList.remove('drag-over'));
    slot.addEventListener('drop',event=>{event.preventDefault();slot.classList.remove('drag-over');setSlot(Number(slot.dataset.index),event.dataTransfer.getData('text/plain'))});
    slot.addEventListener('click',event=>{if(event.target.closest('.captain-star')||Date.now()<suppressDragClickUntil)return;if(slot.dataset.masterId)openPlayer(slot.dataset.masterId)});
    slot.addEventListener('keydown',event=>{if(event.target.closest('.captain-star'))return;if(event.key==='Enter'||event.key===' '){event.preventDefault();if(slot.dataset.masterId)openPlayer(slot.dataset.masterId)}});
  });
  const bench=$('#bench-list');bench?.addEventListener('dragover',event=>event.preventDefault());
  bench?.addEventListener('drop',event=>{event.preventDefault();const id=event.dataTransfer.getData('text/plain'),index=selectedIds.indexOf(id);if(index<0)return;selectedIds[index]=null;if(captainId===id)captainId=null;pendingPlayer=null;renderBuilder();$('#draft-status').textContent='Jugador movido al banquillo · pendiente de guardar'});
}
function setSlot(index,id){
  const player=playerById(id);if(!player)return;const pos=document.querySelector(`.pitch-slot[data-index="${index}"]`)?.dataset.position;
  if(player.position!==pos){toast(`Esta posición es para ${pos}; ${player.name} juega de ${player.position}.`);return}
  const existing=selectedIds.indexOf(id);if(existing>=0)selectedIds[existing]=null;
  selectedIds[index]=id;selectedIds=selectedIds.slice(0,11);if(!captainId||!selectedIds.includes(captainId))captainId=null;renderBuilder();$('#draft-status').textContent='Cambios sin guardar';
}

function useRecommended(notify=true){
  const lineup=data?.report?.lineup;if(!lineup?.players?.length){toast('Aún no hay suficientes datos para sugerir el XI.');return}
  $('#formation').value=lineup.formation.join('-');currentFormation=$('#formation').value;
  const groups={Portero:[],Defensa:[],Medio:[],Delantero:[]};
  for(const row of lineup.players){const player=data.roster.find(p=>p.id===String(row.id)||p.masterId===String(row.masterId));if(player&&groups[row.position])groups[row.position].push(player.id)}
  const [g,d,m,f]=formations[currentFormation];
  const fill=(position,count)=>[...groups[position].slice(0,count),...Array(Math.max(0,count-groups[position].length)).fill(null)];
  selectedIds=[...fill('Portero',g),...fill('Defensa',d),...fill('Medio',m),...fill('Delantero',f)];
  captainId=null;renderBuilder();$('#draft-status').textContent=lineup.complete?'XI sugerido · pendiente de guardar':'XI parcial · falta completar la plantilla';if(notify)toast(lineup.complete?'XI sugerido; revisa el capitán antes de guardar.':'Aún no hay 11 jugadores: revisa las posiciones que faltan.');
}

function repackForFormation(oldFormation,newFormation){
  const [oldG,oldD,oldM,oldF]=formations[oldFormation]||formations['4-3-3'];
  const positions={Portero:[],Defensa:[],Medio:[],Delantero:[]};
  let offset=0;for(const [position,count] of [['Portero',oldG],['Defensa',oldD],['Medio',oldM],['Delantero',oldF]]){for(let i=0;i<count;i++){const id=selectedIds[offset++];if(id&&playerById(id))positions[position].push(id)}}
  const [g,d,m,f]=formations[newFormation]||formations['4-3-3'];
  const fill=(position,count)=>[...positions[position].slice(0,count),...Array(Math.max(0,count-positions[position].length)).fill(null)];
  return [...fill('Portero',g),...fill('Defensa',d),...fill('Medio',m),...fill('Delantero',f)];
}

async function saveDraft(){
  const player_ids=Array.from({length:11},(_,index)=>selectedIds[index]&&playerById(selectedIds[index])?selectedIds[index]:null);
  try{const res=await fetch('/api/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({formation:$('#formation').value,player_ids,captain_id:captainId})});const result=await res.json();if(!res.ok)throw new Error(result.error||'No se pudo guardar.');$('#draft-status').textContent=`Guardado ${new Date().toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'})}`;toast('Borrador y capitán guardados en este dispositivo.');}
  catch(err){toast(err.message)}
}

$('#pitch-metric').addEventListener('change',renderBuilder);$('#bench-trend-filter').addEventListener('change',renderBuilder);$('#market-trend-filter').addEventListener('change',renderMarket);$('#radar-trend-filter').addEventListener('change',()=>{radarVisible=12;renderClauseRadar()});$('#sell-trend-filter').addEventListener('change',renderSales);$('#favorites-trend-filter').addEventListener('change',renderFavorites);$('#manager-trend-filter').addEventListener('change',renderRivals);$('#refresh').addEventListener('click',()=>loadDashboard(true));$('#optimize').addEventListener('click',()=>useRecommended());$('#save-draft').addEventListener('click',saveDraft);$('#activity-more').addEventListener('click',loadMoreActivity);$('#formation').addEventListener('change',()=>{const next=$('#formation').value;selectedIds=repackForFormation(currentFormation,next);currentFormation=next;renderBuilder();$('#draft-status').textContent='Formación modificada · pendiente de guardar'});$('#market-search').addEventListener('input',renderMarket);$('#position-filter').addEventListener('change',renderMarket);$('#market-sort').addEventListener('change',renderMarket);
$('#radar-more').addEventListener('click',()=>{radarVisible+=12;renderClauseRadar()});

$('#modal-close').addEventListener('click',()=>$('#player-modal').close());$('#player-modal').addEventListener('click',event=>{if(event.target===$('#player-modal'))$('#player-modal').close()});
$('#manager-modal-close').addEventListener('click',()=>$('#manager-modal').close());$('#manager-modal').addEventListener('click',event=>{if(event.target===$('#manager-modal'))$('#manager-modal').close()});
document.querySelectorAll('.nav-item').forEach(item=>item.addEventListener('click',()=>{document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));item.classList.add('active')}));
function fixtureMetric(player){
  const mode=$('#fixture-metric')?.value||'form';
  if(mode==='market')return `<span class="fixture-metric"><b>${money(player.value)}</b><em class="${player.dailyChange>0?'trend-up':player.dailyChange<0?'trend-down':'muted'}">${player.dailyChange==null?'Cambio n/d':`${player.dailyChange>0?'↑':player.dailyChange<0?'↓':'•'} ${money(Math.abs(player.dailyChange))}/día`}</em></span>`;
  if(mode==='points')return `<span class="fixture-metric"><b>${player.lastPoints==null?'Puntos n/d':`${number(Number(player.lastPoints))} pts`}</b><em>${player.lastPointsWeek==null?'Última jornada sin dato':`Jornada ${number(Number(player.lastPointsWeek))}`}</em></span>`;
  return `<span class="fixture-prob ${Number(player.probability)>=80?'high':Number(player.probability)<50?'low':''}">${player.probability==null?'Prob. n/d':`${number(player.probability)}%`}</span>`;
}
function fixturePlayer(player,index){
  const x=Number.parseFloat(player.x),y=Number.parseFloat(player.y),located=Number.isFinite(x)&&Number.isFinite(y);
  const fallbackY=player.goalkeeper?88:18+Math.floor(index/4)*22;
  return `<button class="fixture-player" type="button" data-fixture-slug="${esc(player.slug||'')}" data-fixture-master="${esc(player.masterId||'')}" style="--fx:${located?Math.min(89,Math.max(11,x)):20+(index%4)*20}%;--fy:${located?Math.min(88,Math.max(12,y)):fallbackY}%"><span class="fixture-portrait">${portrait(player.image,player.name)}<span class="portrait-fallback">${esc(initials(player.name))}</span></span><b>${esc(player.name)}</b>${fixtureMetric(player)}</button>`;
}
function fixturePitch(name,badge,players,bench,complete){
  const sorted=[...players].sort((a,b)=>Number.parseFloat(a.y||'0')-Number.parseFloat(b.y||'0'));
  return `<div class="fixture-team"><h3>${badge?`<img src="${esc(badge)}" alt="">`:''}${esc(name)} <small class="${complete?'':'fixture-incomplete'}">${sorted.length}/11 probables${complete?'':' · fuente incompleta'}</small></h3><div class="fixture-team-layout"><div class="fixture-pitch">${sorted.length?sorted.map(fixturePlayer).join(''):'<div class="fixture-empty">Once probable todavía no disponible</div>'}</div><aside class="fixture-bench"><h4>Sustitutos <span>${bench.length}</span></h4><div class="fixture-bench-list">${bench.map(p=>`<button class="fixture-sub" type="button" data-fixture-slug="${esc(p.slug||'')}" data-fixture-master="${esc(p.masterId||'')}"><span class="fixture-sub-photo">${portrait(p.image,p.name)}<span class="portrait-fallback">${esc(initials(p.name))}</span></span><span><b>${esc(p.name)}</b>${fixtureMetric(p)}</span></button>`).join('')||'<small>Sin sustitutos publicados</small>'}</div></aside></div></div>`;
}
function openFixturePlayer(player){
  if(player?.masterId){openPlayer(player.masterId);return}
  const modal=$('#player-modal');modal.dataset.playerId='';$('#modal-player-name').textContent=player?.name||'Jugador';
  $('#player-detail').innerHTML=`<div class="detail-player"><div class="player-art">${portrait(player?.image,player?.name||'Jugador')}<span class="portrait-fallback">${esc(initials(player?.name||''))}</span></div><div class="detail-player-copy"><b>${esc(player?.name||'Jugador')}</b><small>Once probable · FutbolFantasy</small></div></div><div class="detail-stats"><div class="detail-stat"><small>VALOR</small><b>${money(player?.value)}</b></div><div class="detail-stat"><small>CAMBIO DIARIO</small><b>${money(player?.dailyChange)}</b></div><div class="detail-stat"><small>PROBABILIDAD</small><b>${player?.probability==null?'n/d':`${number(player.probability)}%`}</b></div><div class="detail-stat"><small>ÚLTIMA JORNADA</small><b>${player?.lastPoints==null?'n/d':`${number(Number(player.lastPoints))} pts`}</b></div></div><p class="fixture-detail-note">La ficha de LALIGA aún no se ha vinculado con certeza a este jugador.</p>`;
  modal.showModal();
}
function renderFixtures(){
  const fixtures=data?.fixtures||[],tabs=$('#fixture-tabs'),detail=$('#fixture-detail');if(!tabs||!detail)return;
  $('#fixtures-count').textContent=`${fixtures.length} partidos`;
  if(!fixtures.length){detail.innerHTML='<div class="empty-state">Los próximos onces probables aún no están disponibles.</div>';tabs.innerHTML='';return}
  selectedFixture=Math.min(selectedFixture,fixtures.length-1);
  tabs.innerHTML=fixtures.map((match,index)=>`<button class="fixture-tab ${index===selectedFixture?'active':''}" type="button" data-fixture-index="${index}"><span>${esc(match.home)} · ${esc(match.away)}</span><small>${match.kickoff?new Date(match.kickoff.replace(' ','T')).toLocaleString('es-ES',{weekday:'short',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'Fecha pendiente'}</small></button>`).join('');
  const match=fixtures[selectedFixture];detail.innerHTML=`<div class="fixture-score"><span>${esc(match.home)}</span><b>VS</b><span>${esc(match.away)}</span></div><div class="fixture-pitches">${fixturePitch(match.home,match.homeBadge,match.homeXI||[],match.homeBench||[],match.homeComplete)}${fixturePitch(match.away,match.awayBadge,match.awayXI||[],match.awayBench||[],match.awayComplete)}</div>`;
  tabs.querySelectorAll('[data-fixture-index]').forEach(button=>button.addEventListener('click',()=>{selectedFixture=Number(button.dataset.fixtureIndex);renderFixtures()}));
  detail.querySelectorAll('[data-fixture-slug]').forEach(button=>button.addEventListener('click',()=>{const players=[...(match.homeXI||[]),...(match.awayXI||[]),...(match.homeBench||[]),...(match.awayBench||[])];openFixturePlayer(players.find(p=>p.slug===button.dataset.fixtureSlug&&String(p.masterId||'')===button.dataset.fixtureMaster))}));
}
$('#fixture-metric').addEventListener('change',renderFixtures);
let searchTimer;
$('#global-search')?.addEventListener('input',event=>{clearTimeout(searchTimer);const query=event.target.value.trim(),panel=$('#global-results');panel.hidden=true;if(query.length<2)return;searchTimer=setTimeout(async()=>{try{const response=await fetch(`/api/search?q=${encodeURIComponent(query)}`),result=await response.json();if($('#global-search').value.trim()!==query)return;panel.innerHTML=(result.items||[]).map((item,index)=>`<button type="button" data-search-index="${index}"><b>${esc(item.name)}</b><small>${esc(item.detail)}</small></button>`).join('')||'<span class="search-empty">Sin resultados</span>';panel.hidden=false;panel.querySelectorAll('[data-search-index]').forEach(button=>button.addEventListener('click',()=>{const item=result.items[Number(button.dataset.searchIndex)];panel.hidden=true;$('#global-search').value='';if(item.type==='player')openPlayer(item.id);else if(item.type==='manager'){location.hash='#rivales';openManagerModal(item.id)}else{location.hash='#mercado';$('#market-search').value=item.name;renderMarket()}}))}catch{panel.hidden=true}},180)});
document.addEventListener('click',event=>{if(!event.target.closest('.global-search'))$('#global-results').hidden=true});
setInterval(()=>{if(document.visibilityState==='visible'&&!dashboardLoading)loadDashboard(true,true)},5*60*1000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&data&&Date.now()-new Date(data.updatedAt).getTime()>5*60*1000)loadDashboard(true,true)});
showCachedDashboard();loadDashboard();
