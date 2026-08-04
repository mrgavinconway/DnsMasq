let state={reservations:[],dns:[],leases:[]};
let baseline='',dirty=false,toastTimer;
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const config=()=>({reservations:state.reservations,dns:state.dns});
const snapshot=()=>JSON.stringify(config());

function duration(seconds){if(seconds<=0)return'Expired';const m=Math.floor(seconds/60);if(m<60)return`${m}m`;const h=Math.floor(m/60);return h<24?`${h}h ${m%60}m`:`${Math.floor(h/24)}d ${h%24}h`}
function notify(message,error=false){const el=$('#toast');el.textContent=message;el.className=`toast show${error?' error':''}`;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.className='toast',2800)}
function setDirty(){dirty=snapshot()!==baseline;$('#dirty').textContent=dirty?'Changes ready to apply':'No unsaved changes';$('#save-note').textContent=dirty?'Review then apply to dnsmasq':'dnsmasq is up to date';$('#save').disabled=!dirty;$('#discard').disabled=!dirty}
function field(label,key,value,placeholder){return`<div class="field"><label>${label}</label><input data-key="${key}" value="${esc(value)}" placeholder="${placeholder}" autocomplete="off"></div>`}
function isReserved(lease){return state.reservations.some(r=>r.mac.toLowerCase()===lease.mac.toLowerCase()||r.ip===lease.ip)}
function filteredLeases(){const q=$('#lease-filter').value.trim().toLowerCase();return q?state.leases.filter(x=>[x.hostname,x.ip,x.mac].some(v=>String(v).toLowerCase().includes(q))):state.leases}

function renderLeases(){
 const rows=filteredLeases();
 $('#lease-rows').innerHTML=rows.map(x=>{const reserved=isReserved(x),initial=(x.hostname||'?')[0];return`<tr><td><div class="device"><span class="device-icon">${esc(initial)}</span><strong>${esc(x.hostname||'Unknown device')}</strong></div></td><td class="mono">${esc(x.ip)}</td><td class="mono">${esc(x.mac)}</td><td><span class="lease-time">${duration(x.remaining)}</span></td><td>${reserved?'<span class="reserved">Reserved</span>':`<button class="reserve" data-mac="${esc(x.mac)}" data-ip="${esc(x.ip)}" data-host="${esc(x.hostname)}">Reserve</button>`}</td></tr>`}).join('')||'<tr><td colspan="5" class="loading">No matching leases.</td></tr>';
}
function render(){
 $('#lease-count').textContent=state.leases.length;$('#reservation-count').textContent=state.reservations.length;$('#dns-count').textContent=state.dns.length;
 renderLeases();
 $('#reservation-rows').innerHTML=state.reservations.map((x,i)=>`<div class="record" data-index="${i}" data-kind="reservations">${field('Hostname','hostname',x.hostname,'printer')}${field('IP address','ip',x.ip,'192.168.1.20')}${field('MAC address','mac',x.mac,'aa:bb:cc:dd:ee:ff')}<button class="remove" aria-label="Remove ${esc(x.hostname||'reservation')}">Remove</button></div>`).join('')||'<div class="empty">No reservations yet. Reserve a live device or add one manually.</div>';
 $('#dns-rows').innerHTML=state.dns.map((x,i)=>`<div class="record dns" data-index="${i}" data-kind="dns">${field('Hostname','hostname',x.hostname,'nas.home')}${field('IP address','ip',x.ip,'192.168.1.10')}<button class="remove" aria-label="Remove ${esc(x.hostname||'DNS record')}">Remove</button></div>`).join('')||'<div class="empty">No local DNS records yet.</div>';
 setDirty();
}
async function load(showNotice=false){
 const refresh=$('#refresh');refresh.disabled=true;
 try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw Error('Could not reach the service');state=await r.json();baseline=snapshot();render();$('#status').innerHTML='<span></span>dnsmasq online';$('#status').className='status ok';$('#updated').textContent=`Updated ${new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}`;if(showNotice)notify('Network data refreshed')}
 catch(e){$('#status').innerHTML=`<span></span>${esc(e.message)}`;$('#status').className='status bad';notify(e.message,true)}finally{refresh.disabled=false}
}
function showTab(name){document.querySelectorAll('nav button,.panel').forEach(x=>x.classList.remove('active'));document.querySelector(`[data-tab="${name}"]`).classList.add('active');$('#'+name).classList.add('active')}
function addReservation(button){const mac=button.dataset.mac,ip=button.dataset.ip,hostname=button.dataset.host||'';state.reservations.push({hostname,ip,mac});render();showTab('reservations');const input=document.querySelector('#reservation-rows .record:last-child input');input?.focus();notify(`${hostname||ip} added as a reservation`)}

document.addEventListener('click',e=>{
 const tab=e.target.closest('[data-tab]');if(tab)showTab(tab.dataset.tab);
 const add=e.target.closest('[data-add]');if(add){state[add.dataset.add].push(add.dataset.add==='dns'?{hostname:'',ip:''}:{hostname:'',ip:'',mac:''});render();document.querySelector(`#${add.dataset.add==='dns'?'dns':'reservation'}-rows .record:last-child input`)?.focus()}
 const rm=e.target.closest('.remove');if(rm){const row=rm.closest('.record');state[row.dataset.kind].splice(+row.dataset.index,1);render()}
 const reserve=e.target.closest('.reserve');if(reserve)addReservation(reserve);
});
document.addEventListener('input',e=>{const row=e.target.closest('.record');if(row){state[row.dataset.kind][+row.dataset.index][e.target.dataset.key]=e.target.value;e.target.classList.remove('invalid');setDirty()}if(e.target.id==='lease-filter')renderLeases()});
$('#refresh').onclick=()=>{if(dirty){notify('Apply or discard changes before refreshing',true);return}load(true)};
$('#discard').onclick=()=>load().then(()=>notify('Changes discarded'));
$('#save').onclick=async()=>{
 const invalid=[...document.querySelectorAll('.record input')].filter(x=>!x.value.trim());invalid.forEach(x=>x.classList.add('invalid'));if(invalid.length){invalid[0].focus();notify('Complete all fields before applying',true);return}
 const button=$('#save');button.disabled=true;button.textContent='Applying…';
 try{const r=await fetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(config())});const out=await r.json();if(!r.ok)throw Error(out.error||'Apply failed');baseline=snapshot();setDirty();await load();notify('Changes applied — dnsmasq reloaded')}
 catch(e){notify(e.message,true);$('#save-note').textContent=e.message;button.disabled=false}finally{button.textContent='Apply changes'}
};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue=''}});
load();setInterval(()=>{if(!dirty)load()},30000);
