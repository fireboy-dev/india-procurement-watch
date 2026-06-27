/* ═══════════════════════════════════════════
   network.js — Contract Network Explorer
   Search a company/buyer → render its ego-network (vis-network)
   Data comes from /api/network/* (served from network.db).
   ═══════════════════════════════════════════ */

const NET_EDGE_COLORS = {
  AWARDED:        '#6b7280',
  CO_BIDDER:      '#8b5cf6',
  SHARES_EMAIL:   '#f87171',
  SHARES_ADDRESS: '#f5b942',
};
const NET_NODE_COLORS = { company: '#4f8ef7', buyer: '#8b5cf6' };

let netVis = null;            // vis.Network instance
let netNodeMeta = {};         // node_id -> full metadata (for panel/tooltip)
let netSearchTimer = null;

// ── INIT (called from main.js after the dashboard reveals) ──
async function initNetwork() {
  const section = document.getElementById('networkSection');
  if (!section) return;
  let stats;
  try {
    stats = await fetch('/api/network/stats').then(r => r.json());
  } catch (e) {
    stats = { ready: false };
  }

  if (!stats.ready) {
    document.getElementById('netNotice').style.display = 'block';
    document.getElementById('netExplorer').style.display = 'none';
    return;
  }

  document.getElementById('netNotice').style.display = 'none';
  document.getElementById('netExplorer').style.display = 'block';
  document.getElementById('netStats').textContent =
    `${fmtNum(stats.companies)} companies · ${fmtNum(stats.buyers)} buyers · ${fmtNum(stats.edges_total)} links`;

  // search wiring
  const input = document.getElementById('netSearchInput');
  input.addEventListener('input', () => {
    clearTimeout(netSearchTimer);
    netSearchTimer = setTimeout(netDoSearch, 220);
  });
  document.getElementById('netTypeFilter').addEventListener('change', netDoSearch);
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.net-search-wrap')) hideSuggest();
  });

  if (window.lucide) lucide.createIcons();

  // deep link: /?focus=<node_id> auto-renders that node's network
  const focus = new URLSearchParams(location.search).get('focus');
  if (focus) loadEgo(focus);
}

// ── SEARCH ──
async function netDoSearch() {
  const q = document.getElementById('netSearchInput').value.trim();
  const type = document.getElementById('netTypeFilter').value;
  if (q.length < 2) { hideSuggest(); return; }

  try {
    const params = new URLSearchParams({ q });
    if (type) params.set('type', type);
    const data = await fetch(`/api/network/search?${params}`).then(r => r.json());
    renderSuggest(data.results || []);
  } catch (e) {
    hideSuggest();
  }
}

function renderSuggest(results) {
  const box = document.getElementById('netSuggest');
  if (!results.length) {
    box.innerHTML = `<div class="net-suggest-item" style="cursor:default">
      <span class="net-suggest-name" style="color:var(--text-muted)">No matches</span></div>`;
    box.style.display = 'block';
    return;
  }
  box.innerHTML = results.map(r => {
    const tag = r.ntype === 'company'
      ? '<span class="net-type-tag net-type-company">Co</span>'
      : '<span class="net-type-tag net-type-buyer">Buyer</span>';
    const meta = r.ntype === 'company'
      ? `${fmtNum(r.n_contracts)} contracts · ₹${fmtNum(r.total_value_cr)} Cr`
      : `${fmtNum(r.n_contracts)} awards`;
    return `<div class="net-suggest-item" onclick="netSelect('${encodeURIComponent(r.node_id)}')">
        <span class="net-suggest-name">${tag}${escapeHtml(r.label || '(unnamed)')}</span>
        <span class="net-suggest-meta">${meta}</span>
      </div>`;
  }).join('');
  box.style.display = 'block';
}

function hideSuggest() {
  const box = document.getElementById('netSuggest');
  if (box) box.style.display = 'none';
}

// ── SELECT / LOAD EGO ──
function netSelect(encId) {
  const id = decodeURIComponent(encId);
  hideSuggest();
  document.getElementById('netSearchInput').value = '';
  loadEgo(id);
}

async function loadEgo(nodeId) {
  const empty = document.getElementById('netEmpty');
  empty.textContent = 'Loading network…';
  empty.style.display = 'flex';

  let data;
  try {
    data = await fetch(`/api/network/ego?id=${encodeURIComponent(nodeId)}&limit=60`).then(r => r.json());
  } catch (e) {
    empty.textContent = '⚠️ Could not load network.';
    return;
  }
  if (!data.nodes || !data.nodes.length) {
    empty.textContent = 'No connections found for this node.';
    return;
  }
  empty.style.display = 'none';

  // build vis datasets
  netNodeMeta = {};
  const visNodes = data.nodes.map(n => {
    netNodeMeta[n.node_id] = n;
    const base = NET_NODE_COLORS[n.ntype] || '#4f8ef7';
    const size = Math.min(10 + Math.sqrt(n.n_contracts || 1) * 2.2, 42);
    return {
      id: n.node_id,
      label: truncate(n.label || '(unnamed)', 22),
      title: tooltipFor(n),
      color: {
        background: n.focus ? '#ffffff' : base,
        border: n.focus ? '#f5b942' : base,
        highlight: { background: base, border: '#f5b942' },
      },
      borderWidth: n.focus ? 4 : 2,
      size: n.focus ? Math.max(size, 22) : size,
      font: { color: '#e8ecf5', size: n.focus ? 15 : 12, face: 'Inter' },
    };
  });

  const visEdges = data.edges.map(e => {
    const c = NET_EDGE_COLORS[e.etype] || '#6b7280';
    return {
      from: e.src, to: e.dst,
      color: { color: c, opacity: 0.55, highlight: c },
      width: Math.min(1 + Math.log2((e.weight || 1) + 1), 6),
      title: `${e.etype.replace('_', ' ')}${e.label ? ' · ' + e.label : ''}`,
      dashes: (e.etype === 'SHARES_EMAIL' || e.etype === 'SHARES_ADDRESS'),
    };
  });

  const container = document.getElementById('netGraph');
  const visData = {
    nodes: new vis.DataSet(visNodes),
    edges: new vis.DataSet(visEdges),
  };
  const options = {
    nodes: { shape: 'dot', borderWidth: 2, shadow: false },
    edges: { smooth: { type: 'continuous' } },
    physics: {
      stabilization: { iterations: 150, fit: true },
      barnesHut: { gravitationalConstant: -14000, springLength: 130,
                   springConstant: 0.04, damping: 0.45, avoidOverlap: 0.2 },
    },
    interaction: { hover: true, tooltipDelay: 120, hideEdgesOnDrag: true },
  };

  if (netVis) { netVis.setData(visData); netVis.setOptions(options); }
  else {
    netVis = new vis.Network(container, visData, options);
    netVis.on('click', (params) => {
      if (params.nodes.length) {
        const id = params.nodes[0];
        renderPanel(netNodeMeta[id]);
        if (!netNodeMeta[id].focus) loadEgo(id);   // clicking a neighbour re-centres
      }
    });
    netVis.on('stabilizationIterationsDone', () => netVis.setOptions({ physics: false }));
  }
  // re-enable physics for the new layout, then freeze again
  netVis.setOptions({ physics: { enabled: true } });
  netVis.once('stabilizationIterationsDone', () => netVis.setOptions({ physics: false }));

  // focus node details into the panel
  const focus = data.nodes.find(n => n.focus) || data.nodes[0];
  renderPanel(focus);
}

// ── TOOLTIP ──
function tooltipFor(n) {
  if (n.ntype === 'company') {
    return `${n.label}\n${n.state || ''} · ${n.status || ''}\n` +
           `${fmtNum(n.n_contracts)} contracts · ₹${fmtNum(n.total_value_cr)} Cr`;
  }
  return `${n.label}\nGovernment buyer · ${n.state || ''}\n${fmtNum(n.n_contracts)} awards`;
}

// ── SIDE PANEL ──
function renderPanel(n) {
  if (!n) return;
  const panel = document.getElementById('netPanel');
  const isCo = n.ntype === 'company';
  const tag = isCo
    ? '<span class="net-type-tag net-type-company">Company</span>'
    : '<span class="net-type-tag net-type-buyer">Govt Buyer</span>';

  const fields = [];
  const add = (k, v) => { if (v) fields.push(
    `<div class="net-field"><div class="net-field-key">${k}</div><div class="net-field-val">${escapeHtml(String(v))}</div></div>`); };

  if (isCo) {
    add('CIN', n.cin);
    add('Status', n.status);
    add('Registered State', n.state);
    add('Email', n.email);
    add('Registered Address', n.address);
    add('Business Activity', n.activity);
    add('RoC', n.roc);
  } else {
    add('Region / State', n.state);
  }

  const metrics = isCo
    ? [['Contracts', fmtNum(n.n_contracts)], ['Value', '₹' + fmtNum(n.total_value_cr) + ' Cr'],
       ['Buyers', fmtNum(n.n_partners)], ['Links', fmtNum(n.degree)]]
    : [['Awards', fmtNum(n.n_contracts)], ['Value', '₹' + fmtNum(n.total_value_cr) + ' Cr'],
       ['Vendors', fmtNum(n.n_partners)], ['Links', fmtNum(n.degree)]];

  panel.innerHTML = `
    <div class="net-panel-title">${tag}${escapeHtml(n.label || '(unnamed)')}</div>
    <div class="net-panel-sub">${n.node_id}</div>
    <div class="net-metric-row">
      ${metrics.map(m => `<div class="net-metric"><div class="net-metric-val">${m[1]}</div><div class="net-metric-lbl">${m[0]}</div></div>`).join('')}
    </div>
    ${fields.join('')}
    <div class="net-hint">Click any node in the graph to recenter on it.</div>
  `;
}

// ── UTIL ──
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
