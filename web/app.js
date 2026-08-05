let state={reservations:[],dns:[],leases:[]};
let baseline='',dirty=false,toastTimer;
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const config=()=>({reservations:state.reservations,dns:state.dns});
const snapshot=()=>JSON.stringify(config());

function duration(seconds){if(seconds<=0)return'Expired';const m=Math.floor(seconds/60);if(m<60)return`${m}m`;const h=Math.floor(m/60);return h<24?`${h}h ${m%60}m`:`${Math.floor(h/24)}d ${h%24}h`}
function uptime(seconds){if(seconds==null)return'Unavailable';const d=Math.floor(seconds/86400),h=Math.floor(seconds%86400/3600);return d?`${d}d ${h}h`:`${h}h`}
function setMetric(id,value,label,warn,danger){const text=$('#'+id),bar=$('#'+id+'-bar');text.textContent=value==null?'—':label(value);if(!bar)return;bar.style.width=`${Math.min(100,value??0)}%`;bar.className=value>=danger?'danger':value>=warn?'warn':''}
function renderHealth(){
 const s=state.system||{};setMetric('temperature',s.temperature,v=>`${v.toFixed(1)}°C`,70,80);setMetric('load',s.loadPercent,v=>`${Math.round(v)}%`,75,100);setMetric('memory',s.memoryPercent,v=>`${Math.round(v)}%`,80,92);setMetric('storage',s.diskPercent,v=>`${Math.round(v)}%`,80,92);$('#uptime').textContent=uptime(s.uptime);
 const issues=[];if(s.temperature>=80)issues.push('CPU temperature is critical');else if(s.temperature>=70)issues.push('CPU is running warm');if(s.loadPercent>=100)issues.push('CPU load is high');if(s.memoryPercent>=92)issues.push('Memory is nearly full');if(s.diskPercent>=92)issues.push('Storage is nearly full');
 const health=$('.health');health.className=`health${issues.some(x=>x.includes('critical')||x.includes('nearly'))?' danger':issues.length?' warn':''}`;$('#health-title').textContent=issues.length?'Attention recommended':'System nominal';$('#health-detail').textContent=issues.join(' · ')||(s.hostname?`${s.hostname} is running normally`:'No hardware issues detected');
}
function notify(message,error=false){const el=$('#toast');el.textContent=message;el.className=`toast show${error?' error':''}`;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.className='toast',2800)}
function setDirty(){dirty=snapshot()!==baseline;$('#dirty').textContent=dirty?'Changes ready to apply':'No unsaved changes';$('#save-note').textContent=dirty?'Review then apply to dnsmasq':'dnsmasq is up to date';$('#save').disabled=!dirty;$('#discard').disabled=!dirty}
function field(label,key,value,placeholder){return`<div class="field"><label>${label}</label><input data-key="${key}" value="${esc(value)}" placeholder="${placeholder}" autocomplete="off"></div>`}
function isReserved(lease){return state.reservations.some(r=>r.mac.toLowerCase()===lease.mac.toLowerCase()||r.ip===lease.ip)}
function filteredLeases(){const q=$('#lease-filter').value.trim().toLowerCase();return q?state.leases.filter(x=>[x.hostname,x.ip,x.mac].some(v=>String(v).toLowerCase().includes(q))):state.leases}

function renderLeases(){
 const rows=filteredLeases();
 $('#lease-rows').innerHTML=rows.map(x=>{const reserved=isReserved(x),initial=(x.hostname||'?')[0];return`<tr><td><div class="device"><span class="device-icon">${esc(initial)}</span><strong>${esc(x.hostname||'Unknown device')}</strong></div></td><td class="mono">${esc(x.ip)}</td><td><button class="mac-lookup mono" data-lookup-mac="${esc(x.mac)}" title="Look up MAC vendor">${esc(x.mac)}</button></td><td><span class="lease-time">${duration(x.remaining)}</span></td><td>${reserved?'<span class="reserved">Reserved</span>':`<button class="reserve" data-mac="${esc(x.mac)}" data-ip="${esc(x.ip)}" data-host="${esc(x.hostname)}">Reserve</button>`}</td></tr>`}).join('')||'<tr><td colspan="5" class="loading">No matching leases.</td></tr>';
}
function render(){
 $('#lease-count').textContent=state.leases.length;$('#reservation-count').textContent=state.reservations.length;$('#dns-count').textContent=state.dns.length;
 renderHealth();
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
async function showVendor(mac){
 const dialog=$('#vendor-dialog');$('#vendor-name').textContent='Looking up vendor…';$('#vendor-mac').textContent=mac;$('#vendor-note').textContent='Matched against the local IEEE registration database.';dialog.showModal();
 try{const r=await fetch(`/api/vendor?mac=${encodeURIComponent(mac)}`);const out=await r.json();if(!r.ok)throw Error(out.error||'Lookup failed');$('#vendor-name').textContent=out.vendor;$('#vendor-note').textContent=out.private?'This device uses a locally administered address, so its manufacturer is intentionally hidden.':'Matched against the local IEEE registration database.'}
 catch(e){$('#vendor-name').textContent='Vendor unavailable';$('#vendor-note').textContent=e.message}
}

document.addEventListener('click',e=>{
 const tab=e.target.closest('[data-tab]');if(tab)showTab(tab.dataset.tab);
 const add=e.target.closest('[data-add]');if(add){state[add.dataset.add].push(add.dataset.add==='dns'?{hostname:'',ip:''}:{hostname:'',ip:'',mac:''});render();document.querySelector(`#${add.dataset.add==='dns'?'dns':'reservation'}-rows .record:last-child input`)?.focus()}
 const rm=e.target.closest('.remove');if(rm){const row=rm.closest('.record');state[row.dataset.kind].splice(+row.dataset.index,1);render()}
 const reserve=e.target.closest('.reserve');if(reserve)addReservation(reserve);
 const lookup=e.target.closest('[data-lookup-mac]');if(lookup)showVendor(lookup.dataset.lookupMac);
 if(e.target.closest('.dialog-close'))$('#vendor-dialog').close();
});
$('#vendor-dialog').addEventListener('click',e=>{if(e.target===$('#vendor-dialog'))$('#vendor-dialog').close()});
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
