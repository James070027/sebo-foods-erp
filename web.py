#!/usr/bin/env python3
"""
Sebo Foods ERP - web dashboard (Python standard library only).
Run:  python web.py        then open  http://127.0.0.1:8000
It uses the SAME database and business rules as the CLI (app.py): no logic is duplicated.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import app

PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "127.0.0.1")


def rows(cur):
    return [dict(r) for r in cur]


# ------------------------------------------------------------- read endpoints
def dashboard(conn, q):
    stock_value = conn.execute("SELECT ROUND(COALESCE(SUM(qty_on_hand*avg_cost),0),2) v FROM materials").fetchone()["v"]
    tb = {r["code"]: r["balance"] for r in app.trial_balance(conn)}
    ap_total = conn.execute("SELECT ROUND(COALESCE(SUM(amount_owed),0),2) v FROM v_ap_balance").fetchone()["v"]
    open_pos = conn.execute("SELECT COUNT(*) c FROM purchase_orders WHERE status IN ('OPEN','PARTIAL')").fetchone()["c"]
    balanced, reconciled, rec = app.integrity_check(conn)
    return {
        "stock_value": stock_value, "cash": tb.get("1000", 0), "ap_total": ap_total,
        "consumed": tb.get("5000", 0), "open_pos": open_pos,
        "reorder": rows(app.reorder_report(conn)),
        "balanced": balanced, "reconciled": reconciled, "reconciliation": rec,
    }


def journal(conn, q):
    return rows(conn.execute(
        """SELECT e.id, e.entry_date, e.description, a.code, a.name AS account, l.debit, l.credit
             FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id
             JOIN gl_accounts a ON a.id=l.account_id
         ORDER BY e.id DESC, l.id LIMIT 60"""))


GET = {
    "dashboard": dashboard,
    "suppliers": lambda c, q: rows(app.list_suppliers(c)),
    "materials": lambda c, q: rows(app.list_materials(c)),
    "pos": lambda c, q: rows(app.list_pos(c)),
    "po_lines": lambda c, q: rows(app.po_lines(c, int(q["po_id"][0]))),
    "trial_balance": lambda c, q: rows(app.trial_balance(c)),
    "ap": lambda c, q: rows(app.ap_report(c)),
    "ledger": lambda c, q: rows(app.stock_ledger(c, int(q["material_id"][0]))),
    "journal": journal,
}


# ------------------------------------------------------------ write endpoints
def _f(d, k):
    try:
        return float(d[k])
    except (KeyError, TypeError, ValueError):
        raise app.ERPError(f"'{k}' must be a number")


def _i(d, k):
    try:
        return int(d[k])
    except (KeyError, TypeError, ValueError):
        raise app.ERPError(f"'{k}' is required")


def _po_create(c, d):
    lines = [(int(l["material_id"]), float(l["qty"]), float(l["unit_cost"])) for l in d.get("lines", [])]
    return {"id": app.create_po(c, _i(d, "supplier_id"), lines)}


POST = {
    "supplier/add": lambda c, d: {"id": app.add_supplier(c, d.get("name", ""), d.get("phone", ""), d.get("email", ""), d.get("address", ""))},
    "supplier/update": lambda c, d: app.update_supplier(c, _i(d, "id"), name=d.get("name"), phone=d.get("phone"),
                                                         email=d.get("email"), address=d.get("address"), active=d.get("active")),
    "supplier/delete": lambda c, d: app.delete_supplier(c, _i(d, "id")),
    "material/add": lambda c, d: {"id": app.add_material(c, d.get("sku", ""), d.get("name", ""), d.get("unit", ""), d.get("reorder_level") or 0)},
    "material/update": lambda c, d: app.update_material(c, _i(d, "id"), name=d.get("name"), unit=d.get("unit"),
                                                         reorder_level=d.get("reorder_level")),
    "material/delete": lambda c, d: app.delete_material(c, _i(d, "id")),
    "po/create": _po_create,
    "po/cancel": lambda c, d: app.cancel_po(c, _i(d, "id")),
    "receive": lambda c, d: {"value": app.receive_goods(c, _i(d, "po_line_id"), _f(d, "qty"))},
    "issue": lambda c, d: {"value": app.issue_to_production(c, _i(d, "material_id"), _f(d, "qty"), d.get("note") or "Production")},
    "pay": lambda c, d: app.pay_supplier(c, _i(d, "supplier_id"), _f(d, "amount")),
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj if obj is not None else {"ok": True}))

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html")
        if url.path.startswith("/api/"):
            name = url.path[5:]
            conn = app.connect()
            try:
                if name not in GET:
                    return self._json(404, {"error": "Unknown endpoint"})
                return self._json(200, GET[name](conn, parse_qs(url.query)))
            except (KeyError, ValueError):
                return self._json(400, {"error": "Bad request"})
            finally:
                conn.close()
        self._json(404, {"error": "Not found"})

    def do_POST(self):
        name = urlparse(self.path).path[5:]
        if not self.path.startswith("/api/") or name not in POST:
            return self._json(404, {"error": "Unknown endpoint"})
        conn = app.connect()
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            return self._json(200, POST[name](conn, data))
        except app.ERPError as e:
            self._json(400, {"error": str(e)})
        except (ValueError, KeyError, TypeError):
            self._json(400, {"error": "Invalid input"})
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        pass


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sebo Foods ERP</title>
<style>
:root{--bg:#f4f6f8;--card:#fff;--ink:#1c2733;--mut:#6b7885;--line:#dfe4ea;--acc:#1f7a4d;--acc2:#e8f4ee;--bad:#b3261e}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{background:var(--acc);color:#fff;padding:12px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:18px;margin:0}header small{opacity:.85}
nav{display:flex;gap:4px;flex-wrap:wrap;background:#fff;border-bottom:1px solid var(--line);padding:0 12px}
nav button{border:0;background:none;padding:12px 14px;cursor:pointer;font-size:14px;color:var(--mut);border-bottom:3px solid transparent}
nav button.on{color:var(--acc);border-color:var(--acc);font-weight:600}
main{max-width:1100px;margin:18px auto;padding:0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:16px}
.kpi .v{font-size:22px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
h2{font-size:16px;margin:0 0 10px}table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}th{color:var(--mut);font-size:12px;text-transform:uppercase}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.tbl{overflow-x:auto}
form{display:flex;gap:8px;flex-wrap:wrap;align-items:end}
label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:3px}
input,select{padding:7px 9px;border:1px solid var(--line);border-radius:6px;font:inherit;min-width:120px}
button.b{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer;font:inherit}
button.s{background:#fff;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 9px;cursor:pointer;font:inherit}
button.d{color:var(--bad)}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;background:var(--acc2);color:var(--acc)}
.pill.bad{background:#fdeceb;color:var(--bad)}
#toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);padding:10px 16px;border-radius:8px;color:#fff;display:none;max-width:90%;z-index:9}
.ok{background:var(--acc)}.err{background:var(--bad)}
.mut{color:var(--mut)}.flow{background:var(--acc2);padding:8px 12px;border-radius:8px;margin-bottom:12px;font-size:13px}
.hide{display:none}
</style></head><body>
<header><h1>Sebo Foods ERP</h1><small>Materials Management + Financials &middot; one integrated database</small></header>
<nav id="nav"></nav>
<main id="main"></main><div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>Number(v).toLocaleString(undefined,{maximumFractionDigits:2});
async function api(path,body){
  const r=await fetch('/api/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json(); if(!r.ok) throw new Error(j.error||'Request failed'); return j;
}
function toast(msg,ok=true){const t=$('#toast');t.textContent=msg;t.className=ok?'ok':'err';t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',3500);}
async function act(path,body,msg,reload){try{const r=await api(path,body);toast(typeof msg==='function'?msg(r):msg);await reload();}catch(e){toast(e.message,false);}}
function table(cols,data,actions){ // cols: [key,label,numeric?]
  if(!data.length) return '<p class="mut">No records.</p>';
  return '<div class="tbl"><table><tr>'+cols.map(c=>`<th class="${c[2]?'n':''}">${esc(c[1])}</th>`).join('')+(actions?'<th></th>':'')+'</tr>'+
   data.map((r,i)=>'<tr>'+cols.map(c=>`<td class="${c[2]?'n':''}">${c[2]&&r[c[0]]!==null?fmt(r[c[0]]):esc(r[c[0]])}</td>`).join('')+(actions?`<td>${actions(r,i)}</td>`:'')+'</tr>').join('')+'</table></div>';
}
const tabs={dashboard:'Dashboard',suppliers:'Suppliers',materials:'Materials',purchasing:'Purchasing',issue:'Production issue',finance:'Financials'};
function nav(cur){$('#nav').innerHTML=Object.entries(tabs).map(([k,v])=>`<button class="${k===cur?'on':''}" onclick="go('${k}')">${v}</button>`).join('');}
async function go(t){nav(t);$('#main').innerHTML='<p class="mut">Loading...</p>';await views[t]();}
const views={};

views.dashboard=async()=>{
  const d=await api('dashboard');
  $('#main').innerHTML=`<div class="grid">
   ${[['Inventory value (UGX)',d.stock_value],['Cash & bank (UGX)',d.cash],['Owed to suppliers (UGX)',d.ap_total],['Materials consumed (UGX)',d.consumed],['Open purchase orders',d.open_pos]]
     .map(k=>`<div class="card kpi"><div class="l">${k[0]}</div><div class="v">${fmt(k[1])}</div></div>`).join('')}</div>
   <div class="card"><h2>Data integrity (single source of truth)</h2>
     <p>All journal entries balanced: <span class="pill ${d.balanced?'':'bad'}">${d.balanced?'Yes':'NO'}</span>
     &nbsp; Stock value equals GL inventory: <span class="pill ${d.reconciled?'':'bad'}">${d.reconciled?'Yes':'NO'}</span></p>
     <p class="mut">Stock records ${fmt(d.reconciliation.stock_value)} &middot; General ledger ${fmt(d.reconciliation.gl_inventory)}</p></div>
   <div class="card"><h2>Reorder alerts</h2>${table([['sku','SKU'],['name','Material'],['qty_on_hand','On hand',1],['reorder_level','Reorder level',1],['unit','Unit']],d.reorder)}</div>
   <div class="card"><h2>Recent journal postings</h2><div id="jr"></div></div>`;
  const j=await api('journal');$('#jr').innerHTML=table([['id','Entry #'],['entry_date','Date'],['description','Description'],['code','Acct'],['account','Account'],['debit','Debit',1],['credit','Credit',1]],j);
};

views.suppliers=async()=>{
  const s=await api('suppliers');
  $('#main').innerHTML=`<div class="card"><h2>Add supplier</h2><form id="f">
    <label>Name<input name="name" required></label><label>Phone<input name="phone"></label>
    <label>Email<input name="email"></label><label>Address<input name="address"></label><button class="b">Add</button></form></div>
   <div class="card"><h2>Suppliers</h2>${table([['id','ID'],['name','Name'],['phone','Phone'],['email','Email'],['address','Address'],['active','Active']],s,
     r=>`<button class="s" onclick="editSup(${r.id})">Edit</button> <button class="s" onclick="toggleSup(${r.id},${r.active?0:1})">${r.active?'Deactivate':'Activate'}</button> <button class="s d" onclick="delSup(${r.id})">Delete</button>`)}</div>`;
  window._sup=s;
  $('#f').onsubmit=e=>{e.preventDefault();act('supplier/add',Object.fromEntries(new FormData(e.target)),'Supplier added',views.suppliers);};
};
window.editSup=id=>{const r=_sup.find(x=>x.id===id);const name=prompt('Name',r.name);if(name===null)return;const phone=prompt('Phone',r.phone||'');if(phone===null)return;
  act('supplier/update',{id,name,phone},'Supplier updated',views.suppliers);};
window.toggleSup=(id,active)=>act('supplier/update',{id,active},'Status changed',views.suppliers);
window.delSup=id=>confirm('Delete this supplier?')&&act('supplier/delete',{id},'Supplier deleted',views.suppliers);

views.materials=async()=>{
  const m=await api('materials');window._mat=m;
  $('#main').innerHTML=`<div class="card"><h2>Add material</h2><form id="f">
    <label>SKU<input name="sku" required></label><label>Name<input name="name" required></label>
    <label>Unit<input name="unit" required placeholder="kg"></label><label>Reorder level<input name="reorder_level" type="number" step="any" value="0"></label><button class="b">Add</button></form></div>
   <div class="card"><h2>Materials</h2><p class="mut">Quantity and cost can only change through goods receipts and issues, never by editing.</p>
   ${table([['id','ID'],['sku','SKU'],['name','Name'],['unit','Unit'],['qty_on_hand','On hand',1],['avg_cost','Avg cost',1],['reorder_level','Reorder level',1]],m,
     r=>`<button class="s" onclick="editMat(${r.id})">Edit</button> <button class="s" onclick="ledger(${r.id})">Ledger</button> <button class="s d" onclick="delMat(${r.id})">Delete</button>`)}</div><div id="led"></div>`;
  $('#f').onsubmit=e=>{e.preventDefault();act('material/add',Object.fromEntries(new FormData(e.target)),'Material added',views.materials);};
};
window.editMat=id=>{const r=_mat.find(x=>x.id===id);const name=prompt('Name',r.name);if(name===null)return;const rl=prompt('Reorder level',r.reorder_level);if(rl===null)return;
  act('material/update',{id,name,reorder_level:rl},'Material updated',views.materials);};
window.delMat=id=>confirm('Delete this material?')&&act('material/delete',{id},'Material deleted',views.materials);
window.ledger=async id=>{const l=await api('ledger?material_id='+id);$('#led').innerHTML='<div class="card"><h2>Stock ledger</h2>'+
  table([['moved_at','When'],['movement_type','Type'],['qty','Qty',1],['unit_cost','Unit cost',1],['ref_type','Ref'],['note','Note']],l)+'</div>';$('#led').scrollIntoView();};

views.purchasing=async()=>{
  const [s,m,p]=await Promise.all([api('suppliers'),api('materials'),api('pos')]);
  const mo=m.map(x=>`<option value="${x.id}">${esc(x.sku)} - ${esc(x.name)}</option>`).join('');
  $('#main').innerHTML=`<div class="flow">Receiving goods updates stock, the stock ledger and the general ledger (Dr Inventory / Cr Accounts Payable) in one transaction.</div>
   <div class="card"><h2>New purchase order</h2><form id="f">
    <label>Supplier<select name="supplier_id">${s.filter(x=>x.active).map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></label>
    <label>Material<select name="material_id">${mo}</select></label>
    <label>Quantity<input name="qty" type="number" step="any" min="0" required></label>
    <label>Unit cost (UGX)<input name="unit_cost" type="number" step="any" min="0" required></label><button class="b">Create PO</button></form>
    <p class="mut">One material per PO in this screen (the database supports several lines per PO).</p></div>
   <div class="card"><h2>Purchase orders</h2>${table([['id','PO #'],['supplier','Supplier'],['order_date','Date'],['status','Status'],['order_value','Value',1]],p,
     r=>`<button class="s" onclick="viewPO(${r.id})">Lines / receive</button> ${r.status==='OPEN'?`<button class="s d" onclick="cancelPO(${r.id})">Cancel</button>`:''}`)}</div><div id="pol"></div>`;
  $('#f').onsubmit=e=>{e.preventDefault();const d=Object.fromEntries(new FormData(e.target));
    act('po/create',{supplier_id:d.supplier_id,lines:[{material_id:d.material_id,qty:d.qty,unit_cost:d.unit_cost}]},r=>'Created PO #'+r.id,views.purchasing);};
};
window.viewPO=async id=>{const l=await api('po_lines?po_id='+id);$('#pol').innerHTML=`<div class="card"><h2>PO #${id} lines</h2>`+
  table([['sku','SKU'],['name','Material'],['qty_ordered','Ordered',1],['qty_received','Received',1],['unit_cost','Unit cost',1]],l,
   r=>r.qty_received<r.qty_ordered?`<button class="s" onclick="recv(${r.id},${r.qty_ordered-r.qty_received},${id})">Receive goods</button>`:'<span class="pill">Complete</span>')+'</div>';$('#pol').scrollIntoView();};
window.recv=(lineId,max,poId)=>{const q=prompt('Quantity received (max '+max+')',max);if(q===null)return;
  act('receive',{po_line_id:lineId,qty:q},r=>'Received. Inventory and payables +'+fmt(r.value),async()=>{await views.purchasing();viewPO(poId);});};
window.cancelPO=id=>confirm('Cancel PO #'+id+'?')&&act('po/cancel',{id},'PO cancelled',views.purchasing);

views.issue=async()=>{
  const m=await api('materials');
  $('#main').innerHTML=`<div class="flow">Issuing to production reduces stock (blocked if insufficient) and posts Dr Raw Materials Consumed / Cr Inventory at average cost.</div>
   <div class="card"><h2>Issue material to production</h2><form id="f">
    <label>Material<select name="material_id">${m.map(x=>`<option value="${x.id}">${esc(x.sku)} - ${esc(x.name)} (${fmt(x.qty_on_hand)} ${esc(x.unit)})</option>`).join('')}</select></label>
    <label>Quantity<input name="qty" type="number" step="any" min="0" required></label>
    <label>Note<input name="note" placeholder="e.g. Batch 12 maize meal"></label><button class="b">Issue</button></form></div>
   <div class="card"><h2>Current stock</h2>${table([['sku','SKU'],['name','Material'],['qty_on_hand','On hand',1],['unit','Unit'],['avg_cost','Avg cost',1]],m)}</div>`;
  $('#f').onsubmit=e=>{e.preventDefault();act('issue',Object.fromEntries(new FormData(e.target)),r=>'Issued. Expense recognised: '+fmt(r.value),views.issue);};
};

views.finance=async()=>{
  const [tb,ap]=await Promise.all([api('trial_balance'),api('ap')]);
  $('#main').innerHTML=`<div class="card"><h2>Pay supplier</h2><form id="f">
    <label>Supplier<select name="supplier_id">${ap.map(x=>`<option value="${x.supplier_id}">${esc(x.name)} (owed ${fmt(x.amount_owed)})</option>`).join('')}</select></label>
    <label>Amount (UGX)<input name="amount" type="number" step="any" min="0" required></label><button class="b">Pay</button></form></div>
   <div class="card"><h2>Trial balance</h2>${table([['code','Code'],['name','Account'],['type','Type'],['total_debit','Debit',1],['total_credit','Credit',1],['balance','Balance (Dr-Cr)',1]],tb)}</div>
   <div class="card"><h2>Supplier balances (accounts payable)</h2>${table([['name','Supplier'],['amount_owed','Amount owed (UGX)',1]],ap)}</div>`;
  $('#f').onsubmit=e=>{e.preventDefault();act('pay',Object.fromEntries(new FormData(e.target)),'Payment posted',views.finance);};
};
go('dashboard');
</script></body></html>"""


def main():
    conn = app.connect()
    app.init_db(conn)
    conn.close()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Sebo Foods ERP dashboard running at http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
