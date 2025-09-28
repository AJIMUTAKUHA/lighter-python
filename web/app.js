const fmt = (x, d=4) => (x==null||isNaN(x)) ? '-' : Number(x).toFixed(d)

let sortKey = 'pair'
let sortAsc = true
let latest = []
let sockets = {}
let filterQ = ''
let enterZ = Number(localStorage.getItem('enterZ')||'2.0')
let exitZ = Number(localStorage.getItem('exitZ')||'0.5')

const statusEl = document.getElementById('status')
const tbody = document.getElementById('pairs-body')

document.getElementById('refresh').addEventListener('click', () => init())
document.getElementById('q').addEventListener('input', (e)=>{ filterQ = e.target.value.trim().toUpperCase(); render(latest) })
document.getElementById('enterZ').value = enterZ
document.getElementById('exitZ').value = exitZ
document.getElementById('enterZ').addEventListener('change', (e)=>{ enterZ=Number(e.target.value||'2.0'); localStorage.setItem('enterZ', String(enterZ)); render(latest) })
document.getElementById('exitZ').addEventListener('change', (e)=>{ exitZ=Number(e.target.value||'0.5'); localStorage.setItem('exitZ', String(exitZ)); render(latest) })

async function fetchJSON(url){
  const r = await fetch(url)
  if(!r.ok) throw new Error(await r.text())
  return await r.json()
}

async function init(){
  statusEl.textContent = 'Loading...'
  const pairs = await fetchJSON('/api/pairs')
  latest = await fetchJSON('/api/latest')
  ensureSockets(pairs)
  render(latest)
  statusEl.textContent = 'Live'
}

function ensureSockets(pairs){
  for(const p of pairs){
    if(sockets[p]) continue
    const ws = new WebSocket(`${location.origin.replace('http','ws')}/ws/stream?pair=${encodeURIComponent(p)}`)
    ws.onopen = () => console.log('ws open', p)
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data)
      // update in latest
      const idx = latest.findIndex(x => x.pair===p)
      if(idx>=0) latest[idx] = {...latest[idx], ...msg}
      else latest.push(msg)
      render(latest)
      // update detail if expanded
      const row = document.querySelector(`tr[data-pair="${p}"]`)
      if(row && row.nextSibling && row.nextSibling.classList.contains('detail-row')){
        const pre = row.nextSibling.querySelector('.last-json')
        pre.textContent = JSON.stringify(latest.find(x=>x.pair===p), null, 2)
      }
    }
    ws.onclose = () => { delete sockets[p] }
    sockets[p] = ws
  }
}

function render(rows){
  const filtered = rows.filter(r => !filterQ || (r.pair||'').toUpperCase().includes(filterQ))
  const sorted = [...filtered].sort((a,b)=>{
    const ka = a[sortKey]; const kb = b[sortKey]
    const va = typeof ka==='string' ? ka : Number(ka||0)
    const vb = typeof kb==='string' ? kb : Number(kb||0)
    if(va<vb) return sortAsc?-1:1
    if(va>vb) return sortAsc?1:-1
    return 0
  })
  tbody.innerHTML = ''
  for(const r of sorted){
    const tr = document.createElement('tr')
    tr.dataset.pair = r.pair
    if(r.stale){ tr.classList.add('stale') }
    const zClass = Math.abs(r.z||0) >= enterZ ? 'pos' : (Math.abs(r.z||0) <= exitZ ? 'muted':'')
    tr.innerHTML = `
      <td><button class="expander">${r.pair}</button></td>
      <td>${fmt(r.price_a,2)}</td>
      <td>${fmt(r.price_b,2)}</td>
      <td>${fmt(r.spread,4)}</td>
      <td class="${zClass}">${fmt(r.z,3)}</td>
      <td>${fmt(r.ema,4)}</td>
      <td>${fmt(r.center_dev,3)}</td>
      <td>${fmt(r.ob_spread_a,4)}</td>
      <td>${fmt(r.ob_spread_b,4)}</td>
      <td>${fmt(r.ob_spread_pct_a*100,2)}%</td>
      <td>${fmt(r.ob_spread_pct_b*100,2)}%</td>
      <td>${fmt(r.vol_a,0)}</td>
      <td>${fmt(r.vol_b,0)}</td>
      <td>${fmt(r.depth_qty_a,2)}</td>
      <td>${fmt(r.depth_qty_b,2)}</td>
      <td>${fmt(r.maker_fee_a,4)}/${fmt(r.taker_fee_a,4)}</td>
      <td>${fmt(r.maker_fee_b,4)}/${fmt(r.taker_fee_b,4)}</td>
      <td>${fmt(r.fr_a,6)}</td>
      <td>${fmt(r.fr_b,6)}</td>
      <td>${formatCountdown(r.fr_countdown_ms)}</td>
      <td>${formatDuration(r.t_exit_s)}</td>
      <td>${fmt(r.skew_ms,0)}ms</td>
      <td>${fmt(r.latency_ms,0)}ms</td>
    `
    tr.addEventListener('click', ()=>toggleDetail(tr, r.pair))
    tbody.appendChild(tr)
  }
}

async function toggleDetail(row, pair){
  const next = row.nextSibling
  if(next && next.classList && next.classList.contains('detail-row')){
    next.remove()
    return
  }
  const tpl = document.getElementById('detail-template')
  const clone = tpl.content.cloneNode(true)
  row.after(clone)
  const detailRow = row.nextSibling
  const ctx = detailRow.querySelector('canvas').getContext('2d')
  const pre = detailRow.querySelector('.last-json')
  const adv = detailRow.querySelector('.advice')
  pre.textContent = JSON.stringify(latest.find(x=>x.pair===pair)||{}, null, 2)
  adv.textContent = (latest.find(x=>x.pair===pair)||{}).advice || '-'
  const hist = await fetchJSON(`/api/spreads?pair=${encodeURIComponent(pair)}&limit=500`)
  const labels = hist.map(x=> new Date(x.ts_ms).toLocaleTimeString())
  const spread = hist.map(x=> x.spread)
  const z = hist.map(x=> x.z)
  new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {label:'Spread', data: spread, borderColor:'#22d3ee', fill:false, yAxisID:'y'},
        {label:'Z', data: z, borderColor:'#10b981', fill:false, yAxisID:'y1'}
      ]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: true } },
      scales: {
        y: { type:'linear', position:'left' },
        y1: { type:'linear', position:'right', grid:{ drawOnChartArea:false } }
      }
    }
  })

  // order walls
  try{
    const depth = await fetchJSON(`/api/depth?pair=${encodeURIComponent(pair)}&levels=50`)
    const owA = detailRow.querySelector('.ow-a').getContext('2d')
    const owB = detailRow.querySelector('.ow-b').getContext('2d')
    const bidsA = (depth.a.bids||[]).slice(0,50)
    const asksA = (depth.a.asks||[]).slice(0,50)
    const bidsB = (depth.b.bids||[]).slice(0,50)
    const asksB = (depth.b.asks||[]).slice(0,50)

    const labelsA = [...bidsA.map(x=>x[0]).reverse(), ...asksA.map(x=>x[0])]
    const dataA = [...bidsA.map(x=>x[1]).reverse(), ...asksA.map(x=>x[1])]
    new Chart(owA, {
      type:'bar',
      data:{ labels: labelsA, datasets:[{ label:'Qty', data:dataA, backgroundColor:'#334155' }]},
      options:{ scales:{ x:{ ticks:{ display:false }}, y:{ beginAtZero:true } }, plugins:{ legend:{ display:false } } }
    })

    const labelsB = [...bidsB.map(x=>x[0]).reverse(), ...asksB.map(x=>x[0])]
    const dataB = [...bidsB.map(x=>x[1]).reverse(), ...asksB.map(x=>x[1])]
    new Chart(owB, {
      type:'bar',
      data:{ labels: labelsB, datasets:[{ label:'Qty', data:dataB, backgroundColor:'#334155' }]},
      options:{ scales:{ x:{ ticks:{ display:false }}, y:{ beginAtZero:true } }, plugins:{ legend:{ display:false } } }
    })
  }catch(e){ console.warn('depth fetch failed', e) }

  // simulate
  const simOut = detailRow.querySelector('.sim-out')
  const simNotional = detailRow.querySelector('.sim-notional')
  const btnShort = detailRow.querySelector('.sim-a-short-b-long')
  const btnLong = detailRow.querySelector('.sim-a-long-b-short')
  const runSim = async (pattern) => {
    try{
      const n = Number(simNotional.value||'1000')
      const res = await fetchJSON(`/api/simulate?pair=${encodeURIComponent(pair)}&notional_usd=${n}&pattern=${pattern}`)
      simOut.textContent = `总成本: $${fmt(res.total_cost_usd,2)}\n`+
        `A: mid=${fmt(res.mid_a,2)} avg=${fmt(res.avg_a,2)} slip=${fmt(res.slip_a_pct*100,2)}% fee=$${fmt(res.fee_a_usd,2)} filled=${fmt(res.filled_base_a,6)}\n`+
        `B: mid=${fmt(res.mid_b,2)} avg=${fmt(res.avg_b,2)} slip=${fmt(res.slip_b_pct*100,2)}% fee=$${fmt(res.fee_b_usd,2)} filled=${fmt(res.filled_base_b,6)}`
    }catch(e){ simOut.textContent = '模拟失败: '+e }
  }
  btnShort.addEventListener('click', ()=>runSim('enter_short_A_long_B'))
  btnLong.addEventListener('click', ()=>runSim('enter_long_A_short_B'))

  // stats bins
  const stDays = detailRow.querySelector('.st-days')
  const stExit = detailRow.querySelector('.st-exit')
  const stEdges = detailRow.querySelector('.st-edges')
  const stBtn = detailRow.querySelector('.st-refresh')
  const stBody = detailRow.querySelector('.st-table tbody')
  stExit.value = String(exitZ)
  async function loadStats(){
    try{
      const url = `/api/stats/bins?pair=${encodeURIComponent(pair)}&days=${encodeURIComponent(stDays.value||'7')}&exit_z=${encodeURIComponent(stExit.value||'0.5')}&edges=${encodeURIComponent(stEdges.value||'1.5,2,2.5,3')}`
      const stats = await fetchJSON(url)
      stBody.innerHTML = ''
      for(const b of stats.stats){
        const tr = document.createElement('tr')
        const hi = (b.bin.hi==null)?'+∞':b.bin.hi
        const prob = (b.prob_exit_before_funding==null)?'-':(b.prob_exit_before_funding*100).toFixed(1)+'%'
        tr.innerHTML = `
          <td>[${b.bin.lo}, ${hi})</td>
          <td>${b.samples}</td>
          <td>${fmt(b.p25_s,0)}</td>
          <td>${fmt(b.median_s,0)}</td>
          <td>${fmt(b.p75_s,0)}</td>
          <td>${fmt(b.p90_s,0)}</td>
          <td>${prob}</td>
        `
        stBody.appendChild(tr)
      }
    }catch(e){ stBody.innerHTML = '<tr><td colspan="7">统计加载失败</td></tr>' }
  }
  stBtn.addEventListener('click', loadStats)
  await loadStats()
}

function formatCountdown(ms){
  if(ms==null || isNaN(ms)) return '-'
  const s = Math.max(0, Math.floor(Number(ms)/1000))
  const h = Math.floor(s/3600)
  const m = Math.floor((s%3600)/60)
  const ss = s%60
  return `${h}h ${m}m ${ss}s`
}

function formatDuration(s){
  if(s==null || isNaN(s)) return '-'
  const sec = Math.max(0, Math.floor(Number(s)))
  const h = Math.floor(sec/3600)
  const m = Math.floor((sec%3600)/60)
  const ss = sec%60
  return `${h}h ${m}m ${ss}s`
}

// sort handlers
document.querySelectorAll('thead th').forEach(th=>{
  th.addEventListener('click', ()=>{
    const key = th.dataset.sort
    if(sortKey===key){ sortAsc = !sortAsc } else { sortKey = key; sortAsc = false }
    render(latest)
  })
})

init().catch(e=>{
  console.error(e)
  statusEl.textContent = 'Error'
})
