const TOKEN='local-demo-session', state={language:'en',tab:'overview',data:null,history:[],busy:false,approvalContext:new Map()};
const dict={en:{workspace:'YOUR WORKSPACE',overview:'Overview',activity:'Transactions',cards:'Cards',payments:'Payments',disputes:'Disputes',insights:'Financial Insights',trace:'Agent Activity',demo:'SIMULATED BANK ENVIRONMENT',reset:'Reset demo',welcome:'PERSONAL BANKING · LIVE EXPERIENCE',greeting:'Good afternoon, Camila.',subtitle:'Your money, your control. Everything in one place.',assistant:'Ask Aurora AI',total:'AVAILABLE CHECKING BALANCE',updated:'Live demo data',scheduled:'SCHEDULED OBLIGATIONS',month:'Upcoming commitments',remaining:'PROJECTED AVAILABLE',net:'After scheduled bills',open:'OPEN DISPUTES',cases:'Cases under review',recent:'Recent activity',live:'Real records from the banking simulator',meet:'Meet your AI banking team',meetBody:'Five specialists, one conversation. Understand spending, protect your cards, resolve disputes and pay safely.',start:'Start a conversation →',beneficiary:'Verified Pix beneficiary',pixNote:'Payments require explicit approval.',online:'Banking specialists online',promptFraud:'Report unknown charge',promptPix:'Pay condominium Pix',promptBudget:'Analyze my cash flow',disclaimer:'Synthetic demonstration · Never enter real financial information'},es:{workspace:'TU ESPACIO',overview:'Resumen',activity:'Transacciones',cards:'Tarjetas',payments:'Pagos',disputes:'Reclamaciones',insights:'Análisis financiero',trace:'Actividad de agentes',demo:'ENTORNO BANCARIO SIMULADO',reset:'Reiniciar demo',welcome:'BANCA PERSONAL · EXPERIENCIA EN VIVO',greeting:'Buenas tardes, Camila.',subtitle:'Tu dinero, tu control. Todo en un solo lugar.',assistant:'Hablar con Aurora AI',total:'SALDO DISPONIBLE EN CUENTA',updated:'Datos de demostración en vivo',scheduled:'OBLIGACIONES PROGRAMADAS',month:'Próximos compromisos',remaining:'DISPONIBLE PROYECTADO',net:'Después de las próximas facturas',open:'RECLAMACIONES ABIERTAS',cases:'Casos en revisión',recent:'Actividad reciente',live:'Registros reales del simulador bancario',meet:'Conoce a tu equipo bancario de IA',meetBody:'Cinco especialistas, una conversación. Analiza gastos, protege tarjetas, resuelve reclamaciones y paga de forma segura.',start:'Iniciar conversación →',beneficiary:'Beneficiario Pix verificado',pixNote:'Los pagos requieren autorización explícita.',online:'Especialistas bancarios en línea',promptFraud:'Reportar cargo desconocido',promptPix:'Pagar condominio por Pix',promptBudget:'Analizar flujo de caja',disclaimer:'Demostración ficticia · No introduzcas información financiera real'}};
const $=id=>document.getElementById(id), money=n=>new Intl.NumberFormat(state.language==='es'?'es-ES':'en-US',{style:'currency',currency:'BRL'}).format(Number(n||0));
function toast(msg){$('toast').textContent=msg;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),3800)}
async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Demo-Session':TOKEN,...options.headers}});if(!response.ok)throw new Error((await response.text()).slice(0,260));return response.json()}
function localize(){document.documentElement.lang=state.language;document.querySelectorAll('[data-i]').forEach(e=>e.textContent=dict[state.language][e.dataset.i]||e.textContent);$('entry').placeholder=state.language==='es'?'Pregunta sobre tu cuenta...':'Ask about your banking...';$('initial').textContent=state.language==='es'?'¡Hola, Camila! Soy Aurora, tu asistente bancario. Puedo ayudarte con transacciones, seguridad de tarjetas, reclamaciones, pagos Pix y planificación financiera. ¿En qué te ayudo?':'Hello, Camila! I’m Aurora, your banking assistant. I can help with transactions, card security, disputes, Pix payments and financial planning. How can I help?';render()}
function record(title,description,value,icon='↗'){const el=document.createElement('div');el.className='record';const i=document.createElement('div');i.className='record-icon';i.textContent=icon;const main=document.createElement('div');main.className='record-main';const strong=document.createElement('strong');strong.textContent=title;const small=document.createElement('small');small.textContent=description;main.append(strong,small);const amount=document.createElement('div');amount.className='amount';amount.textContent=value;el.append(i,main,amount);return el}
function render(){const s=state.data;if(!s)return;const due=s.obligations.reduce((a,b)=>a+Number(b.amount),0);$('balance').textContent=money(s.account.balance);$('obligations').textContent=money(due);$('projected').textContent=money(Number(s.account.balance)-due);$('disputesCount').textContent=s.disputes.filter(d=>d.status==='under_review').length;$('beneficiaryText').textContent=s.beneficiaries.map(b=>`${b.name} · ${b.verified?'✓ Verified':'Unverified'}`).join(', ');const lang=state.language;const titles={overview:lang==='es'?'Actividad reciente':'Recent activity',activity:lang==='es'?'Transacciones':'Transactions',cards:lang==='es'?'Mis tarjetas':'My cards',payments:lang==='es'?'Pagos Pix':'Pix payments',disputes:lang==='es'?'Reclamaciones':'Disputes',insights:lang==='es'?'Compromisos financieros':'Upcoming obligations',trace:lang==='es'?'Eventos auditables':'Auditable events'};$('tableTitle').textContent=titles[state.tab];$('location').textContent=titles[state.tab].toUpperCase();const list=$('records');list.replaceChildren();let items=[];
if(state.tab==='overview'||state.tab==='activity'){items=[...s.transactions.map(t=>record(t.merchant,`${t.id} · ${t.kind.toUpperCase()} · ${t.status}`,money(t.amount),'⇄')),...s.payments.map(p=>record('Pix · '+p.beneficiary_id,`${p.id} · ${p.status}`,money(p.amount),'↗'))]}
if(state.tab==='cards')items=s.cards.map(c=>record('Aurora Premium · •••• '+c.last4,c.id+' · '+c.status,c.status==='blocked'?'⛔ BLOCKED':'✓ ACTIVE','▣'));
if(state.tab==='payments')items=[...s.payments.map(p=>record(p.id,p.status+' · '+p.beneficiary_id,money(p.amount),'↗')),...s.beneficiaries.map(b=>record(b.name,b.pix_key,b.verified?'✓ VERIFIED':'UNVERIFIED','◆'))];
if(state.tab==='disputes')items=s.disputes.map(d=>record(d.id,d.transaction_id+' · '+d.status,d.status,'♧'));
if(state.tab==='insights')items=s.obligations.map(o=>record(o.label,o.due,money(o.amount),'◷'));
if(state.tab==='trace')items=[...s.events].reverse().map(e=>record(e.type,e.created,e.id,'⌘'));
if(!items.length)items=[record(lang==='es'?'Todavía no hay registros':'No records yet',lang==='es'?'Usa Aurora AI para comenzar':'Use Aurora AI to get started','—','◌')];items.forEach(e=>list.appendChild(e))}
async function refresh(){try{state.data=await api('/api/state');render()}catch(e){toast('Data source unavailable: '+e.message)}}
function openChat(){ $('chatPanel').classList.add('open');document.body.classList.add('chat-open');$('entry').focus() }function closeChat(){$('chatPanel').classList.remove('open');document.body.classList.remove('chat-open')}
function bubble(text,kind){const el=document.createElement('div');el.className='message '+kind;el.textContent=text;$('messages').appendChild(el);$('messages').scrollTop=$('messages').scrollHeight;return el}
async function send(message){
  if(state.busy||!message.trim())return;
  openChat();state.busy=true;
  bubble(message,'user');
  const placeholder=bubble(state.language==='es'?'Consultando especialistas...':'Consulting specialists...','bot');
  $('actions').replaceChildren();state.approvalContext.clear();
  try{
    const result=await api('/api/chat',{method:'POST',body:JSON.stringify({message,language:state.language,history:state.history.slice(-8)})});
    placeholder.textContent=result.message;
    state.history.push({role:'user',content:message},{role:'assistant',content:result.message});
    if(result.data?.language&&result.data.language!==state.language){state.language=result.data.language;$('lang').value=state.language;localize()}
    for(const action of result.next_actions||[]){
      state.approvalContext.set(action.approval_id,{request:message,language:state.language});
      const row=document.createElement('div');row.className='approval-choice';row.dataset.approvalId=action.approval_id;
      const approve=document.createElement('button');approve.textContent='✓ '+action.label;approve.onclick=()=>decide(action.approval_id,true);
      const reject=document.createElement('button');reject.className='reject';reject.textContent=state.language==='es'?'Rechazar':'Decline';reject.onclick=()=>decide(action.approval_id,false);
      row.append(approve,reject);$('actions').append(row);
    }
    await refresh();
  }catch(e){placeholder.textContent='Error: '+e.message}
  finally{state.busy=false;$('messages').scrollTop=$('messages').scrollHeight}
}
async function decide(id,approved){
  if(state.busy)return;
  state.busy=true;
  document.querySelectorAll('#actions button').forEach(x=>x.disabled=true);
  try{
    const context=state.approvalContext.get(id)||{request:'',language:state.language};
    const result=await api('/api/approvals/'+encodeURIComponent(id)+'/decide',{
      method:'POST',body:JSON.stringify({approved,language:context.language,original_request:context.request})
    });
    const answer=result.message||(state.language==='es'?'Operación procesada.':'Operation processed.');
    bubble(answer,'bot');
    state.history.push({role:'assistant',content:answer});
    const choice=[...document.querySelectorAll('#actions .approval-choice')].find(x=>x.dataset.approvalId===id);
    if(choice)choice.remove();
    state.approvalContext.delete(id);
    toast(approved?(state.language==='es'?'✓ Operación completada':'✓ Operation completed'):(state.language==='es'?'Acción rechazada':'Action declined'));
    await refresh();
  }catch(e){toast('Error: '+e.message)}
  finally{state.busy=false;document.querySelectorAll('#actions button').forEach(x=>x.disabled=false)}
}
document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#nav button').forEach(x=>x.classList.toggle('active',x===b));state.tab=b.dataset.tab;render()});$('lang').onchange=e=>{state.language=e.target.value;localize()};['ask','startChat'].forEach(x=>$(x).onclick=openChat);$('close').onclick=closeChat;$('chatForm').onsubmit=e=>{e.preventDefault();const m=$('entry').value;$('entry').value='';send(m)};$('entry').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chatForm').requestSubmit()}};document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>send(state.language==='es'?({"I don't recognize the R$1,280 Orion Digital charge. Block my card and open a dispute.":'No reconozco el cargo de R$1.280 de Orion Digital. Bloquea mi tarjeta y abre una reclamación.',"Pay R$1,250 to my verified condominium Pix beneficiary, then check my remaining balance.":'Paga R$1.250 por Pix a mi beneficiario verificado del condominio y revisa el saldo restante.',"Will I have enough money after my upcoming expenses?":'¿Tendré suficiente dinero después de mis próximos gastos?'})[b.dataset.prompt]:b.dataset.prompt));$('reset').onclick=async()=>{if(!confirm(state.language==='es'?'¿Reiniciar todos los datos ficticios?':'Reset all synthetic banking data?'))return;try{await api('/api/reset',{method:'POST'});state.history=[];state.approvalContext.clear();$('actions').replaceChildren();$('messages').replaceChildren();bubble(state.language==='es'?'Datos reiniciados. ¿En qué puedo ayudarte?':'Data reset. How can I help?','bot');await refresh();toast('Demo reset')}catch(e){toast(e.message)}};refresh();
