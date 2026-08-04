let state={reservations:[],dns:[],leases:[]},dirty=false;
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function duration(n){if(n<=0)return 'Expired';let m=Math.floor(n/60);if(m<60)return `${m}m`;let h=Math.floor(m/60);return h<24?`${h}h ${m%60}m`:`${Math.floor(h/24)}d ${h%24}h`}
function setDirty(v=true){dirty=v;$('#dirty').textContent=v?'Unsaved changes':'No unsaved changes';$('#dirty').className=v?'error':'';$('#save').disabled=!v}
function field(label,key,value){return `<div class="field"><label>${label}</label><input data-key="${key}" value="${esc(value)}"></div>`}
function render(){
 $('#lease-count').textContent=state.leases.length;
 $('#lease-rows').innerHTML=state.leases.map(x=>`<tr><td><strong>${esc(x.hostname||'Unknown')}</strong></td><td class="mono">${esc(x.ip)}</td><td class="mono">${esc(x.mac)}</td><td>${duration(x.remaining)}</td></tr>`).join('')||'<tr><td colspan="4">No active leases.</td></tr>';
 const rr=$('#reservation-rows');rr.innerHTML=state.reservations.map((x,i)=>`<div class="record" data-index="${i}" data-kind="reservations">${field('Hostname','hostname',x.hostname)}${field('IP address','ip',x.ip)}${field('MAC address','mac',x.mac)}<button class="remove" aria-label="Remove reservation">Remove</button></div>`).join('')||'<div class="empty">No reservations yet.</div>';
 const dr=$('#dns-rows');dr.innerHTML=state.dns.map((x,i)=>`<div class="record dns" data-index="${i}" data-kind="dns">${field('Hostname','hostname',x.hostname)}${field('IP address','ip',x.ip)}<button class="remove" aria-label="Remove DNS record">Remove</button></div>`).join('')||'<div class="empty">No DNS records yet.</div>';
}
async function load(){try{let r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw Error('Could not reach service');state=await r.json();render();$('#status').textContent='dnsmasq online';$('#status').className='status ok'}catch(e){$('#status').textContent=e.message;$('#status').className='status bad'}}
document.addEventListener('click',e=>{let tab=e.target.closest('[data-tab]');if(tab){document.querySelectorAll('nav button,.panel').forEach(x=>x.classList.remove('active'));tab.classList.add('active');$('#'+tab.dataset.tab).classList.add('active')}let add=e.target.closest('[data-add]');if(add){state[add.dataset.add].push(add.dataset.add==='dns'?{hostname:'',ip:''}:{hostname:'',ip:'',mac:''});render();setDirty()}let rm=e.target.closest('.remove');if(rm){let row=rm.closest('.record');state[row.dataset.kind].splice(+row.dataset.index,1);render();setDirty()}});
document.addEventListener('input',e=>{let row=e.target.closest('.record');if(row){state[row.dataset.kind][+row.dataset.index][e.target.dataset.key]=e.target.value;setDirty()}});
$('#refresh').onclick=load;
$('#save').onclick=async()=>{let b=$('#save');b.disabled=true;b.textContent='Applying…';try{let r=await fetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({reservations:state.reservations,dns:state.dns})});let out=await r.json();if(!r.ok)throw Error(out.error||'Apply failed');setDirty(false);await load()}catch(e){$('#dirty').textContent=e.message;$('#dirty').className='error';b.disabled=false}finally{b.textContent='Apply changes'}};
load();setInterval(()=>{if(!dirty)load()},30000);
