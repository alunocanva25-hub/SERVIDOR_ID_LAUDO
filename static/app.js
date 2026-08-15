const $ = id => document.getElementById(id);
const VERSION = 'V1.0.0.30';
const LIMITS = {
  'A (2%)': {ativa:4.00, reativa:4.00},
  'B (1%)': {ativa:1.30, reativa:2.60},
  'C (0,5%)': {ativa:0.70, reativa:1.40},
  'D (0,2%)': {ativa:0.30, reativa:0.60}
};
const STATUS_META = {
  RASCUNHO:{label:'Rascunho',cls:'draft'},
  PRONTO:{label:'Pronto p/ ID CAMPS',cls:'ready'},
  PRONTO_PARA_ID_CAMPS:{label:'Pronto p/ ID CAMPS',cls:'ready'},
  AGUARDANDO_REVISAO:{label:'Aguardando revisão',cls:'waiting'},
  EM_REVISAO:{label:'Em revisão',cls:'reviewing'},
  DEVOLVIDO:{label:'Correção solicitada',cls:'returned'},
  LAUDO_CRIADO:{label:'Laudo criado',cls:'created'}
};
const FLOW_STATUSES=['PRONTO','PRONTO_PARA_ID_CAMPS','AGUARDANDO_REVISAO','EM_REVISAO'];
const ACTIVE_STATUSES=['RASCUNHO',...FLOW_STATUSES,'DEVOLVIDO'];
function normStatus(s){ const v=String(s||'RASCUNHO').toUpperCase(); return v==='PRONTO'?'PRONTO_PARA_ID_CAMPS':v; }
function statusMeta(s){ return STATUS_META[normStatus(s)] || {label:normStatus(s).replaceAll('_',' '),cls:''}; }
let bootstrap = {models:[], observations:[], people:{}, manufacturers:[], counts:{}};
let selectedObs = [];
let currentRecordId = null;
let currentStep = 0;
let lastStep = 0;
let activeMainTab = 'home';
let historyFilter = 'ALL';
let laudosSearchTerm = '';
let historySearchTerm = '';
let allRecords = [];
let appConfig = {users:[], form_visibility:{}, backend:{}};
let settingsPreviousTab = 'home';
let settingsReturnEditor = false;
let editingConfigUserId = null;
const REQUIRED_FORM_FIELDS = new Set(['numero_laudo','instalacao','numero_serie','modelo']);
let toastTimer = 0;
let autoSaveTimer = 0;
let autoSaving = false;
let lastCatalogRefresh = 0;
let deferredInstallPrompt = null;
let toiManuallyEdited = false;
let authConfig = {requested:false,configured:false,enabled:false,admin_api:false};
let authState = {access_token:'',refresh_token:''};
let currentUser = null;
let appStarted = false;
let passwordChangeMode = 'forced';
const AUTH_STORAGE_KEY = 'idlaudo.auth.v27';
const LEGACY_AUTH_STORAGE_KEYS = ['idlaudo.auth.v26','idlaudo.auth.v25'];
let authGateState = 'BOOT'; // BOOT | LOCKED | AUTHENTICATED | LOCAL_BYPASS

function getUiMode(){ return 'APP'; }

function animateOnce(el, cls, removeHidden=false){
  if(!el) return;
  if(removeHidden) el.classList.remove('hidden');
  el.classList.remove('fx-enter-right','fx-enter-left','fx-exit-left','fx-exit-right');
  void el.offsetWidth;
  el.classList.add(cls);
  const clear=()=>{el.classList.remove(cls); el.removeEventListener('animationend', clear);};
  el.addEventListener('animationend', clear);
}

function switchView(showId, hideId, dir='right'){
  const showEl=$(showId), hideEl=$(hideId);
  if(!showEl || !hideEl) return;
  hideEl.classList.remove('fx-enter-right','fx-enter-left','fx-exit-left','fx-exit-right');
  showEl.classList.remove('fx-enter-right','fx-enter-left','fx-exit-left','fx-exit-right');
  animateOnce(hideEl, dir==='right' ? 'fx-exit-left' : 'fx-exit-right');
  setTimeout(()=>{
    hideEl.classList.add('hidden');
    animateOnce(showEl, dir==='right' ? 'fx-enter-right' : 'fx-enter-left', true);
  }, 120);
}

function updateBottomNav(){
  document.querySelectorAll('.navItem').forEach(btn=>btn.classList.toggle('active', btn.dataset.tab===activeMainTab));
}
function setEditorMode(on){ document.body.classList.toggle('editor-mode', !!on); }
function switchMainTab(tab){
  if(tab===activeMainTab){ loadRecords(); return; }
  const order={home:0,laudos:1,historico:2};
  const dir=(order[tab]??0) >= (order[activeMainTab]??0) ? 'right' : 'left';
  switchView(`${tab}View`, `${activeMainTab}View`, dir);
  activeMainTab=tab;
  updateBottomNav();
  loadRecords();
  refreshCatalog(true);
}
function setHistoryFilter(filter){
  historyFilter=filter;
  $('historyAllBtn')?.classList.toggle('active', filter==='ALL');
  $('historyDraftBtn')?.classList.toggle('active', filter==='RASCUNHO');
  $('historySentBtn')?.classList.toggle('active', filter==='ENVIADOS');
  $('historyReturnedBtn')?.classList.toggle('active', filter==='DEVOLVIDO');
  $('historyCreatedBtn')?.classList.toggle('active', filter==='LAUDO_CRIADO');
  renderHistory();
}

function digitsOnly(v){ return String(v||'').replace(/\D+/g,''); }
function matchesRecordSearch(row, term){
  const q=digitsOnly(term); if(!q) return true;
  return digitsOnly(row.numero_laudo).includes(q) || digitsOnly(row.numero_serie).includes(q);
}
function onLaudosSearch(v){
  const clean=digitsOnly(v); if($('laudosSearch') && $('laudosSearch').value!==clean) $('laudosSearch').value=clean;
  laudosSearchTerm=clean; $('laudosSearchClear')?.classList.toggle('hidden',!clean); renderLaudos();
}
function clearLaudosSearch(){ laudosSearchTerm=''; setVal('laudosSearch',''); $('laudosSearchClear')?.classList.add('hidden'); renderLaudos(); }
function onHistorySearch(v){
  const clean=digitsOnly(v); if($('historySearch') && $('historySearch').value!==clean) $('historySearch').value=clean;
  historySearchTerm=clean; $('historySearchClear')?.classList.toggle('hidden',!clean); renderHistory();
}
function clearHistorySearch(){ historySearchTerm=''; setVal('historySearch',''); $('historySearchClear')?.classList.add('hidden'); renderHistory(); }

function openSettings(server='',serverMode=''){
  if(document.body.classList.contains('auth-locked')){modal('Configurações','Entre no ID LAUDO para acessar as configurações.');return;}
  if($('settingsView') && !$('settingsView').classList.contains('hidden')) return;
  settingsPreviousTab=activeMainTab;
  settingsReturnEditor=!!($('editorView') && !$('editorView').classList.contains('hidden'));
  if($('settingsServerValue')) $('settingsServerValue').textContent=server||location.host||'—';
  if($('settingsServerMode')) $('settingsServerMode').textContent=(serverMode||((location.protocol==='https:')?'ONLINE':'LOCAL')).toUpperCase();
  document.body.classList.add('settings-mode');
  if(settingsReturnEditor){document.body.classList.remove('editor-mode');switchView('settingsView','editorView','right');}
  else switchView('settingsView',`${activeMainTab}View`,'right');
  refreshSettingsData();
}
window.openSettingsFromNative=function(server='',serverMode=''){ openSettings(String(server||''),String(serverMode||'')); return 'OK'; };
function closeSettings(){
  document.body.classList.remove('settings-mode');
  if(settingsReturnEditor){document.body.classList.add('editor-mode');switchView('editorView','settingsView','left');settingsReturnEditor=false;return;}
  switchView(`${settingsPreviousTab}View`,'settingsView','left');
  activeMainTab=settingsPreviousTab; updateBottomNav();
}
function useOnlineServer(){
  if(navigator.userAgent.includes('IDLAUDOAndroid')) window.location.href='idlaudo://use-cloud';
  else modal('Servidor online','Esta opção é controlada pelo APK Android. No navegador, você já está conectado ao endereço atual.');
}
function openCloudSettings(){
  if(navigator.userAgent.includes('IDLAUDOAndroid')) window.location.href='idlaudo://cloud';
  else modal('Servidor online','A alteração do endereço do Render é feita pelo APK Android.');
}
function openIpSettings(){
  if(navigator.userAgent.includes('IDLAUDOAndroid')) window.location.href='idlaudo://ip';
  else modal('Modo local / IP','O IP local é uma contingência e é configurado pelo APK Android.');
}
function toggleSettingsCard(button){
  const card=button?.closest('.settingsCard'); if(!card) return;
  card.classList.toggle('collapsed');
  const key=card.dataset.settingsCard||'';
  try{localStorage.setItem(`cfgCollapse:${key}`,card.classList.contains('collapsed')?'1':'0');}catch{}
}
function restoreSettingsCollapse(){
  document.querySelectorAll('.settingsCard').forEach(card=>{try{if(localStorage.getItem(`cfgCollapse:${card.dataset.settingsCard||''}`)==='1')card.classList.add('collapsed');}catch{}});
}

function formFieldContainer(el){
  return el.closest('.field,.testCard,.reactiveToggle,.checks label') || el;
}
function fieldDisplayName(el){
  const box=formFieldContainer(el);
  const label=box.querySelector?.('label'); if(label) return label.textContent.trim().replace(/\s+/g,' ');
  const h=box.querySelector?.('h4'); if(h) return h.textContent.trim();
  const b=box.querySelector?.('b'); if(b && box.classList.contains('energyRow')) return `${b.textContent.trim()} • ${el.id}`;
  return el.getAttribute('aria-label') || el.id;
}
function indexFormStructure(){
  document.querySelectorAll('#laudoForm .step').forEach((step,si)=>{
    step.querySelectorAll(':scope > .card').forEach((card,ci)=>{
      const key=`s${si}_c${ci}`; card.dataset.formCardKey=key;
      card.querySelectorAll('input[id],select[id],textarea[id]').forEach(el=>{ if(el.type!=='hidden') el.dataset.formFieldKey=el.id; });
    });
  });
}
function getFormVisibility(){
  const fv=appConfig.form_visibility||{};
  return {cards:{...(fv.cards||{})},fields:{...(fv.fields||{})}};
}
function applyFormVisibility(){
  const fv=getFormVisibility();
  document.querySelectorAll('#laudoForm .card[data-form-card-key]').forEach(card=>{
    const hasRequired=[...card.querySelectorAll('[id]')].some(el=>REQUIRED_FORM_FIELDS.has(el.id));
    card.classList.toggle('configHidden',!hasRequired && fv.cards[card.dataset.formCardKey]===false);
  });
  document.querySelectorAll('#laudoForm [data-form-field-key]').forEach(el=>{
    const hidden=fv.fields[el.id]===false && !REQUIRED_FORM_FIELDS.has(el.id);
    formFieldContainer(el).classList.toggle('configHiddenField',hidden);
  });
}
function renderFormConfig(){
  const box=$('formConfigList'); if(!box) return;
  const fv=getFormVisibility(); box.innerHTML='';
  document.querySelectorAll('#laudoForm .step').forEach((step,si)=>{
    const stepWrap=document.createElement('div'); stepWrap.className='formConfigStep';
    stepWrap.innerHTML=`<div class="formConfigStepTitle"><b>${si+1}. ${esc(step.dataset.title||'Etapa')}</b></div>`;
    step.querySelectorAll(':scope > .card').forEach(card=>{
      const key=card.dataset.formCardKey; const title=card.querySelector('.cardHead h3')?.textContent?.trim() || 'Card';
      const cardRow=document.createElement('div'); cardRow.className='formConfigCard';
      const lockedCard=[...card.querySelectorAll('[id]')].some(el=>REQUIRED_FORM_FIELDS.has(el.id));
      const visible=lockedCard || fv.cards[key]!==false;
      cardRow.innerHTML=`<div class="formConfigCardHead"><div><b>${esc(title)}</b><small>${lockedCard?'Card obrigatório':'Card'}</small></div><label class="switch ${lockedCard?'locked':''}"><input type="checkbox" ${visible?'checked':''} ${lockedCard?'disabled':''}><span></span></label></div><div class="formConfigFields"></div>`;
      const cardSwitch=cardRow.querySelector('.switch input'); if(!lockedCard) cardSwitch.onchange=async e=>{fv.cards[key]=!!e.target.checked;appConfig.form_visibility=fv;applyFormVisibility();await saveFormVisibility();};
      const fields=cardRow.querySelector('.formConfigFields');
      const seen=new Set();
      card.querySelectorAll('input[id],select[id],textarea[id]').forEach(el=>{
        if(el.type==='hidden'||seen.has(el.id))return;seen.add(el.id);
        const locked=REQUIRED_FORM_FIELDS.has(el.id); const fvis=fv.fields[el.id]!==false || locked;
        const row=document.createElement('div');row.className='formConfigField';
        row.innerHTML=`<div><span>${esc(fieldDisplayName(el))}</span>${locked?'<small>Obrigatório</small>':''}</div><label class="switch ${locked?'locked':''}"><input type="checkbox" ${fvis?'checked':''} ${locked?'disabled':''}><span></span></label>`;
        const inp=row.querySelector('input'); if(!locked) inp.onchange=async e=>{fv.fields[el.id]=!!e.target.checked;appConfig.form_visibility=fv;applyFormVisibility();await saveFormVisibility();};
        fields.appendChild(row);
      });
      stepWrap.appendChild(cardRow);
    });
    box.appendChild(stepWrap);
  });
}
async function saveFormVisibility(){
  try{await api('/api/config/form-visibility',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:appConfig.form_visibility||{}})});}catch(e){toast(e.message)}
}
async function resetFormVisibility(){
  appConfig.form_visibility={cards:{},fields:{}}; applyFormVisibility(); renderFormConfig(); await saveFormVisibility(); toast('Todos os cards e campos foram reexibidos.');
}
function renderBackendInfo(){
  const b=appConfig.backend||{};
  if($('cfgBackendMode')) $('cfgBackendMode').textContent=b.mode||'LOCAL';
  if($('cfgBackendDb')) $('cfgBackendDb').textContent=b.database||'SQLite';
  if($('cfgCatalogMode')) $('cfgCatalogMode').textContent=bootstrap?.source?.includes('POSTGRESQL')?'PostgreSQL':'SQLite';
  if($('cfgAuthMode')) $('cfgAuthMode').textContent=b.auth||'CONFIGURAR';
}
function renderAccountInfo(){
  if($('cfgCurrentName')) $('cfgCurrentName').textContent=currentUser?.nome||currentUser?.usuario||'—';
  if($('cfgCurrentEmail')) $('cfgCurrentEmail').textContent=currentUser?.email||'—';
  if($('cfgCurrentRole')) $('cfgCurrentRole').textContent=currentUser?.perfil||'—';
  renderNativeSecuritySettings();
}
function applySettingsPermissions(){
  const admin=String(currentUser?.perfil||'').toUpperCase()==='ADMIN' || !authConfig.requested;
  $('adminUsersCard')?.classList.toggle('hidden',!admin);
  $('adminFormCard')?.classList.toggle('hidden',!admin);
}
async function refreshSettingsData(){
  try{const r=await api('/api/config');appConfig.users=r.users||[];appConfig.form_visibility=r.form_visibility||{};appConfig.backend=r.backend||{};if(r.current_user)currentUser=r.current_user;renderConfigUsers();renderFormConfig();renderBackendInfo();renderAccountInfo();applySettingsPermissions();applyFormVisibility();restoreSettingsCollapse();}catch(e){toast(e.message)}
}
async function loadAppConfig(){
  try{const r=await api('/api/config');appConfig.users=r.users||[];appConfig.form_visibility=r.form_visibility||{};appConfig.backend=r.backend||{};if(r.current_user)currentUser=r.current_user;}catch{appConfig={users:[],form_visibility:{},backend:{}}}
  renderBackendInfo();renderAccountInfo();applySettingsPermissions();applyFormVisibility();
}
function renderConfigUsers(){
  const box=$('configUsersList');if(!box)return;box.innerHTML='';
  if(!appConfig.users.length){box.innerHTML='<div class="configEmpty">Nenhum usuário cadastrado.</div>';return;}
  appConfig.users.forEach(u=>{
    const active=!!Number(u.ativo);
    const row=document.createElement('div');row.className=`configUserRow ${active?'':'userSuspended'}`;
    row.innerHTML=`<div><b>${esc(u.nome)}</b><span>${esc(u.usuario)}${u.email?' • '+esc(u.email):''}</span><small>${active?'ATIVO':'SUSPENSO'}</small></div><span class="roleBadge ${u.perfil==='ADMIN'?'admin':''}">${esc(u.perfil)}</span><div class="configUserActions"><button class="secondary" type="button">EDITAR</button><button class="secondary" type="button">${active?'SUSPENDER':'REATIVAR'}</button><button class="secondary" type="button">RESET SENHA</button><button class="danger" type="button">EXCLUIR</button></div>`;
    const bs=row.querySelectorAll('button');
    bs[0].onclick=()=>editConfigUser(u.id);
    bs[1].onclick=()=>confirmBox(active?'Suspender usuário':'Reativar usuário',active?`Suspender o acesso de ${u.nome}?`:`Reativar o acesso de ${u.nome}?`,()=>suspendConfigUser(u.id,active));
    bs[2].onclick=()=>confirmBox('Redefinir senha',`Enviar um link de redefinição para ${u.email||u.nome}?`,()=>resetConfigUserPassword(u.id));
    bs[3].onclick=()=>confirmBox('Excluir usuário',`Excluir ${u.nome}? O acesso será removido do Supabase Auth.`,()=>deleteConfigUser(u.id));
    box.appendChild(row);
  });
}
function editConfigUser(id){const u=appConfig.users.find(x=>Number(x.id)===Number(id));if(!u)return;editingConfigUserId=u.id;setVal('cfgUserName',u.nome);setVal('cfgUserLogin',u.usuario);setVal('cfgUserEmail',u.email||'');setVal('cfgUserRole',u.perfil);const b=document.querySelector('.cfgAddUser');if(b)b.textContent='SALVAR';}
function resetConfigUserForm(){editingConfigUserId=null;setVal('cfgUserName','');setVal('cfgUserLogin','');setVal('cfgUserEmail','');setVal('cfgUserRole','OPERADOR');const b=document.querySelector('.cfgAddUser');if(b)b.textContent='ADICIONAR';}
async function saveConfigUser(){
  const nome=val('cfgUserName'),usuario=val('cfgUserLogin'),email=val('cfgUserEmail'),perfil=val('cfgUserRole')||'OPERADOR';
  if(!nome||!usuario||!email){modal('Usuários','Preencha Nome, Usuário e E-mail.');return;}
  const existing=editingConfigUserId?appConfig.users.find(x=>Number(x.id)===Number(editingConfigUserId)):null;
  try{
    const r=await api('/api/config/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:editingConfigUserId,data:{nome,usuario,email,perfil,ativo:existing?!!Number(existing.ativo):true}})});
    const created=!editingConfigUserId;resetConfigUserForm();await refreshSettingsData();
    if(created)modal('Usuário criado',r.reset_sent?'O usuário foi criado e recebeu um e-mail para definir a senha.':'Usuário criado. Se o e-mail não chegar, use RESET SENHA para enviar um novo link.');else toast('Usuário salvo.');
  }catch(e){modal('Usuários',e.message)}
}
async function suspendConfigUser(id,currentlyActive){try{await api(`/api/config/users/${id}/suspend`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({suspended:!!currentlyActive})});await refreshSettingsData();toast(currentlyActive?'Usuário suspenso.':'Usuário reativado.');}catch(e){modal('Usuários',e.message)}}
async function resetConfigUserPassword(id){try{await api(`/api/config/users/${id}/reset-password`,{method:'POST'});modal('Redefinição enviada','Se o e-mail estiver disponível, o usuário receberá um link para criar uma nova senha.');}catch(e){modal('Usuários',e.message)}}
async function deleteConfigUser(id){try{await api(`/api/config/users/${id}`,{method:'DELETE'});await refreshSettingsData();toast('Usuário excluído.');}catch(e){modal('Usuários',e.message)}}
window.addEventListener('beforeinstallprompt',e=>{
  e.preventDefault(); deferredInstallPrompt=e;
  const b=$('installBtn'); if(b) b.classList.remove('hidden');
});
window.addEventListener('appinstalled',()=>{ deferredInstallPrompt=null; const b=$('installBtn'); if(b)b.classList.add('hidden'); toast('ID LAUDO instalado.'); });
async function installPwa(){
  if(!deferredInstallPrompt){ modal('Instalar ID LAUDO','A instalação como aplicativo fica disponível quando o sistema é aberto em um endereço seguro (HTTPS) compatível com PWA. Você pode continuar usando normalmente no navegador ou usar o projeto Android incluído nesta versão.'); return; }
  deferredInstallPrompt.prompt(); await deferredInstallPrompt.userChoice; deferredInstallPrompt=null; const b=$('installBtn'); if(b)b.classList.add('hidden');
}


function showQuickHelp(){
  const src=bootstrap?.source || 'Base ID CAMPS';
  modal('ID LAUDO', `Seu app de laudos em campo.
Crie, acompanhe e consulte seus espelhos de forma rápida.

BASE ATUAL
${src}

COMO USAR
• Campos numéricos abrem teclado numérico.
• Invólucro e TOI aceitam letras e números.
• SALVAR grava o rascunho no banco online e volta para Laudos.
• FINALIZAR envia para o PostgreSQL e cria a notificação para revisão no ID CAMPS.`);
}

function syncToiFromProcess(force=false){
  const processo=val('processo');
  const toi=$('pre_toi_numero');
  if(!toi) return;
  if(force || !toiManuallyEdited || !val('pre_toi_numero')){
    setVal('pre_toi_numero', processo);
    toiManuallyEdited=false;
  }
}

function bindProcessToToi(){
  const processo=$('processo');
  const toi=$('pre_toi_numero');
  if(!processo || !toi) return;
  processo.addEventListener('input',()=>syncToiFromProcess(false));
  toi.addEventListener('input',()=>{
    toiManuallyEdited=val('pre_toi_numero')!==val('processo');
  });
}

function bindInputModes(){
  const numericIds=['numero_laudo','ano','instalacao','ano_fabricacao','elementos','numero_fios','laudosSearch','historySearch'];
  const decimalIds=['leitura_kw_inicial','leitura_kw_final','ea_cos_cn','ea_cos_cp','ea_cos_ci','ea_nominal','ea_pequena','ea_indutiva','leitura_kvar_inicial','leitura_kvar_final','er_nominal','er_pequena','er_indutiva'];
  numericIds.forEach(id=>{const el=$(id); if(!el) return; el.setAttribute('inputmode','numeric'); el.setAttribute('pattern','[0-9]*');});
  decimalIds.forEach(id=>{const el=$(id); if(!el) return; el.setAttribute('inputmode','decimal');});
}

function sanitizeNumericFields(){
  const numericIds=['numero_laudo','ano','instalacao','ano_fabricacao','elementos','numero_fios','laudosSearch','historySearch'];
  numericIds.forEach(id=>{const el=$(id); if(!el) return; el.addEventListener('input',()=>{el.value=el.value.replace(/\D+/g,'');});});
  const decimalIds=['leitura_kw_inicial','leitura_kw_final','ea_cos_cn','ea_cos_cp','ea_cos_ci','ea_nominal','ea_pequena','ea_indutiva','leitura_kvar_inicial','leitura_kvar_final','er_nominal','er_pequena','er_indutiva'];
  decimalIds.forEach(id=>{const el=$(id); if(!el) return; el.addEventListener('input',()=>{el.value=el.value.replace(/[^0-9,.-]/g,'');});});
}

function val(id){ return String($(id)?.value ?? '').trim(); }
function setVal(id,v){ if($(id)) $(id).value = v ?? ''; }
function checked(id){ return !!$(id)?.checked; }
function setChecked(id,v){ if($(id)) $(id).checked=!!v; }
function todayIso(){ const d=new Date(); const off=d.getTimezoneOffset(); return new Date(d.getTime()-off*60000).toISOString().slice(0,10); }
function esc(s){ return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function toast(text){ const t=$('toast'); t.textContent=text; t.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>t.classList.remove('show'),2600); }
function modal(title,text,buttons=[['OK','primary',()=>closeModal()]]){ $('modalTitle').textContent=title; $('modalText').textContent=text; const a=$('modalActions'); a.innerHTML=''; buttons.forEach(([label,cls,fn])=>{const b=document.createElement('button');b.textContent=label;b.className=cls;b.onclick=fn;a.appendChild(b);}); $('modal').classList.remove('hidden'); }
function closeModal(){ $('modal').classList.add('hidden'); }
function confirmBox(title,text,onYes){ modal(title,text,[['CANCELAR','secondary',closeModal],['CONFIRMAR','primary',()=>{closeModal();onYes();}]]); }
async function publicApi(url, options={}){
  const r=await fetch(url,{cache:'no-store',...options}); let j={};
  try{j=await r.json();}catch{}
  if(!r.ok) throw new Error(j.detail || j.message || 'Falha na operação.');
  return j;
}
function nativeBridge(){return window.IdLaudoNative||null;}
function nativeSetAuthenticated(value){try{nativeBridge()?.setAuthenticated(!!value);}catch{}}
function nativeBiometricAvailable(){try{return !!nativeBridge()?.isBiometricAvailable();}catch{return false;}}
function nativeBiometricEnabled(){try{return !!nativeBridge()?.isBiometricEnabled();}catch{return false;}}
function updateBiometricLoginButton(){
  const btn=$('biometricLoginBtn');if(!btn)return;
  const hasSession=!!(authState.access_token||authState.refresh_token);
  btn.classList.toggle('hidden',!(nativeBiometricAvailable()&&nativeBiometricEnabled()&&hasSession));
}
function requestBiometricLogin(){
  if(!nativeBiometricAvailable()){showAuthError('Biometria não disponível ou não cadastrada neste aparelho.');return;}
  if(!nativeBiometricEnabled()){showAuthError('A biometria ainda não está ativada para esta conta.');return;}
  showAuthError('');try{nativeBridge()?.requestBiometricLogin();}catch{showAuthError('Não foi possível abrir a biometria.');}
}
async function idLaudoBiometricUnlocked(){
  showAuthError('');
  const btn=$('biometricLoginBtn');if(btn){btn.disabled=true;btn.querySelector('span:last-child').textContent='VALIDANDO...';}
  try{
    const ok=await enterWithStoredSession();
    if(!ok||!currentUser)throw new Error('Sua sessão expirou. Entre com e-mail e senha.');
    if(currentUser?.must_change_password){showChangePassword('forced');return;}
    if(!unlockApp('BIOMETRIC'))throw new Error('Não foi possível validar sua conta.');
    if(!appStarted)await startApp();else{await loadAppConfig();await loadRecords();}
  }catch(e){showLoginView();showAuthError(e.message||'Não foi possível entrar com biometria.');try{nativeBridge()?.disableBiometric();}catch{}}
  finally{if(btn){btn.disabled=false;btn.querySelector('span:last-child').textContent='ENTRAR COM BIOMETRIA';}updateBiometricLoginButton();}
}
function idLaudoBiometricError(message='Não foi possível validar a biometria.'){
  if(document.body.classList.contains('auth-locked'))showAuthError(message);else toast(message);
}
function idLaudoBiometricCanceled(){showAuthError('');}
function idLaudoBiometricEnabled(){toast('Biometria ativada.');renderNativeSecuritySettings();updateBiometricLoginButton();}
function idLaudoBiometricDisabled(){toast('Biometria desativada.');renderNativeSecuritySettings();updateBiometricLoginButton();}
function enableBiometricSetting(){try{nativeBridge()?.enableBiometric();}catch{toast('Biometria disponível somente no APK Android.');}}
function toggleBiometricSetting(){
  if(!nativeBiometricAvailable()){modal('Biometria','Biometria não disponível ou não cadastrada neste aparelho.');return;}
  if(nativeBiometricEnabled())confirmBox('Desativar biometria','Deseja voltar a exigir e-mail e senha ao abrir o aplicativo?',()=>{try{nativeBridge()?.disableBiometric();}catch{}});
  else enableBiometricSetting();
}
function offerBiometricAfterLogin(){
  if(!nativeBiometricAvailable()||nativeBiometricEnabled())return;
  let asked=false;try{asked=localStorage.getItem('idlaudo.bio.offer.v27')==='1';}catch{}
  if(asked)return;try{localStorage.setItem('idlaudo.bio.offer.v27','1');}catch{}
  setTimeout(()=>modal('Ativar biometria','Quer usar a digital/biometria deste aparelho para entrar mais rápido nas próximas vezes? Sua senha não é armazenada para isso.',[
    ['AGORA NÃO','secondary',closeModal],['ATIVAR','primary',()=>{closeModal();enableBiometricSetting();}]
  ]),350);
}
function notificationPermissionState(){try{return String(nativeBridge()?.notificationPermissionState()||'SOMENTE APK');}catch{return 'SOMENTE APK';}}
function requestNotificationPermission(){try{nativeBridge()?.requestNotificationPermission();}catch{modal('Notificações','A permissão de notificações está disponível no APK Android.');}}
function idLaudoNotificationPermissionChanged(state){toast(state==='ATIVADA'?'Notificações ativadas.':'Notificações não foram autorizadas.');renderNativeSecuritySettings();}
function renderNativeSecuritySettings(){
  const bioStatus=$('cfgBiometricStatus'),bioAction=$('cfgBiometricAction'),notStatus=$('cfgNotificationStatus'),notAction=$('cfgNotificationAction');
  if(bioStatus){const available=nativeBiometricAvailable(),enabled=nativeBiometricEnabled();bioStatus.textContent=!available?'INDISPONÍVEL':enabled?'ATIVADA':'DESATIVADA';bioStatus.className=enabled?'on':'off';}
  if(bioAction){const available=nativeBiometricAvailable(),enabled=nativeBiometricEnabled();bioAction.disabled=!available;bioAction.textContent=enabled?'DESATIVAR':'ATIVAR';}
  if(notStatus){const state=notificationPermissionState();notStatus.textContent=state;notStatus.className=state==='ATIVADA'?'on':'off';}
  if(notAction){const state=notificationPermissionState();notAction.disabled=state==='ATIVADA'||state==='SOMENTE APK';notAction.textContent=state==='ATIVADA'?'ATIVADA':'ATIVAR';}
}
window.idLaudoBiometricUnlocked=idLaudoBiometricUnlocked;
window.idLaudoBiometricError=idLaudoBiometricError;
window.idLaudoBiometricCanceled=idLaudoBiometricCanceled;
window.idLaudoBiometricEnabled=idLaudoBiometricEnabled;
window.idLaudoBiometricDisabled=idLaudoBiometricDisabled;
window.idLaudoNotificationPermissionChanged=idLaudoNotificationPermissionChanged;

function saveAuthState(){
  try{localStorage.setItem(AUTH_STORAGE_KEY,JSON.stringify(authState||{}));}catch{}
}
function clearLegacyAuthState(){
  try{LEGACY_AUTH_STORAGE_KEYS.forEach(k=>localStorage.removeItem(k));}catch{}
}
function loadAuthState(){
  clearLegacyAuthState();
  try{const x=JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY)||'{}');authState={access_token:x.access_token||'',refresh_token:x.refresh_token||''};}catch{authState={access_token:'',refresh_token:''};}
}
function clearAuthState(){authState={access_token:'',refresh_token:''};currentUser=null;try{localStorage.removeItem(AUTH_STORAGE_KEY);clearLegacyAuthState();}catch{}}
async function purgeLegacyUiCache(){
  // V27: evita WebView reutilizar HTML/JS antigos durante login e biometria.
  try{if('caches' in window){for(const key of await caches.keys()) await caches.delete(key);}}catch{}
  try{if('serviceWorker' in navigator){for(const reg of await navigator.serviceWorker.getRegistrations()) await reg.unregister();}}catch{}
}
async function refreshAuthSession(){
  if(!authState.refresh_token)return false;
  try{
    const r=await publicApi('/api/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:authState.refresh_token})});
    authState={access_token:r.session?.access_token||'',refresh_token:r.session?.refresh_token||authState.refresh_token};currentUser=r.user||currentUser;saveAuthState();return !!authState.access_token;
  }catch{clearAuthState();return false;}
}
async function api(url, options={}){
  const headers={...(options.headers||{})};
  if(authState.access_token) headers.Authorization=`Bearer ${authState.access_token}`;
  const r=await fetch(url,{cache:'no-store',...options,headers}); let j={}; try{j=await r.json();}catch{}
  if(r.status===401 && authState.refresh_token && !options.__retried && !url.startsWith('/api/auth/')){
    const ok=await refreshAuthSession();
    if(ok)return api(url,{...options,__retried:true});
  }
  if(!r.ok) throw new Error(j.detail || j.message || 'Falha na operação.'); return j;
}
function togglePassword(id,btn){const el=$(id);if(!el)return;el.type=el.type==='password'?'text':'password';if(btn)btn.textContent=el.type==='password'?'◉':'○';}
function showAuthError(text=''){const box=$('authError');if(!box)return;box.textContent=text;box.classList.toggle('hidden',!text);}
function showLoginView(){
  authGateState='LOCKED';
  nativeSetAuthenticated(false);
  document.body.classList.remove('auth-booting');document.body.classList.add('auth-locked');
  $('authView')?.classList.remove('hidden');$('forgotView')?.classList.add('hidden');$('resetPasswordView')?.classList.add('hidden');
  showAuthError('');try{if(!val('loginEmail'))setVal('loginEmail',localStorage.getItem('idlaudo.last.email')||'');}catch{}updateBiometricLoginButton();setTimeout(()=>$('loginEmail')?.focus(),80);
}
function unlockApp(mode='AUTHENTICATED'){
  // Falha fechada: servidor online com login solicitado só libera a interface com usuário validado.
  if(authConfig?.requested && !currentUser) return false;
  authGateState=mode;
  nativeSetAuthenticated(true);
  document.body.classList.remove('auth-booting','auth-locked');
  $('authView')?.classList.add('hidden');$('forgotView')?.classList.add('hidden');$('resetPasswordView')?.classList.add('hidden');
  return true;
}
function openForgotPassword(){setVal('forgotEmail',val('loginEmail'));$('authView')?.classList.add('hidden');$('forgotView')?.classList.remove('hidden');setTimeout(()=>$('forgotEmail')?.focus(),80);}
function closeForgotPassword(){$('forgotView')?.classList.add('hidden');$('authView')?.classList.remove('hidden');}
async function forgotPasswordSubmit(){
  const email=val('forgotEmail');if(!email){modal('Recuperar senha','Informe seu e-mail.');return;}
  try{const r=await publicApi('/api/auth/forgot-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});modal('Verifique seu e-mail',r.message||'Se o e-mail estiver cadastrado, enviaremos as instruções.');closeForgotPassword();}catch(e){modal('Recuperar senha',e.message)}
}
function recoveryParams(){
  const rawHash=location.hash&&location.hash.startsWith('#')?location.hash.slice(1):'';
  const rawQuery=location.search&&location.search.startsWith('?')?location.search.slice(1):'';
  const hash=new URLSearchParams(rawHash);const query=new URLSearchParams(rawQuery);
  const pick=(key)=>hash.get(key)||query.get(key)||'';
  return {rawHash,rawQuery,hash,query,pick};
}
function recoveryPayloadFromCurrentUrl(){
  const {rawHash,rawQuery,pick}=recoveryParams();
  if((pick('type')||'')!=='recovery'||!pick('access_token'))return '';
  return rawHash?('#'+rawHash):(rawQuery?('?'+rawQuery):'');
}
function rememberRecoveryPayload(payload){
  if(!payload)return;
  window.__idlaudoRecoveryPayload=payload;
  try{sessionStorage.setItem('idlaudo.recovery.payload',payload);}catch{}
}
function rememberedRecoveryPayload(){
  if(window.__idlaudoRecoveryPayload)return window.__idlaudoRecoveryPayload;
  try{return sessionStorage.getItem('idlaudo.recovery.payload')||'';}catch{return '';}
}
function clearRememberedRecoveryPayload(){
  window.__idlaudoRecoveryPayload='';
  try{sessionStorage.removeItem('idlaudo.recovery.payload');}catch{}
}
function parseRecoverySession(){
  const payload=recoveryPayloadFromCurrentUrl();
  if(payload)rememberRecoveryPayload(payload);
  const {pick}=recoveryParams();
  const err=pick('error_description')||pick('error');
  if(err){window.__idlaudoRecoveryError=decodeURIComponent(String(err).replace(/\+/g,' '));return false;}
  const type=pick('type');const access=pick('access_token');const refresh=pick('refresh_token');
  if(type==='recovery'&&access){
    authState={access_token:access,refresh_token:refresh};saveAuthState();
    history.replaceState(null,'','/password-reset?password_recovery=1');passwordChangeMode='recovery';return true;
  }
  return false;
}
function recoveryDeepLinkTarget(){
  const payload=rememberedRecoveryPayload();
  return payload?('idlaudo://password-reset'+payload):'';
}
function tryOpenRecoveryInApp(){
  if(nativeBridge())return false;
  if(!/Android/i.test(navigator.userAgent||''))return false;
  const target=recoveryDeepLinkTarget();if(!target)return false;
  try{location.href=target;return true;}catch{return false;}
}
function openRecoveryInApp(){
  const target=recoveryDeepLinkTarget();
  if(!target){modal('Recuperar senha','O link não contém uma sessão de recuperação válida. Solicite um novo e-mail.');return;}
  location.href=target;
}
function showChangePassword(mode='forced'){
  passwordChangeMode=mode;document.body.classList.remove('auth-booting');document.body.classList.add('auth-locked');
  $('authView')?.classList.add('hidden');$('forgotView')?.classList.add('hidden');$('resetPasswordView')?.classList.remove('hidden');
  const forced=mode==='forced';$('resetPasswordTitle').textContent=forced?'Troque sua senha inicial':'Criar nova senha';
  $('resetPasswordText').textContent=forced?'Por segurança, a senha temporária só pode ser usada neste primeiro acesso.':'Use pelo menos 8 caracteres.';
  const openApp=$('openRecoveryAppBtn');if(openApp){openApp.classList.toggle('hidden',!(mode==='recovery'&&!nativeBridge()&&/Android/i.test(navigator.userAgent||'')));}
  setVal('newPassword','');setVal('newPasswordConfirm','');setTimeout(()=>$('newPassword')?.focus(),80);
}
function openChangePassword(){showChangePassword('settings');}
async function changePasswordSubmit(){
  const p1=val('newPassword'),p2=val('newPasswordConfirm');
  if(p1.length<8){modal('Nova senha','Use pelo menos 8 caracteres.');return;}
  if(p1!==p2){modal('Nova senha','As senhas digitadas não são iguais.');return;}
  try{
    await api('/api/auth/change-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p1})});
    if(currentUser)currentUser.must_change_password=false;
    const mode=passwordChangeMode;if(mode==='recovery')clearRememberedRecoveryPayload();unlockApp();
    if(!appStarted)await startApp(); else {await loadAppConfig();renderAccountInfo();}
    offerBiometricAfterLogin();
    modal('Senha atualizada','Sua nova senha foi salva com sucesso.');
    if(mode==='settings')setTimeout(()=>openSettings(),100);
  }catch(e){modal('Nova senha',e.message)}
}
async function loginSubmit(){
  const email=val('loginEmail'),password=val('loginPassword');if(!email||!password){showAuthError('Informe e-mail e senha.');return;}
  const btn=$('loginBtn');if(btn){btn.disabled=true;btn.textContent='ENTRANDO...';}showAuthError('');
  try{
    const r=await publicApi('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});
    authState={access_token:r.session?.access_token||'',refresh_token:r.session?.refresh_token||''};currentUser=r.user||null;saveAuthState();try{localStorage.setItem('idlaudo.last.email',email);}catch{}setVal('loginPassword','');
    if(currentUser?.must_change_password){showChangePassword('forced');return;}
    if(!unlockApp('AUTHENTICATED')){showLoginView();showAuthError('Não foi possível validar a sessão. Entre novamente.');return;} if(!appStarted)await startApp(); else {await loadAppConfig();await loadRecords();} offerBiometricAfterLogin();
  }catch(e){showAuthError(e.message)}finally{if(btn){btn.disabled=false;btn.textContent='ENTRAR';}}
}
async function logoutApp(){
  try{if(authState.access_token)await api('/api/auth/logout',{method:'POST'});}catch{}
  try{nativeBridge()?.clearBiometricOnLogout();}catch{}clearAuthState();authGateState='LOCKED';nativeSetAuthenticated(false);document.body.classList.remove('settings-mode','editor-mode');showLoginView();
}
async function enterWithStoredSession(){
  loadAuthState();if(!authState.access_token&&!authState.refresh_token)return false;
  if(!authState.access_token&&!(await refreshAuthSession()))return false;
  try{const r=await api('/api/auth/me');currentUser=r.user||null;return !!currentUser;}catch{if(await refreshAuthSession()){try{const r=await api('/api/auth/me');currentUser=r.user||null;return !!currentUser;}catch{}}clearAuthState();return false;}
}
async function init(){
  // V27: a tela de login continua sendo a barreira inicial; a sessão anterior só é liberada pela biometria.
  authGateState='BOOT';
  await purgeLegacyUiCache();
  clearLegacyAuthState();
  // V30: o e-mail retorna para /password-reset no Render. A sessão é lida primeiro,
  // a tela de nova senha é preparada e só então tentamos abrir o APK no Android.
  let recovery=parseRecoverySession();
  try{authConfig=await publicApi('/api/auth/config');}
  catch(e){showLoginView();showAuthError(`Não foi possível consultar o login. ${e.message}`);return;}

  if(authConfig.requested){
    // Em modo online, configuração incompleta nunca libera o aplicativo.
    if(!authConfig.configured){
      showLoginView();
      const b=$('authSetupBox');if(b){b.classList.remove('hidden');b.textContent='Login obrigatório. O Supabase Auth ainda precisa ser concluído no Render.';}
      return;
    }
    if(!authConfig.admin_api || !authConfig.admin_ready){
      const b=$('authSetupBox');if(b){b.classList.remove('hidden');b.textContent='Login obrigatório. O administrador inicial ainda não está pronto; confira as variáveis do Supabase Auth no Render.';}
    }
    if(recovery){
      try{
        const r=await api('/api/auth/me');currentUser=r.user||null;showChangePassword('recovery');
        // No Android, tenta abrir o APK. Se o sistema bloquear o deep link, a própria
        // página web permanece na tela "Criar nova senha" e o botão ABRIR NO APP continua disponível.
        if(!nativeBridge() && /Android/i.test(navigator.userAgent||'')) setTimeout(()=>tryOpenRecoveryInApp(),450);
      }
      catch(e){clearAuthState();clearRememberedRecoveryPayload();showLoginView();showAuthError(window.__idlaudoRecoveryError||'O link de redefinição expirou. Solicite um novo link.');}
      return;
    }
    // V27: sessão anterior só pode ser reutilizada depois da biometria nativa.
    loadAuthState();
    showLoginView();
    if(nativeBiometricEnabled() && (authState.access_token||authState.refresh_token)){
      updateBiometricLoginButton();
      setTimeout(()=>requestBiometricLogin(),320);
    }else{
      clearAuthState();
      updateBiometricLoginButton();
    }
    return;
  }

  // Bypass permitido apenas quando o servidor informa explicitamente que está em modo local/sem autenticação.
  authGateState='LOCAL_BYPASS';
  unlockApp('LOCAL_BYPASS');
  await startApp();
}

async function startApp(){
  if(appStarted){await loadAppConfig();await loadRecords();return;}
  try{
    bootstrap = await api('/api/bootstrap');
    $('statModels').textContent=bootstrap.counts?.models ?? bootstrap.models.length;
    $('statObs').textContent=bootstrap.counts?.observations ?? bootstrap.observations.length;
    const src=bootstrap.source || 'Base ID CAMPS';
    if($('sourceInfo')) $('sourceInfo').textContent=src;
    buildStepper(); indexFormStructure(); fillPeople(); fillManufacturers(); fillPortarias(); bindInputModes(); sanitizeNumericFields(); bindProcessToToi(); setDefaults(); updateBottomNav(); await loadAppConfig(); await loadRecords();
    appStarted=true;
  }catch(e){ modal('ID LAUDO',`Não foi possível carregar a base de cadastros.\n\n${e.message}`); }
  // Online APK: não registra Service Worker; evita carregar interface antiga em cache.
  purgeLegacyUiCache();
}

function buildStepper(){
  const steps=[...document.querySelectorAll('.step')];
  $('stepper').innerHTML='';
  steps.forEach((s,i)=>{const b=document.createElement('button'); b.type='button'; b.className='stepTab'; b.textContent=`${i+1}. ${s.dataset.title}`; b.onclick=()=>goStep(i); $('stepper').appendChild(b);});
  updateStepUi();
}
function updateStepUi(){
  const steps=[...document.querySelectorAll('.step')];
  const dir=currentStep>=lastStep?'right':'left';
  steps.forEach((s,i)=>{
    s.classList.toggle('active',i===currentStep);
    if(i===currentStep){
      animateOnce(s, dir==='right' ? 'fx-enter-right' : 'fx-enter-left');
    }
  });
  [...document.querySelectorAll('.stepTab')].forEach((b,i)=>{b.classList.toggle('active',i===currentStep);b.classList.toggle('done',i<currentStep)});
  $('stepLabel').textContent=`Etapa ${currentStep+1} de ${steps.length}`;
  $('progressBar').style.width=`${((currentStep+1)/steps.length)*100}%`;
  $('prevBtn').disabled=currentStep===0;
  $('nextBtn').style.visibility=currentStep===steps.length-1?'hidden':'visible';
  if(currentStep===steps.length-1) renderReview();
  window.scrollTo({top:0,behavior:'smooth'});
  lastStep=currentStep;
}
function goStep(i){
  const target=Math.max(0,Math.min(document.querySelectorAll('.step').length-1,i));
  if(target!==currentStep) flushAutoSave();
  currentStep=target; updateStepUi();
  if(currentStep===1 || currentStep===6) refreshCatalog(true);
  if(currentStep===2) syncToiFromProcess(false);
}
function nextStep(){ goStep(currentStep+1); }
function prevStep(){ goStep(currentStep-1); }
function reviewAndExport(){ goStep(7); }

function fillSelect(id, items, preferred=''){
  const el=$(id); if(!el) return; el.innerHTML='<option value="">Selecione</option>';
  [...new Set(items||[])].forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;el.appendChild(o)});
  if(preferred && [...el.options].some(o=>o.value===preferred)) el.value=preferred;
}
function fillManufacturers(preferred=''){
  if(preferred && $('fabricante')) setVal('fabricante', preferred);
}
function filterManufacturers(term){
  const box=$('manufacturerResults'); if(!box) return;
  const q=String(term||'').trim().toUpperCase();
  box.innerHTML='';
  const rows=(bootstrap.manufacturers||[]).filter(name=>!q || String(name).toUpperCase().includes(q)).slice(0,40);
  rows.forEach(name=>{
    const item=document.createElement('div'); item.className='resultItem manufacturerItem';
    item.innerHTML=`<div><b>${esc(name)}</b><br><small>Fabricante cadastrado</small></div><code>SELECIONAR</code>`;
    item.onmousedown=e=>e.preventDefault();
    item.onclick=()=>{ setVal('fabricante',name); box.innerHTML=''; if(!val('modeloSearch')){ setVal('modeloSearch',name); filterModels(name); } };
    box.appendChild(item);
  });
  if(!rows.length && q){
    const item=document.createElement('div'); item.className='resultItem';
    item.innerHTML=`<div><b>Usar fabricante digitado</b><br><small>${esc(term)}</small></div><code>MANUAL</code>`;
    item.onmousedown=e=>e.preventDefault();
    item.onclick=()=>{ setVal('fabricante',term); box.innerHTML=''; };
    box.appendChild(item);
  }
}
function hideManufacturerResults(){ const box=$('manufacturerResults'); if(box) box.innerHTML=''; }

function toggleSign(id){
  const el=$(id); if(!el) return;
  let s=String(el.value||'').trim();
  if(!s){ el.value='-'; el.focus(); return; }
  if(s==='-'){ el.value=''; el.focus(); return; }
  el.value=s.startsWith('-')?s.slice(1):'-'+s.replace(/^\+/,'');
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.focus();
}
async function refreshCatalog(silent=true){
  if(silent && Date.now()-lastCatalogRefresh<5000) return;
  try{
    const currentManufacturer=val('fabricante');
    const currentPortaria=val('observacao_portaria');
    const data=await api('/api/bootstrap');
    bootstrap=data;
    lastCatalogRefresh=Date.now();
    if($('statModels')) $('statModels').textContent=bootstrap.counts?.models ?? bootstrap.models.length;
    if($('statObs')) $('statObs').textContent=bootstrap.counts?.observations ?? bootstrap.observations.length;
    if($('sourceInfo')) $('sourceInfo').textContent=bootstrap.source || 'Base ID CAMPS';
    if($('homeBaseLabel')) $('homeBaseLabel').textContent=bootstrap.source || 'ID CAMPS';
    fillPeople(true); fillManufacturers(currentManufacturer); fillPortarias();
    if(currentPortaria) setVal('observacao_portaria',currentPortaria);
    selectedObs=selectedObs.map(o=>bootstrap.observations.find(x=>String(x.id)===String(o.id))||o);
    renderSelectedObs();
    if(!silent) toast('Cadastros sincronizados com o ID CAMPS.');
  }catch(e){ if(!silent) modal('Sincronizar cadastros',e.message); }
}

function fillPeople(preserve=true){
  const p=bootstrap.people||{};
  const prev=preserve?{
    responsavel_ensaio:val('responsavel_ensaio'),equipamento_utilizado:val('equipamento_utilizado'),
    tecnico_1:val('tecnico_1'),responsavel_tecnico:val('responsavel_tecnico'),digitador:val('digitador')
  }:{};
  fillSelect('responsavel_ensaio',p.tecnico||[],prev.responsavel_ensaio||'');
  fillSelect('equipamento_utilizado',p.equipamento||[],prev.equipamento_utilizado||'');
  fillSelect('tecnico_1',p.tecnico_assinatura||[],prev.tecnico_1||'EMERSON LEITE');
  fillSelect('responsavel_tecnico',p.responsavel_tecnico||[],prev.responsavel_tecnico||'EMERSON LEITE');
  fillSelect('digitador',p.digitador||[],prev.digitador||'');
}

function fillPortarias(){
  const box=$('portariaResults'); if(box) box.innerHTML='';
}
function renderPortariaSearch(term){
  const box=$('portariaResults'); if(!box) return;
  const q=String(term||'').trim().toUpperCase();
  box.innerHTML='';
  const rows=(bootstrap.observation_portarias||[]).filter(text=>!q || String(text).toUpperCase().includes(q));
  rows.forEach((text,index)=>{
    const item=document.createElement('div'); item.className='resultItem portariaItem';
    item.innerHTML=`<div><b>${index+1}. ${esc(text)}</b></div><code>SELECIONAR</code>`;
    item.onmousedown=e=>e.preventDefault();
    item.onclick=()=>{ setVal('observacao_portaria',text); box.innerHTML=''; };
    box.appendChild(item);
  });
  if(!rows.length && q){
    const item=document.createElement('div'); item.className='resultItem';
    item.innerHTML=`<div><b>Nenhuma Portaria encontrada</b><br><small>${esc(term)}</small></div>`;
    box.appendChild(item);
  }
}
function hidePortariaResults(){ const box=$('portariaResults'); if(box) box.innerHTML=''; }



function setSaveState(text,cls=''){
  const el=$('saveState'); if(!el) return;
  el.textContent=text; el.className='saveState '+cls;
}
function meaningfulDraft(){
  return !!currentRecordId || !!(val('numero_laudo')||val('instalacao')||val('numero_serie')||val('modelo')||val('fabricante')||val('processo'));
}
function scheduleAutoSave(){
  if($('editorView')?.classList.contains('hidden') || !meaningfulDraft()) return;
  clearTimeout(autoSaveTimer);
  setSaveState('ALTERADO','dirty');
  autoSaveTimer=setTimeout(()=>saveDraft(true),1700);
}
function flushAutoSave(){
  if(!autoSaveTimer) return;
  clearTimeout(autoSaveTimer); autoSaveTimer=0;
  if(meaningfulDraft()) saveDraft(true);
}
function bindAutoSave(){
  const form=$('laudoForm'); if(!form) return;
  form.addEventListener('input',e=>{ if(e.target?.id) scheduleAutoSave(); });
  form.addEventListener('change',e=>{ if(e.target?.id) scheduleAutoSave(); });
}

function setDefaults(){
  setVal('ano',bootstrap.year || new Date().getFullYear()); setVal('data_ensaio',todayIso()); setVal('data_emissao',todayIso());
  setTipo('NR'); syncLacragem(); syncReactive(); updateEnergyResults();
}

function setTipo(tipo){
  tipo=tipo==='CV'?'CV':'NR'; setVal('tipo',tipo); $('tipoNR').classList.toggle('active',tipo==='NR'); $('tipoCV').classList.toggle('active',tipo==='CV');
  $('tipoDesc').textContent=tipo==='CV'?'CERTIFICADO DE VERIFICAÇÃO':'NOTIFICAÇÃO DE REPROVAÇÃO';
  $('conclusaoPreview').textContent=tipo==='CV'?'APROVADO':'REPROVADO - ver observações';
}

function filterModels(term){
  const q=String(term||'').trim().toUpperCase(); const box=$('modelResults'); box.innerHTML='';
  if(!q) return;
  const rows=bootstrap.models.filter(m=>String(m.modelo||'').toUpperCase().includes(q)||String(m.fabricante||'').toUpperCase().includes(q)).slice(0,30);
  rows.forEach((m)=>{const item=document.createElement('div');item.className='resultItem';item.innerHTML=`<div><b>${esc(m.modelo)}</b><br><small>${esc(m.fabricante||'-')} • ${esc(m.classe||'-')}</small></div><code>${esc(m.portaria_rtm||'-')}</code>`;item.onclick=()=>applyModel(m);box.appendChild(item)});
  if(!rows.length){const item=document.createElement('div');item.className='resultItem';item.innerHTML=`<div><b>Usar como modelo digitado</b><br><small>${esc(term)}</small></div><code>MANUAL</code>`;item.onclick=()=>{setVal('modelo',term);box.innerHTML='';updateEnergyResults()};box.appendChild(item)}
}
function applyModel(m){
  setVal('modeloSearch',m.modelo||''); setVal('modelo',m.modelo||''); setVal('fabricante',m.fabricante||''); hideManufacturerResults(); setVal('portaria',m.portaria||''); setVal('classe',m.classe||''); setVal('elementos',m.elementos||''); setVal('corrente_nominal',m.corrente_nominal||''); setVal('corrente_maxima',m.corrente_maxima||''); setVal('tensao_nominal',m.tensao_nominal||''); setVal('frequencia',m.frequencia||''); setVal('constante',m.constante||''); setVal('portaria_rtm',m.portaria_rtm||''); $('modelResults').innerHTML=''; updateEnergyResults();
}

function lacIds(){return ['lacragem_conforme','lacragem_ausencia','lacragem_travas','lacragem_arame','lacragem_haste','lacragem_pontos','lacragem_outros','lacragem_pea']}
function syncLacragem(changed=null){
  const enabled=val('lacragem_realizado')!=='NÃO'; const conforme=$('lacragem_conforme'); const irregular=lacIds().slice(1).map(id=>$(id));
  lacIds().forEach(id=>{const x=$(id);x.disabled=!enabled;x.closest('label')?.classList.toggle('disabled',!enabled);if(!enabled)x.checked=false});
  if(!enabled){$('lacragemResult').textContent='N/A';return 'N/A'}
  if(changed===conforme && conforme.checked) irregular.forEach(x=>x.checked=false);
  if(changed && changed!==conforme && changed.checked) conforme.checked=false;
  if(!conforme.checked && !irregular.some(x=>x.checked) && !changed) conforme.checked=true;
  const r=irregular.some(x=>x.checked)?'IRREGULAR':(conforme.checked?'REGULAR':'SEM OPÇÃO'); $('lacragemResult').textContent=r; return r;
}

function toNum(v){ const s=String(v??'').trim().replace('%','').replace(',','.'); if(!s||s==='-') return null; const n=Number(s); return Number.isFinite(n)?n:null; }
function resultFor(value,limit){const n=toNum(value);if(n===null||!Number.isFinite(limit))return 'NÃO REALIZADO';return Math.abs(n)>Math.abs(limit)?'REPROVADO':'APROVADO'}
function paintResult(id,result){const e=$(id);e.textContent=result;e.className=result==='APROVADO'?'status-ok':result==='REPROVADO'?'status-bad':'status-na'}
function updateEnergyResults(){
  const lim=LIMITS[val('classe')]; $('activeLimitLabel').textContent=`Limite: ${lim?String(lim.ativa.toFixed(2)).replace('.',','):'–'}%`; $('reactiveLimitLabel').textContent=`Limite: ${lim?String(lim.reativa.toFixed(2)).replace('.',','):'–'}%`;
  [['ea_nominal','eaResCn'],['ea_pequena','eaResCp'],['ea_indutiva','eaResCi']].forEach(([i,r])=>paintResult(r,resultFor(val(i),lim?.ativa)));
  const reativo=val('reativo')==='SIM'; [['er_nominal','erResCn'],['er_pequena','erResCp'],['er_indutiva','erResCi']].forEach(([i,r])=>paintResult(r,reativo?resultFor(val(i),lim?.reativa):'NÃO REALIZADO'));
}
function syncReactive(){ const on=val('reativo')==='SIM'; $('reactiveArea').classList.toggle('disabled',!on); if(!on){['leitura_kvar_inicial','leitura_kvar_final','er_nominal','er_pequena','er_indutiva'].forEach(id=>setVal(id,'-'));} else {['leitura_kvar_inicial','leitura_kvar_final','er_nominal','er_pequena','er_indutiva'].forEach(id=>{if(val(id)==='-')setVal(id,'')});} updateEnergyResults(); }

function renderObservationSearch(term){
  const q=String(term||'').trim().toUpperCase(); const box=$('obsResults'); box.innerHTML=''; if(!q) return;
  bootstrap.observations.filter(o=>String(o.id).includes(q)||String(o.observacao||'').toUpperCase().includes(q)).slice(0,35).forEach(o=>{
    const chosen=selectedObs.some(x=>Number(x.id)===Number(o.id)); const item=document.createElement('div');item.className='resultItem';item.innerHTML=`<div><b>${o.id}. ${esc(o.observacao)}</b>${o.conclusao?`<br><small>${esc(o.conclusao)}</small>`:''}</div><code>${chosen?'SELECIONADA':'ADICIONAR'}</code>`;item.onclick=()=>addObservation(o);box.appendChild(item);
  });
}
function addObservation(o){ if(selectedObs.some(x=>Number(x.id)===Number(o.id))) return; if(selectedObs.length>=4){toast('O Novo Laudo atual aceita até 4 frases de observação.');return;} selectedObs.push(o); renderSelectedObs(); renderObservationSearch(val('obsSearch')); }
function removeObservation(id){ selectedObs=selectedObs.filter(x=>Number(x.id)!==Number(id));renderSelectedObs();renderObservationSearch(val('obsSearch')); }
function renderSelectedObs(){ const box=$('selectedObs');box.innerHTML='';if(!selectedObs.length){box.innerHTML='<span class="muted">Nenhuma observação selecionada.</span>';return;} selectedObs.forEach(o=>{const c=document.createElement('div');c.className='obsChip';c.innerHTML=`<b>${o.id}</b><span>${esc(String(o.observacao).slice(0,65))}${String(o.observacao).length>65?'…':''}</span><button type="button">×</button>`;c.querySelector('button').onclick=()=>removeObservation(o.id);box.appendChild(c)}) }

function flags(payload){
  const lacOn=val('lacragem_realizado')!=='NÃO'; payload.lacragem_sim=lacOn?1:0;payload.lacragem_nao=lacOn?0:1;lacIds().forEach(id=>payload[id]=lacOn&&checked(id)?1:0);payload.sistema_lacragem=syncLacragem();payload.lacres_retirados=val('lacres_retirados')||'NÃO';
  const vis=val('inspecao_visual_realizada')!=='NÃO'; payload.inspecao_visual_sim=vis?1:0;payload.inspecao_visual_nao=vis?0:1;const dp=val('dados_placa'),dm=val('dimensoes'),ps=val('plano_selagem');payload.dados_placa_conforme=vis&&dp==='CONFORME'?1:0;payload.dados_placa_nao_conforme=vis&&dp==='NÃO CONFORME'?1:0;payload.dimensoes_conforme=vis&&dm==='CONFORME'?1:0;payload.dimensoes_nao_conforme=vis&&dm==='NÃO CONFORME'?1:0;payload.dimensoes_na=vis&&dm==='N/A'?1:0;payload.plano_selagem_conforme=vis&&ps==='CONFORME'?1:0;payload.plano_selagem_nao_conforme=vis&&ps==='NÃO CONFORME'?1:0;
  const m=val('marcha_em_vazio');payload.marcha_em_vazio=m;payload.marcha_sim=m==='N/A'?0:1;payload.marcha_nao=m==='N/A'?1:0;payload.marcha_conforme=m==='APROVADO'?1:0;payload.marcha_nao_conforme=m==='REPROVADO'?1:0;
  const r=val('exame_registrador');payload.exame_registrador=r;payload.registrador_sim=r==='N/A'?0:1;payload.registrador_nao=r==='N/A'?1:0;payload.registrador_conforme=r==='APROVADO'?1:0;payload.registrador_nao_conforme=r==='REPROVADO'?1:0;
  const e=val('ensaio_exatidao');payload.ensaio_exatidao=e;payload.exatidao_sim=e==='N/A'?0:1;payload.exatidao_nao=e==='N/A'?1:0;payload.exatidao_conforme=e==='APROVADO'?1:0;payload.exatidao_nao_conforme=e==='REPROVADO'?1:0;
  const gr=val('inspecao_geral_realizada')!=='NÃO', gres=val('inspecao_geral_resultado');payload.inspecao_geral_sim=gr?1:0;payload.inspecao_geral_nao=gr?0:1;payload.inspecao_geral_conforme=gr&&gres==='CONFORME'?1:0;payload.inspecao_geral_nao_conforme=gr&&gres==='NÃO CONFORME'?1:0;
  payload.conclusao=val('tipo')==='CV'?'APROVADO':'REPROVADO - ver observações'; return payload;
}

function collect(){
  const p={
    numero_laudo:val('numero_laudo'),ano:val('ano'),tipo:val('tipo')||'NR',processo:val('processo'),numero_protocolo:val('numero_protocolo'),involucro:val('numero_protocolo'),data_ensaio:val('data_ensaio'),data_emissao:val('data_emissao'),data_emissao_modo:'MANUAL',instalacao:val('instalacao'),numero_serie:val('numero_serie'),
    fabricante:val('fabricante'),modelo:val('modelo')||val('modeloSearch'),portaria:val('portaria'),classe:val('classe'),elementos:val('elementos'),corrente_nominal:val('corrente_nominal'),corrente_maxima:val('corrente_maxima'),tensao_nominal:val('tensao_nominal'),frequencia:val('frequencia'),constante:val('constante'),portaria_rtm:val('portaria_rtm'),ano_fabricacao:val('ano_fabricacao'),
    responsavel_ensaio:val('responsavel_ensaio'),equipamento_utilizado:val('equipamento_utilizado'),tecnico_1:val('tecnico_1'),assinatura:val('assinatura')||'COM',responsavel_tecnico:val('responsavel_tecnico'),digitador:val('digitador'),
    leitura_kw_inicial:val('leitura_kw_inicial'),leitura_kw_final:val('leitura_kw_final'),ea_cos_cn:val('ea_cos_cn'),ea_cos_cp:val('ea_cos_cp'),ea_cos_ci:val('ea_cos_ci'),ea_nominal:val('ea_nominal'),ea_pequena:val('ea_pequena'),ea_indutiva:val('ea_indutiva'),reativo:val('reativo')||'NÃO',leitura_kvar_inicial:val('leitura_kvar_inicial'),leitura_kvar_final:val('leitura_kvar_final'),er_nominal:val('reativo')==='SIM'?val('er_nominal'):'-',er_pequena:val('reativo')==='SIM'?val('er_pequena'):'-',er_indutiva:val('reativo')==='SIM'?val('er_indutiva'):'-',
    avaliacao_parecer:val('avaliacao_parecer')||'-',observacao_portaria:val('observacao_portaria'),observacao_livre:val('observacao_livre'),presencial:val('presencial'),dados_presencial:val('presencial')?val('dados_presencial'):'',comparecimento:val('comparecimento')||'NAO_COMPARECEU',
    espelho_extra:{numero_fios:val('numero_fios'),modo_interface:'APP',usuario_app:{profile_id:currentUser?.id||null,nome:currentUser?.nome||'',email:currentUser?.email||'',perfil:currentUser?.perfil||''},procedimentos_preliminares:{involucro_medidor:val('pre_involucro'),numero_involucro_lacre:val('pre_numero_involucro'),condicoes_involucro:val('pre_condicao'),toi_comprovante:val('pre_toi'),toi_numeracao:val('pre_toi_numero'),preenchimento_toi:val('pre_preenchimento'),ocorrencia_preenchimento:val('pre_ocorrencia')}}
  };
  for(let i=0;i<4;i++){const o=selectedObs[i];p[`frase_${i+1}_codigo`]=o?String(o.id):'';p[`frase_${i+1}_texto`]=o?String(o.observacao||'').trim():'';}
  return flags(p);
}

function hydrate(p){
  p=p||{}; const ids=['numero_laudo','ano','processo','numero_protocolo','data_ensaio','data_emissao','instalacao','numero_serie','fabricante','modelo','portaria','classe','elementos','corrente_nominal','corrente_maxima','tensao_nominal','frequencia','constante','portaria_rtm','ano_fabricacao','responsavel_ensaio','equipamento_utilizado','tecnico_1','assinatura','responsavel_tecnico','digitador','leitura_kw_inicial','leitura_kw_final','ea_cos_cn','ea_cos_cp','ea_cos_ci','ea_nominal','ea_pequena','ea_indutiva','reativo','leitura_kvar_inicial','leitura_kvar_final','er_nominal','er_pequena','er_indutiva','avaliacao_parecer','observacao_portaria','observacao_livre','presencial','dados_presencial','comparecimento'];
  ids.forEach(id=>{if(id in p)setVal(id,p[id])});setVal('modeloSearch',p.modelo||'');setTipo(p.tipo||'NR');
  setVal('lacragem_realizado',Number(p.lacragem_nao||0)?'NÃO':'SIM');setVal('lacres_retirados',p.lacres_retirados||'NÃO');lacIds().forEach(id=>setChecked(id,Number(p[id]||0)===1));
  setVal('inspecao_visual_realizada',Number(p.inspecao_visual_nao||0)?'NÃO':'SIM');setVal('dados_placa',Number(p.dados_placa_nao_conforme||0)?'NÃO CONFORME':'CONFORME');setVal('dimensoes',Number(p.dimensoes_na||0)?'N/A':Number(p.dimensoes_nao_conforme||0)?'NÃO CONFORME':'CONFORME');setVal('plano_selagem',Number(p.plano_selagem_nao_conforme||0)?'NÃO CONFORME':'CONFORME');
  setVal('marcha_em_vazio',p.marcha_em_vazio||'APROVADO');setVal('exame_registrador',p.exame_registrador||'APROVADO');setVal('ensaio_exatidao',p.ensaio_exatidao||'APROVADO');setVal('inspecao_geral_realizada',Number(p.inspecao_geral_nao||0)?'NÃO':'SIM');setVal('inspecao_geral_resultado',Number(p.inspecao_geral_nao_conforme||0)?'NÃO CONFORME':'CONFORME');
  const ex=p.espelho_extra||{};const pp=ex.procedimentos_preliminares||{};setVal('numero_fios',ex.numero_fios||'');setVal('pre_involucro',pp.involucro_medidor||'SIM');setVal('pre_numero_involucro',pp.numero_involucro_lacre||'');setVal('pre_condicao',pp.condicoes_involucro||'CONFORME');setVal('pre_toi',pp.toi_comprovante||'COMPROVANTE');const savedToi=String(pp.toi_numeracao||'').trim();setVal('pre_toi_numero',savedToi||p.processo||'');toiManuallyEdited=!!savedToi && savedToi!==String(p.processo||'').trim();setVal('pre_preenchimento',pp.preenchimento_toi||'SIM');setVal('pre_ocorrencia',pp.ocorrencia_preenchimento||'CONFORME');
  selectedObs=[];for(let i=1;i<=4;i++){const code=p[`frase_${i}_codigo`];const text=p[`frase_${i}_texto`];if(code||text){const found=bootstrap.observations.find(x=>String(x.id)===String(code));selectedObs.push(found||{id:code||i,observacao:text||''})}}renderSelectedObs();syncLacragem();syncReactive();updateEnergyResults();
}

function resetForm(){ clearTimeout(autoSaveTimer);autoSaveTimer=0;$('laudoForm').reset();toiManuallyEdited=false;selectedObs=[];renderSelectedObs();setDefaults();setVal('modelo','');setVal('modeloSearch','');$('modelResults').innerHTML='';$('obsResults').innerHTML='';currentRecordId=null;currentStep=0;setSaveState('RASCUNHO'); }
function newLaudo(){ resetForm(); refreshCatalog(true); $('editorStatus').textContent='NOVO ESPELHO';$('editorTitle').textContent='Novo Laudo';updateStepUi(); setEditorMode(true); switchView('editorView',`${activeMainTab}View`,'right'); }
function backHome(){ flushAutoSave(); setEditorMode(false); switchView(`${activeMainTab}View`,'editorView','left'); setTimeout(loadRecords,180); }
function exitEditorToLaudos(){
  clearTimeout(autoSaveTimer); autoSaveTimer=0;
  setEditorMode(false);
  activeMainTab='laudos'; updateBottomNav();
  switchView('laudosView','editorView','left');
  setTimeout(loadRecords,180);
}


async function saveDraft(silent=false){
  if(autoSaving){ if(!silent) setTimeout(()=>saveDraft(false),260); return; }
  if(!meaningfulDraft()){
    if(!silent) modal('Nada para salvar','Preencha pelo menos um dado do laudo antes de salvar.');
    return;
  }
  autoSaving=true; clearTimeout(autoSaveTimer); autoSaveTimer=0; setSaveState('SALVANDO…','saving');
  try{
    const r=await api('/api/records/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:currentRecordId,data:collect()})});
    currentRecordId=r.item.id;
    $('editorStatus').textContent=statusMeta(r.item.status).label.toUpperCase();
    $('editorTitle').textContent=`${r.item.tipo || 'NR'}-${r.item.numero_laudo || 'SEM Nº'} • ${r.item.instalacao || 'SEM INSTALAÇÃO'}`;
    setSaveState('SALVO','saved');
    if(!silent){
      const online=(r.backend?.mode||appConfig.backend?.mode)==='ONLINE';
      const msg=online?'O laudo foi salvo no Supabase/PostgreSQL com sucesso.':'O laudo foi salvo somente no modo local. Para enviar ao PostgreSQL, altere o Servidor para ONLINE em Configurações.';
      modal('Rascunho salvo',msg,[['OK','primary',()=>{closeModal();exitEditorToLaudos();}]]);
    }
  }catch(e){ setSaveState('SEM REDE','error'); if(!silent) modal('Salvar rascunho',e.message); }
  finally{ autoSaving=false; }
}
async function exportBridge(){
  const p=collect();const missing=[];if(!p.numero_laudo)missing.push('Nº do Laudo');if(!p.instalacao)missing.push('Instalação');if(!p.numero_serie)missing.push('Nº do medidor / Série');if(!p.modelo)missing.push('Modelo');if(missing.length){modal('Espelho incompleto',`Preencha antes de finalizar:\n• ${missing.join('\n• ')}`);return;}
  if(p.lacragem_sim && !lacIds().some(id=>checked(id))){modal('Lacragem','Selecione pelo menos uma opção da integridade dos lacres ou marque a inspeção como NÃO.');return;}
  try{const r=await api('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:currentRecordId,data:p})});currentRecordId=r.record.id;const online=r.backend?.mode==='ONLINE';$('editorStatus').textContent=statusMeta(r.record.status).label.toUpperCase();setSaveState(online?'ENVIADO':'LOCAL','saved');const msg=online?`Laudo gravado no Supabase/PostgreSQL e enviado para revisão.\n\nStatus: ${statusMeta(r.record.status).label}\nID: ${r.bridge_id||'—'}`:`O app está em MODO LOCAL. O laudo foi salvo somente neste computador e NÃO foi enviado ao PostgreSQL.\n\nAbra ⚙ Configurações → Servidor → USAR ONLINE.`;modal(online?'Laudo enviado':'Laudo salvo localmente',msg,[['OK','primary',()=>{closeModal();exitEditorToLaudos();}]])}
  catch(e){modal('Finalizar Espelho',e.message)}
}

function renderReview(){
  const p=collect();const items=[['Tipo / Laudo',`${p.tipo}-${p.numero_laudo||'–'}-${p.ano||'–'}`],['Instalação',p.instalacao||'–'],['Medidor / Série',p.numero_serie||'–'],['Modelo',p.modelo||'–'],['Fabricante',p.fabricante||'–'],['Classe',p.classe||'–'],['Lacragem',p.sistema_lacragem||'–'],['Marcha em vazio',p.marcha_em_vazio||'–'],['Registrador',p.exame_registrador||'–'],['Exatidão',p.ensaio_exatidao||'–'],['Energia Ativa',`${p.ea_nominal||'–'} / ${p.ea_pequena||'–'} / ${p.ea_indutiva||'–'}`],['Observações',selectedObs.length?selectedObs.map(o=>o.id).join(', '):'Nenhuma']];
  $('reviewGrid').innerHTML=items.map(([a,b])=>`<div class="reviewItem"><span>${esc(a)}</span><b>${esc(b)}</b></div>`).join('');
  const warn=[];[['numero_laudo','Nº do Laudo'],['instalacao','Instalação'],['numero_serie','Nº do medidor / Série'],['modelo','Modelo']].forEach(([k,l])=>{if(!p[k])warn.push(l)});if(p.lacragem_sim && !lacIds().some(id=>checked(id)))warn.push('Opção da integridade dos lacres');
  $('reviewWarnings').innerHTML=warn.length?`<div class="warning"><b>Faltam campos obrigatórios:</b> ${esc(warn.join(', '))}.</div>`:'<div class="success"><b>Revisão básica concluída.</b> O Espelho possui os campos obrigatórios da ponte.</div>';
}

async function loadRecords(){
  try{
    const r=await api('/api/records');
    const rows=(r.items||[]).map(x=>({...x,status:normStatus(x.status)}));
    allRecords=rows;
    const drafts=rows.filter(x=>x.status==='RASCUNHO');
    const flow=rows.filter(x=>FLOW_STATUSES.map(normStatus).includes(x.status));
    const corrections=rows.filter(x=>x.status==='DEVOLVIDO');
    const active=rows.filter(x=>ACTIVE_STATUSES.map(normStatus).includes(x.status));
    $('statDrafts').textContent=drafts.length;
    if($('statReady')) $('statReady').textContent=flow.length+corrections.length;
    if($('statTotal')) $('statTotal').textContent=rows.length;
    if($('draftCountLabel')) $('draftCountLabel').textContent=drafts.length;
    if($('readyCountLabel')) $('readyCountLabel').textContent=flow.length;
    if($('correctionCountLabel')) $('correctionCountLabel').textContent=corrections.length;

    renderLaudos();
    renderHistory();
  }catch(e){toast(e.message)}
}
function renderLaudos(){
  const box=$('draftsGrid'); if(!box) return;
  let rows=allRecords.filter(x=>ACTIVE_STATUSES.map(normStatus).includes(x.status));
  rows=rows.filter(x=>matchesRecordSearch(x,laudosSearchTerm));
  $('draftsEmpty').classList.toggle('hidden',rows.length>0);
  $('draftsEmpty').textContent=laudosSearchTerm?'Nenhum laudo encontrado para esta pesquisa.':'Nenhum laudo em andamento.';
  box.innerHTML='';rows.forEach(row=>box.appendChild(makeRecordCard(row)));
}
function makeRecordCard(row){
  const c=document.createElement('div');
  const meta=statusMeta(row.status);
  const correction=row.status==='DEVOLVIDO';
  const created=row.status==='LAUDO_CRIADO';
  c.className='recordCard '+(correction?'needsCorrection':'');
  const bridge=row.bridge_id?`<div>ID Ponte<strong>${esc(row.bridge_id)}</strong></div>`:'';
  const remote=row.remote_laudo_numero?`<div>Laudo oficial<strong>${esc(row.remote_laudo_numero)}</strong></div>`:'';
  const note=row.status_message?`<div class="recordNotice ${correction?'warnNotice':''}">${esc(row.status_message)}</div>`:'';
  c.innerHTML=`<div class="recordTop"><b>${esc(row.tipo||'NR')}-${esc(row.numero_laudo||'SEM Nº')} <small>${esc(row.ano||'')}</small></b><span class="badge ${meta.cls}">${esc(meta.label)}</span></div>${note}<div class="recordMeta"><div>Instalação<strong>${esc(row.instalacao||'–')}</strong></div><div>Medidor<strong>${esc(row.numero_serie||'–')}</strong></div><div>Modelo<strong>${esc(row.modelo||'–')}</strong></div><div>Atualizado<strong>${esc(formatDateTime(row.updated_at))}</strong></div>${bridge}${remote}</div><div class="recordActions"><button class="secondary editBtn">${correction?'CORRIGIR':created?'VER':'ABRIR'}</button>${created?'':`<button class="danger delBtn">EXCLUIR</button>`}</div>`;
  c.querySelector('.editBtn').onclick=()=>openRecord(row.id);
  c.querySelector('.delBtn')?.addEventListener('click',()=>confirmBox('Excluir espelho',`Excluir ${row.tipo||'NR'}-${row.numero_laudo||'SEM Nº'} da lista local?`,()=>deleteRecord(row.id)));
  return c;
}
function renderHistory(){
  const box=$('recordsGrid'); if(!box) return;
  let rows=allRecords;
  if(historyFilter==='RASCUNHO') rows=allRecords.filter(x=>x.status==='RASCUNHO');
  else if(historyFilter==='ENVIADOS') rows=allRecords.filter(x=>FLOW_STATUSES.map(normStatus).includes(x.status));
  else if(historyFilter==='DEVOLVIDO') rows=allRecords.filter(x=>x.status==='DEVOLVIDO');
  else if(historyFilter==='LAUDO_CRIADO') rows=allRecords.filter(x=>x.status==='LAUDO_CRIADO');
  rows=rows.filter(x=>matchesRecordSearch(x,historySearchTerm));
  $('recordsEmpty').classList.toggle('hidden',rows.length>0);
  $('recordsEmpty').textContent=historySearchTerm?'Nenhum laudo encontrado para esta pesquisa.':'Nenhum laudo encontrado.';
  box.innerHTML=''; rows.forEach(row=>box.appendChild(makeRecordCard(row)));
}

function formatDateTime(v){if(!v)return'–';const d=new Date(v);return Number.isNaN(d.getTime())?v:d.toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'})}
async function openRecord(id){
  try{
    const r=await api(`/api/records/${id}`); resetForm(); currentRecordId=r.item.id; hydrate(r.item.payload);
    const meta=statusMeta(r.item.status); $('editorStatus').textContent=meta.label.toUpperCase(); setSaveState('SALVO','saved');
    $('editorTitle').textContent=`${r.item.tipo||'NR'}-${r.item.numero_laudo||'SEM Nº'} • ${r.item.instalacao||'SEM INSTALAÇÃO'}`;
    if(r.item.status_message && normStatus(r.item.status)==='DEVOLVIDO') toast('Correção solicitada: '+r.item.status_message);
    goStep(0); setEditorMode(true); switchView('editorView',`${activeMainTab}View`,'right');
  }catch(e){modal('Abrir espelho',e.message)}
}
async function deleteRecord(id){try{await api(`/api/records/${id}`,{method:'DELETE'});toast('Espelho excluído.');loadRecords()}catch(e){modal('Excluir espelho',e.message)}}
async function openOutbox(){try{const r=await api('/api/open-outbox',{method:'POST'});toast(`Pasta aberta: ${r.path}`)}catch(e){modal('Pasta de envio',e.message)}}


window.requestLogoutFromNative = function(){
  if(!currentUser){showLoginView();return 'HANDLED';}
  confirmBox('Sair da conta','Deseja encerrar sua sessão no ID LAUDO?',()=>logoutApp());
  return 'HANDLED';
};

window.idLaudoBack = function(){
  const modalEl=$('modal');
  if(modalEl && !modalEl.classList.contains('hidden')){ closeModal(); return 'HANDLED'; }
  const forgot=$('forgotView');
  if(forgot && !forgot.classList.contains('hidden')){ closeForgotPassword(); return 'HANDLED'; }
  const reset=$('resetPasswordView');
  if(reset && !reset.classList.contains('hidden')){
    // A troca obrigatória não pode ser ignorada pelo botão Voltar.
    if(passwordChangeMode==='forced') return 'HANDLED';
    clearAuthState(); showLoginView(); return 'HANDLED';
  }
  const auth=$('authView');
  if(auth && !auth.classList.contains('hidden')) return 'EXIT';
  const settings=$('settingsView');
  if(settings && !settings.classList.contains('hidden')){ closeSettings(); return 'HANDLED'; }
  const editor=$('editorView');
  if(editor && !editor.classList.contains('hidden')){
    if(currentStep>0){ prevStep(); }
    else { backHome(); }
    return 'HANDLED';
  }
  if(activeMainTab!=='home'){ switchMainTab('home'); return 'HANDLED'; }
  return 'EXIT';
};

$('numero_protocolo').addEventListener('blur',()=>{if(!val('pre_numero_involucro'))setVal('pre_numero_involucro',val('numero_protocolo'))});
$('pre_numero_involucro').addEventListener('blur',()=>{if(!val('numero_protocolo'))setVal('numero_protocolo',val('pre_numero_involucro'))});
$('fabricante')?.addEventListener('blur',()=>setTimeout(hideManufacturerResults,140));
$('observacao_portaria')?.addEventListener('blur',()=>setTimeout(hidePortariaResults,140));
$('classe').addEventListener('input',updateEnergyResults);
$('tipo').addEventListener('change',()=>setTipo(val('tipo')));

document.body.dataset.uiMode='app';
document.documentElement.dataset.uiMode='app';
init();
