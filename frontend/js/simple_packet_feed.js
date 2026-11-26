/**
 * Simple live packet feed for the local Meshpoint dashboard.
 * Renders incoming packets via WebSocket; row click opens PacketDetailModal.
 */
class SimplePacketFeed {
    constructor(tbodyId, maxRows) {
        this._tbody = document.getElementById(tbodyId);
        this._maxRows = maxRows || 200;
        this._count = 0;
        this._packets = [];
        this._nodeByLastByte = new Map();
        this._onFocus = null;
        this._nodes = [];
        this._sourceIds = new Set();
        this._sourceMode = 'both';
        this._installSourceFilter();
        this._columnFilters = new Map();
        this._installColumnFilters();
        const pills = document.getElementById('packet-preset-pills');
        if (window.MeshpointRadioViewFilter) {
            MeshpointRadioViewFilter.wirePills(pills);
            document.addEventListener(MeshpointRadioViewFilter.EVENT, () => this._render());
        }
    }

    setOnFocus(cb) {
        this._onFocus = cb;
    }

    loadNodes(nodes) {
        this._nodes = nodes;
        this._nodeByLastByte.clear();
        for (const node of nodes) {
            const id = node.node_id;
            if (id && id.length >= 2) {
                this._nodeByLastByte.set(id.slice(-2).toLowerCase(), id);
            }
        }
    }

    addPacket(packet) {
        this._packets.unshift(packet);
        if (this._packets.length > this._maxRows) {
            this._packets.length = this._maxRows;
        }
        this._count += 1;
        if (window.MeshpointRadioViewFilter) {
            MeshpointRadioViewFilter.notePacket(packet);
        }
        this._render();
    }

    _render() {
        if (!this._tbody) return;
        const key = window.MeshpointRadioViewFilter
            ? MeshpointRadioViewFilter.current()
            : 'all';
        const radioVisible = window.MeshpointRadioViewFilter
            ? this._packets.filter((p) => MeshpointRadioViewFilter.matchesPacket(p, key))
            : this._packets;
        const visible = radioVisible.filter(p => this._matchesSource(p) && this._matchesColumns(p));
        this._tbody.replaceChildren();
        for (const packet of visible) {
            this._tbody.appendChild(this._buildRow(packet));
        }
        const countEl = document.getElementById('packet-count');
        if (countEl) countEl.textContent = (this._sourceIds.size || this._columnFilters.size) ? `${visible.length}/${this._packets.length}` : this._count;
    }



    _columnValue(p, key) {
        const sig = p.signal || {};
        switch(key) {
            case 'time': return this._formatTime(p);
            case 'protocol': return p.protocol || 'meshtastic';
            case 'dest': return this._shortId(p.destination_id);
            case 'type': return p.packet_type || '--';
            case 'rssi': return this._displayRssi(sig.rssi ?? p.rssi);
            case 'snr': return sig.snr ?? p.snr;
            case 'preset': return window.ModemPresetLabel ? ModemPresetLabel.fromPacket(p)?.chip || '--' : '--';
            case 'freq': return sig.frequency_mhz ?? p.frequency_mhz;
            case 'sf': return sig.spreading_factor ?? p.spreading_factor;
            case 'hops': {
                const n = p.protocol === 'meshcore' ? p.decoded_payload?.meshcore_hops :
                    p.hop_start > 0 ? p.hop_start-p.hop_limit : null;
                return n == null ? 'Unknown' : n === 0 ? 'Direct' : 'Relayed';
            }
            case 'details': return this._summarize(p);
        }
    }

    _matchesColumns(p) {
        for (const [key, f] of this._columnFilters) {
            const v = this._columnValue(p,key);
            if (f.kind === 'set' && !f.values.has(String(v ?? '--'))) return false;
            if (f.kind === 'text' && !String(v ?? '').toLowerCase().includes(f.value)) return false;
            if (f.kind === 'range' && (v == null || !Number.isFinite(Number(v)) ||
                (f.min !== '' && Number(v)<Number(f.min)) ||
                (f.max !== '' && Number(v)>Number(f.max)))) return false;
        }
        return true;
    }

    _installColumnFilters() {
        const headers = this._tbody?.closest('table')?.querySelectorAll('thead th') || [];
        for (const th of headers) {
            const title = th.textContent.trim();
            const key = title.toLowerCase();
            if (!['time','protocol','dest','type','rssi','snr','preset','freq','sf','hops','details'].includes(key)) continue;
            const kind = ['rssi','snr'].includes(key) ? 'range' : ['time','details'].includes(key) ? 'text' : 'set';
            const btn = document.createElement('button');
            btn.type='button';
            btn.textContent=title+' ▾';
            btn.style.cssText='background:none;border:0;color:inherit;font:inherit;cursor:pointer;padding:0';
            btn.setAttribute('aria-expanded','false');
            th.replaceChildren(btn);
            const panel=document.createElement('div');
            panel.hidden=true;
            panel.style.cssText='position:fixed;z-index:10000;width:min(250px,90vw);padding:10px;background:#172033;color:#e2e8f0;border:1px solid #475569;border-radius:6px;box-shadow:0 6px 20px #0006;font:13px sans-serif;text-align:left';
            document.body.append(panel);
            const apply=f=>{
                if(f) this._columnFilters.set(key,f); else this._columnFilters.delete(key);
                btn.style.color=f?'#22d3ee':'inherit';
                this._render();
            };
            const close=()=>{panel.hidden=true;btn.setAttribute('aria-expanded','false');};
            const build=()=>{
                panel.replaceChildren();
                const current=this._columnFilters.get(key);
                if(kind==='range'){
                    const inputs=['min','max'].map(k=>{
                        const el=document.createElement('input');
                        el.type='number';el.step='any';el.placeholder=k==='min'?'Minimum':'Maximum';
                        el.value=current?.[k]??'';
                        el.style.cssText='width:100%;box-sizing:border-box;margin-bottom:6px';
                        panel.append(el);return el;
                    });
                    const update=()=>{const [min,max]=inputs.map(el=>el.value);apply(min===''&&max===''?null:{kind,min,max});};
                    inputs.forEach(el=>el.addEventListener('input',update));
                }else if(kind==='text'){
                    const el=document.createElement('input');el.type='search';el.placeholder='Contains...';el.value=current?.value||'';
                    el.style.cssText='width:100%;box-sizing:border-box;margin-bottom:6px';
                    el.addEventListener('input',()=>apply(el.value?{kind,value:el.value.toLowerCase()}:null));panel.append(el);
                }else{
                    const values=new Set(this._packets.map(p=>String(this._columnValue(p,key)??'--')));
                    if(key==='hops') ['Direct','Relayed','Unknown'].forEach(v=>values.add(v));
                    if(key==='type') ['text','position','telemetry','nodeinfo','routing','admin','traceroute','neighborinfo','waypoint','range_test','store_forward','detection_sensor','paxcounter','map_report','encrypted','unknown'].forEach(v=>values.add(v));
                    if(current) current.values.forEach(v=>values.add(v));
                    const list=document.createElement('div');list.style.cssText='max-height:240px;overflow:auto';
                    const selected=new Set(current?.values || []);
                    for(const v of [...values].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}))){
                        const label=document.createElement('label');label.style.cssText='display:flex;gap:6px;padding:4px;cursor:pointer';
                        const cb=document.createElement('input');cb.type='checkbox';cb.checked=selected.has(v);
                        cb.addEventListener('change',()=>{if(cb.checked)selected.add(v);else selected.delete(v);apply(selected.size===0?null:{kind,values:new Set(selected)});});
                        label.append(cb,document.createTextNode(v));list.append(label);
                    }
                    panel.append(list);
                }
                const clear=document.createElement('button');clear.type='button';clear.textContent='Clear filter';clear.style.marginTop='8px';
                clear.addEventListener('click',e=>{e.stopPropagation();apply(null);build();});panel.append(clear);
            };
            btn.addEventListener('click',()=>{
                if(!panel.hidden){close();return;}
                build();const rect=btn.getBoundingClientRect();
                panel.style.left=Math.max(4,Math.min(rect.left,window.innerWidth-260))+'px';
                panel.style.top=Math.max(4,Math.min(rect.bottom+4,window.innerHeight-330))+'px';
                panel.hidden=false;btn.setAttribute('aria-expanded','true');
            });
            document.addEventListener('click',e=>{if(!panel.contains(e.target)&&!btn.contains(e.target))close();});
            document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!panel.hidden){close();btn.focus();}});
            window.addEventListener('resize',close);
        }
    }

    _normalizeId(id) {
        return String(id || '').replace(/^!/, '').toLowerCase();
    }

    _matchesSource(packet) {
        if (!this._sourceIds.size) return true;
        const sender = this._sourceIds.has(this._normalizeId(packet.source_id));
        const relay = Number(packet.relay_node || 0);
        const relayed = relay > 0 && [...this._sourceIds].some(id =>
            /^[0-9a-f]{8}$/.test(id) && parseInt(id.slice(-2), 16) === relay);
        return (this._sourceMode !== 'relay' && sender) ||
            (this._sourceMode !== 'sender' && relayed);
    }

    _installSourceFilter() {
        const th = [...(this._tbody?.closest('table')?.querySelectorAll('thead th') || [])]
            .find(el => el.textContent.trim().toLowerCase() === 'source');
        if (!th) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = 'Source ▾';
        button.style.cssText = 'background:none;border:0;color:inherit;font:inherit;cursor:pointer;padding:0';
        button.setAttribute('aria-expanded', 'false');
        th.replaceChildren(button);
        const panel = document.createElement('div');
        panel.hidden = true;
        panel.style.cssText = 'position:fixed;z-index:10000;width:min(320px,90vw);padding:10px;background:#172033;color:#e2e8f0;border:1px solid #475569;border-radius:6px;box-shadow:0 6px 20px #0006;font:13px sans-serif;text-align:left';
        document.body.appendChild(panel);
        const search = document.createElement('input');
        search.type = 'search';
        search.placeholder = 'Search name or Node ID...';
        search.style.cssText = 'width:100%;box-sizing:border-box;margin-bottom:8px;padding:6px';
        const mode = document.createElement('select');
        mode.style.cssText = 'width:100%;margin-bottom:8px';
        for (const [value,label] of [['both','Sender + Relay'],['sender','Sender'],['relay','Relay (last byte)']]) {
            mode.add(new Option(label,value));
        }
        const list = document.createElement('div');
        list.style.cssText = 'max-height:240px;overflow:auto';
        const clear = document.createElement('button');
        clear.type = 'button';
        clear.textContent = 'Clear filter';
        clear.style.marginTop = '8px';
        panel.append(search,mode,list,clear);
        const refresh = () => {
            const q = this._normalizeId(search.value.trim());
            const candidates = new Map();
            for (const n of this._nodes) {
                const id = this._normalizeId(n.node_id);
                if (id) candidates.set(id, [n.long_name,n.short_name,n.display_name].filter(Boolean).join(' '));
            }
            for (const p of this._packets) {
                const id = this._normalizeId(p.source_id);
                if (id && !candidates.has(id)) candidates.set(id,'');
            }
            for (const id of this._sourceIds) if (!candidates.has(id)) candidates.set(id,'');
            list.replaceChildren();
            for (const [id,name] of [...candidates].sort((a,b)=>(a[1]||a[0]).localeCompare(b[1]||b[0]))) {
                if (!(id+' '+name.toLowerCase()).includes(q)) continue;
                const label = document.createElement('label');
                label.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px;cursor:pointer;overflow-wrap:anywhere';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.checked = this._sourceIds.has(id);
                cb.addEventListener('change', () => {
                    if (cb.checked) this._sourceIds.add(id); else this._sourceIds.delete(id);
                    apply();
                });
                label.append(cb,document.createTextNode((name ? name+' — ' : '')+'!'+id));
                list.append(label);
            }
            if (!list.childElementCount) list.textContent = 'No matching nodes';
        };
        const apply = () => {
            button.style.color = this._sourceIds.size ? '#22d3ee' : 'inherit';
            button.textContent = this._sourceIds.size ? 'Source ('+this._sourceIds.size+') ▾' : 'Source ▾';
            this._render();
        };
        const close = () => { panel.hidden = true; button.setAttribute('aria-expanded','false'); };
        button.addEventListener('click', () => {
            if (!panel.hidden) { close(); return; }
            refresh();
            const rect = button.getBoundingClientRect();
            panel.style.left = Math.max(4,Math.min(rect.left,window.innerWidth-330))+'px';
            panel.style.top = Math.max(4,Math.min(rect.bottom+4,window.innerHeight-360))+'px';
            panel.hidden = false;
            button.setAttribute('aria-expanded','true');
            search.focus();
        });
        search.addEventListener('input',refresh);
        mode.addEventListener('change',()=>{this._sourceMode=mode.value;apply();});
        clear.addEventListener('click',()=>{this._sourceIds.clear();search.value='';mode.value='both';this._sourceMode='both';refresh();apply();});
        document.addEventListener('click',e=>{if (!panel.contains(e.target)&&!button.contains(e.target)) close();});
        document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!panel.hidden){close();button.focus();}});
        window.addEventListener('resize',close);
    }

    _buildRow(packet) {
        const tr = document.createElement('tr');
        tr.classList.add('packet-row', 'packet-row--new');
        tr.addEventListener('animationend', () => tr.classList.remove('packet-row--new'));
        tr.innerHTML = this._rowHtml(packet);
        tr.addEventListener('click', () => this._openDetail(tr, packet));
        return tr;
    }

    _rowHtml(packet) {
        const time = this._formatTime(packet);
        const srcShort = this._shortId(packet.source_id);
        const relayByte = packet.relay_node || 0;
        const srcCell = relayByte
            ? `${srcShort} <span class="relay-hop">↝ ${this._resolveRelay(relayByte)}</span>`
            : srcShort;
        const sig = packet.signal || {};
        const rawRssi = sig.rssi != null ? sig.rssi : packet.rssi;
        const rawSnr = sig.snr != null ? sig.snr : packet.snr;
        const rssiVal = this._displayRssi(rawRssi);
        const type = packet.packet_type || '--';
        const protocol = packet.protocol || 'meshtastic';
        const mcHops = packet.decoded_payload?.meshcore_hops;
        const hops = protocol === 'meshcore'
            ? (Number.isInteger(mcHops) && mcHops >= 0 && mcHops <= 63 ? String(mcHops) : '--')
            : packet.hop_start > 0
            ? `${packet.hop_start - packet.hop_limit}/${packet.hop_start}`
            : '--';
        const freqMhz = sig.frequency_mhz || packet.frequency_mhz;
        const freq = freqMhz ? `${Number(freqMhz).toFixed(1)}` : '--';
        const sfVal = sig.spreading_factor || packet.spreading_factor;
        const sf = sfVal ? `SF${sfVal}` : '--';
        return `
            <td>${time}</td>
            <td class="protocol-${protocol}">${protocol}</td>
            <td class="td-source">${srcCell}</td>
            <td>${this._shortId(packet.destination_id)}</td>
            <td class="type-${type.replace(/[^a-zA-Z0-9_-]/g, '')}">${type}</td>
            <td class="${this._rssiClass(rssiVal)}">${rssiVal != null ? rssiVal : '--'}</td>
            <td>${rawSnr != null ? Number(rawSnr).toFixed(1) : '--'}</td>
            <td>${this._presetChip(packet)}</td>
            <td class="td-freq">${freq}</td>
            <td class="td-sf">${sf}</td>
            <td>${hops}</td>
            <td class="packet-details-cell type-${type.replace(/[^a-zA-Z0-9_-]/g, '')}">${this._esc(this._summarize(packet))}</td>
        `;
    }

    _formatTime(packet) {
        if (packet.rx_time) return new Date(packet.rx_time * 1000).toLocaleTimeString();
        if (packet.timestamp) return new Date(packet.timestamp).toLocaleTimeString();
        return new Date().toLocaleTimeString();
    }

    _presetChip(packet) {
        const preset = window.ModemPresetLabel
            ? ModemPresetLabel.fromPacket(packet)
            : null;
        if (!preset) return '<span class="packet-chip packet-chip--unknown">--</span>';
        return `<span class="packet-chip packet-chip--${preset.tone}">${preset.chip}</span>`;
    }

    _openDetail(tr, packet) {
        if (!window.PacketDetailModal) return;

        if (this._onFocus) this._onFocus(packet.source_id);

        window.PacketDetailModal.show(packet, {
            formatNodeId: (id) => this._shortId(id),
            selectedRow: tr,
            onClose: () => {
                if (this._onFocus) this._onFocus(null);
            },
        });
    }

    _summarize(packet) {
        const p = packet.decoded_payload;
        if (!p) return '--';

        switch (packet.packet_type) {
            case 'text': return p.text || '--';
            case 'position': {
                const parts = [];
                if (p.latitude != null) parts.push(`${p.latitude.toFixed(4)}`);
                if (p.longitude != null) parts.push(`${p.longitude.toFixed(4)}`);
                if (p.altitude != null) parts.push(`alt ${p.altitude}m`);
                return parts.join(', ') || '--';
            }
            case 'nodeinfo':
                return [p.long_name, p.short_name, p.hw_model].filter(Boolean).join(' ') || (packet.protocol === 'meshcore' && p.advertisement ? 'Advertisement' : '--');
            case 'telemetry': {
                const parts = [];
                if (p.battery_level != null) {
                    // Credit: javastraat/meshpoint 29368c0
                    parts.push(p.battery_level === 101
                        ? 'batt=powered'
                        : `batt=${p.battery_level}%`);
                }
                if (p.voltage != null) parts.push(`${Number(p.voltage).toFixed(1)}V`);
                if (p.temperature != null) {
                    const t = window.MeshpointDisplayUnits
                        ? window.MeshpointDisplayUnits.formatTemperature(p.temperature)
                        : `${Number(p.temperature).toFixed(0)}°C`;
                    if (t) parts.push(t);
                }
                return parts.join(' ') || '--';
            }
            default: return '--';
        }
    }

    _rssiClass(val) {
        if (val == null) return '';
        const n = Number(val);
        if (!Number.isFinite(n) || n >= 0) return '';
        if (n >= -90) return 'rssi-good';
        if (n >= -110) return 'rssi-mid';
        return 'rssi-bad';
    }

    _displayRssi(rawRssi) {
        if (rawRssi == null) return null;
        const n = Number(rawRssi);
        if (!Number.isFinite(n) || n >= 0) return null;
        return n.toFixed(0);
    }

    _displaySnr(rawRssi, rawSnr) {
        if (rawSnr == null) return '--';
        const snr = Number(rawSnr);
        if (!Number.isFinite(snr)) return '--';
        const rssi = rawRssi == null ? null : Number(rawRssi);
        if (snr === 0 && (rssi == null || !Number.isFinite(rssi) || rssi >= 0)) {
            return '--';
        }
        return snr.toFixed(1);
    }

    _resolveRelay(relayByte) {
        const key = relayByte.toString(16).padStart(2, '0');
        const fullId = this._nodeByLastByte.get(key);
        return fullId ? this._shortId(fullId) : `!${key}`;
    }

    _shortId(id) {
        if (!id) return '--';
        if (id === 'ffffffff' || id === 'ffff') return 'BCAST';
        return id.length > 6 ? `!${id.slice(-4)}` : id;
    }

    _esc(str) {
        const el = document.createElement('span');
        el.textContent = str;
        return el.innerHTML;
    }
}
