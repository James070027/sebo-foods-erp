#!/usr/bin/env python3
"""
Sebo Foods ERP prototype  -  Materials Management (MM) + Financials (FI)
One integrated SQLite database. Run:  python app.py          (interactive menu)
                                      python app.py --demo   (scripted walkthrough)
Business events (receive goods, issue to production, pay supplier) each run in ONE
database transaction: stock, stock ledger and general ledger change together or not at all.
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SEBO_DB", os.path.join(HERE, "sebo_erp.db"))


# ------------------------------------------------------------------ setup
def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    """Create schema + seed data the first time only."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='materials'"
    ).fetchone()
    if exists:
        return
    for f in ("schema.sql", "seed.sql"):
        with open(os.path.join(HERE, f), encoding="utf-8") as fh:
            conn.executescript(fh.read())
    conn.commit()


class ERPError(Exception):
    """Business-rule violation shown to the user."""


def _account_id(conn, code):
    row = conn.execute("SELECT id FROM gl_accounts WHERE code=?", (code,)).fetchone()
    if not row:
        raise ERPError(f"GL account {code} missing")
    return row["id"]


# ------------------------------------------------- Financials: posting engine
def post_journal(conn, description, ref_type, ref_id, lines):
    """lines = [(account_code, debit, credit, supplier_id_or_None), ...]
    Enforces double-entry: total debits must equal total credits."""
    dr = round(sum(l[1] for l in lines), 2)
    cr = round(sum(l[2] for l in lines), 2)
    if dr != cr or dr <= 0:
        raise ERPError(f"Unbalanced journal (Dr {dr} / Cr {cr})")
    cur = conn.execute(
        "INSERT INTO journal_entries (description, ref_type, ref_id) VALUES (?,?,?)",
        (description, ref_type, ref_id),
    )
    entry_id = cur.lastrowid
    for code, debit, credit, supplier_id in lines:
        conn.execute(
            "INSERT INTO journal_lines (entry_id, account_id, supplier_id, debit, credit)"
            " VALUES (?,?,?,?,?)",
            (entry_id, _account_id(conn, code), supplier_id, round(debit, 2), round(credit, 2)),
        )
    return entry_id


# ------------------------------------------------------- CRUD: suppliers
def add_supplier(conn, name, phone="", email="", address=""):
    try:
        cur = conn.execute(
            "INSERT INTO suppliers (name, phone, email, address) VALUES (?,?,?,?)",
            (name.strip(), phone, email, address),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ERPError(f"Cannot add supplier: {e}")


def list_suppliers(conn):
    return conn.execute("SELECT * FROM suppliers ORDER BY id").fetchall()


def update_supplier(conn, supplier_id, **fields):
    allowed = {"name", "phone", "email", "address", "active"}
    fields = {k: v for k, v in fields.items() if k in allowed and v not in (None, "")}
    if not fields:
        raise ERPError("Nothing to update")
    sets = ", ".join(f"{k}=?" for k in fields)
    try:
        cur = conn.execute(f"UPDATE suppliers SET {sets} WHERE id=?", (*fields.values(), supplier_id))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ERPError(f"Cannot update supplier: {e}")
    if cur.rowcount == 0:
        raise ERPError("Supplier not found")


def delete_supplier(conn, supplier_id):
    try:
        cur = conn.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ERPError("Supplier has purchase orders / ledger history - deactivate instead "
                       "(update active=0).")
    if cur.rowcount == 0:
        raise ERPError("Supplier not found")


# -------------------------------------------------------- CRUD: materials
def add_material(conn, sku, name, unit, reorder_level=0):
    try:
        cur = conn.execute(
            "INSERT INTO materials (sku, name, unit, reorder_level) VALUES (?,?,?,?)",
            (sku.strip().upper(), name.strip(), unit.strip(), float(reorder_level)),
        )
        conn.commit()
        return cur.lastrowid
    except (sqlite3.IntegrityError, ValueError) as e:
        conn.rollback()
        raise ERPError(f"Cannot add material: {e}")


def list_materials(conn):
    return conn.execute("SELECT * FROM materials ORDER BY id").fetchall()


def update_material(conn, material_id, **fields):
    # qty_on_hand / avg_cost are deliberately NOT editable: only stock movements change them
    allowed = {"name", "unit", "reorder_level"}
    fields = {k: v for k, v in fields.items() if k in allowed and v not in (None, "")}
    if not fields:
        raise ERPError("Nothing to update")
    sets = ", ".join(f"{k}=?" for k in fields)
    try:
        cur = conn.execute(f"UPDATE materials SET {sets} WHERE id=?", (*fields.values(), material_id))
        conn.commit()
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ERPError(f"Cannot update material: {e}")
    if cur.rowcount == 0:
        raise ERPError("Material not found")


def delete_material(conn, material_id):
    try:
        cur = conn.execute("DELETE FROM materials WHERE id=?", (material_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ERPError("Material is used in purchase orders / stock history - cannot delete.")
    if cur.rowcount == 0:
        raise ERPError("Material not found")


# ----------------------------------------------------- MM: purchase orders
def create_po(conn, supplier_id, lines):
    """lines = [(material_id, qty, unit_cost), ...]"""
    if not lines:
        raise ERPError("A purchase order needs at least one line")
    try:
        sup = conn.execute("SELECT active FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        if not sup:
            raise ERPError("Supplier not found")
        if not sup["active"]:
            raise ERPError("Supplier is inactive")
        po_id = conn.execute(
            "INSERT INTO purchase_orders (supplier_id) VALUES (?)", (supplier_id,)
        ).lastrowid
        for material_id, qty, cost in lines:
            conn.execute(
                "INSERT INTO po_lines (po_id, material_id, qty_ordered, unit_cost) VALUES (?,?,?,?)",
                (po_id, material_id, qty, cost),
            )
        conn.commit()
        return po_id
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ERPError(f"Invalid purchase order: {e}")
    except ERPError:
        conn.rollback()
        raise


def list_pos(conn):
    return conn.execute(
        """SELECT p.id, s.name AS supplier, p.order_date, p.status,
                  ROUND(SUM(l.qty_ordered*l.unit_cost),2) AS order_value
             FROM purchase_orders p
             JOIN suppliers s ON s.id=p.supplier_id
             JOIN po_lines  l ON l.po_id=p.id
         GROUP BY p.id ORDER BY p.id"""
    ).fetchall()


def po_lines(conn, po_id):
    return conn.execute(
        """SELECT l.id, m.sku, m.name, l.qty_ordered, l.qty_received, l.unit_cost
             FROM po_lines l JOIN materials m ON m.id=l.material_id
            WHERE l.po_id=? ORDER BY l.id""", (po_id,)
    ).fetchall()


def cancel_po(conn, po_id):
    row = conn.execute("SELECT status FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
    if not row:
        raise ERPError("PO not found")
    if row["status"] != "OPEN":
        raise ERPError("Only OPEN purchase orders (nothing received) can be cancelled")
    conn.execute("UPDATE purchase_orders SET status='CANCELLED' WHERE id=?", (po_id,))
    conn.commit()


# ----------------------------- MM -> FI integration: goods receipt (GRN)
def receive_goods(conn, po_line_id, qty):
    """One transaction: stock ledger + stock level + avg cost + GL (Dr Inventory / Cr AP)."""
    try:
        line = conn.execute(
            """SELECT l.*, p.supplier_id, p.status, p.id AS po
                 FROM po_lines l JOIN purchase_orders p ON p.id=l.po_id WHERE l.id=?""",
            (po_line_id,),
        ).fetchone()
        if not line:
            raise ERPError("PO line not found")
        if line["status"] in ("CANCELLED", "RECEIVED"):
            raise ERPError(f"PO is {line['status']}")
        if qty <= 0:
            raise ERPError("Quantity must be positive")
        if qty > line["qty_ordered"] - line["qty_received"]:
            raise ERPError("Cannot receive more than the outstanding quantity")

        conn.execute("UPDATE po_lines SET qty_received = qty_received + ? WHERE id=?", (qty, po_line_id))
        conn.execute(
            "INSERT INTO stock_movements (material_id, movement_type, qty, unit_cost, ref_type, ref_id)"
            " VALUES (?, 'RECEIPT', ?, ?, 'PO_LINE', ?)",
            (line["material_id"], qty, line["unit_cost"], po_line_id),
        )  # trigger trg_stock_receipt updates materials
        value = qty * line["unit_cost"]
        post_journal(conn, f"Goods receipt PO#{line['po']}", "PO_LINE", po_line_id, [
            ("1200", value, 0, None),
            ("2100", 0, value, line["supplier_id"]),
        ]) if value > 0 else None

        open_lines = conn.execute(
            "SELECT COUNT(*) c FROM po_lines WHERE po_id=? AND qty_received < qty_ordered",
            (line["po"],)).fetchone()["c"]
        conn.execute("UPDATE purchase_orders SET status=? WHERE id=?",
                     ("PARTIAL" if open_lines else "RECEIVED", line["po"]))
        conn.commit()
        return round(value, 2)
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ERPError(f"Receipt rejected: {e}")
    except ERPError:
        conn.rollback()
        raise


# ------------------------- MM -> FI integration: issue to production
def issue_to_production(conn, material_id, qty, note="Production"):
    """Dr Raw Materials Consumed / Cr Inventory at the current moving-average cost."""
    try:
        m = conn.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        if not m:
            raise ERPError("Material not found")
        if qty <= 0:
            raise ERPError("Quantity must be positive")
        if qty > m["qty_on_hand"]:
            raise ERPError(f"Insufficient stock: {m['qty_on_hand']} {m['unit']} on hand")
        cost = m["avg_cost"]
        mv = conn.execute(
            "INSERT INTO stock_movements (material_id, movement_type, qty, unit_cost, ref_type, note)"
            " VALUES (?, 'ISSUE', ?, ?, 'PRODUCTION', ?)",
            (material_id, qty, cost, note),
        ).lastrowid
        value = qty * cost
        if value > 0:
            post_journal(conn, f"Issue {m['sku']} to production", "STOCK_MOVEMENT", mv, [
                ("5000", value, 0, None),
                ("1200", 0, value, None),
            ])
        conn.commit()
        return round(value, 2)
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ERPError(f"Issue rejected: {e}")
    except ERPError:
        conn.rollback()
        raise


# --------------------------------------------- FI: pay supplier (AP clearing)
def pay_supplier(conn, supplier_id, amount):
    try:
        owed = conn.execute("SELECT amount_owed FROM v_ap_balance WHERE supplier_id=?",
                            (supplier_id,)).fetchone()
        if not owed:
            raise ERPError("Supplier not found")
        if amount <= 0:
            raise ERPError("Amount must be positive")
        if round(amount, 2) > owed["amount_owed"]:
            raise ERPError(f"Payment exceeds amount owed ({owed['amount_owed']:,.2f})")
        cash = conn.execute("SELECT balance FROM v_trial_balance WHERE code='1000'").fetchone()["balance"]
        if amount > cash:
            raise ERPError(f"Insufficient cash ({cash:,.2f})")
        post_journal(conn, "Supplier payment", "PAYMENT", supplier_id, [
            ("2100", amount, 0, supplier_id),
            ("1000", 0, amount, None),
        ])
        conn.commit()
    except ERPError:
        conn.rollback()
        raise


# ------------------------------------------------------------------ reports
def trial_balance(conn):
    return conn.execute("SELECT * FROM v_trial_balance ORDER BY code").fetchall()


def ap_report(conn):
    return conn.execute("SELECT * FROM v_ap_balance ORDER BY name").fetchall()


def reorder_report(conn):
    return conn.execute("SELECT * FROM v_reorder").fetchall()


def stock_ledger(conn, material_id):
    return conn.execute(
        "SELECT moved_at, movement_type, qty, unit_cost, ref_type, ref_id, note "
        "FROM stock_movements WHERE material_id=? ORDER BY id", (material_id,)).fetchall()


def integrity_check(conn):
    unbalanced = conn.execute("SELECT * FROM v_unbalanced_entries").fetchall()
    rec = conn.execute("SELECT * FROM v_inventory_vs_gl").fetchone()
    return len(unbalanced) == 0, abs(rec["stock_value"] - rec["gl_inventory"]) < 1, dict(rec)


# ----------------------------------------------------------------- CLI helpers
def show(rows, cols=None):
    rows = [dict(r) for r in rows]
    if not rows:
        print("  (none)")
        return
    cols = cols or list(rows[0].keys())
    widths = {c: max(len(c), *(len(f"{r[c]:,.2f}" if isinstance(r[c], float) else str(r[c])) for r in rows))
              for c in cols}
    print("  " + " | ".join(c.ljust(widths[c]) for c in cols))
    print("  " + "-+-".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  " + " | ".join(
            (f"{r[c]:,.2f}" if isinstance(r[c], float) else str(r[c])).ljust(widths[c]) for c in cols))


def ask(prompt, cast=str, default=None):
    while True:
        raw = input(f"  {prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
        if raw == "" and default is not None:
            return default
        try:
            return cast(raw)
        except ValueError:
            print("  Invalid input, try again.")


MENU = """
================ SEBO FOODS ERP (MM + FI) ================
 SUPPLIERS                   MATERIALS
  1 List   2 Add               5 List   6 Add
  3 Update 4 Delete            7 Update 8 Delete
 PURCHASING / STOCK          FINANCIALS & REPORTS
  9  Create purchase order     14 Pay supplier
  10 List POs / view lines     15 Trial balance
  11 Receive goods (GRN)       16 Supplier balances (AP)
  12 Issue to production       17 Reorder alerts
  13 Cancel PO                 18 Stock ledger  19 Integrity check
  0 Exit
==========================================================="""


def cli():
    conn = connect()
    init_db(conn)
    while True:
        print(MENU)
        choice = input("Choose: ").strip()
        try:
            if choice == "0":
                break
            elif choice == "1":
                show(list_suppliers(conn))
            elif choice == "2":
                print("  New supplier id:", add_supplier(conn, ask("Name"), ask("Phone", default=""),
                                                         ask("Email", default=""), ask("Address", default="")))
            elif choice == "3":
                update_supplier(conn, ask("Supplier id", int), name=ask("New name (blank=keep)", default=""),
                                phone=ask("New phone (blank=keep)", default=""),
                                active=ask("Active 1/0 (blank=keep)", default=""))
                print("  Updated.")
            elif choice == "4":
                delete_supplier(conn, ask("Supplier id", int)); print("  Deleted.")
            elif choice == "5":
                show(list_materials(conn))
            elif choice == "6":
                print("  New material id:", add_material(conn, ask("SKU"), ask("Name"), ask("Unit"),
                                                        ask("Reorder level", float, 0)))
            elif choice == "7":
                update_material(conn, ask("Material id", int), name=ask("New name (blank=keep)", default=""),
                                unit=ask("New unit (blank=keep)", default=""),
                                reorder_level=ask("New reorder level (blank=keep)", default=""))
                print("  Updated.")
            elif choice == "8":
                delete_material(conn, ask("Material id", int)); print("  Deleted.")
            elif choice == "9":
                sid = ask("Supplier id", int); lines = []
                while True:
                    lines.append((ask("Material id", int), ask("Quantity", float), ask("Unit cost (UGX)", float)))
                    if ask("Add another line? y/n", str, "n").lower() != "y":
                        break
                print("  Created PO", create_po(conn, sid, lines))
            elif choice == "10":
                show(list_pos(conn))
                pid = ask("PO id to view lines (0=skip)", int, 0)
                if pid:
                    show(po_lines(conn, pid))
            elif choice == "11":
                pid = ask("PO id", int); show(po_lines(conn, pid))
                v = receive_goods(conn, ask("PO line id", int), ask("Quantity received", float))
                print(f"  Received. Inventory +{v:,.2f}, Accounts Payable +{v:,.2f}")
            elif choice == "12":
                show(list_materials(conn), ["id", "sku", "name", "qty_on_hand", "avg_cost"])
                v = issue_to_production(conn, ask("Material id", int), ask("Quantity", float))
                print(f"  Issued. Expense recognised: {v:,.2f}")
            elif choice == "13":
                cancel_po(conn, ask("PO id", int)); print("  Cancelled.")
            elif choice == "14":
                show(ap_report(conn))
                pay_supplier(conn, ask("Supplier id", int), ask("Amount", float)); print("  Paid.")
            elif choice == "15":
                show(trial_balance(conn))
            elif choice == "16":
                show(ap_report(conn))
            elif choice == "17":
                show(reorder_report(conn))
            elif choice == "18":
                show(stock_ledger(conn, ask("Material id", int)))
            elif choice == "19":
                ok1, ok2, rec = integrity_check(conn)
                print("  All journals balanced:", ok1)
                print("  Stock value matches GL inventory:", ok2, rec)
            else:
                print("  Unknown option.")
        except ERPError as e:
            print("  ! Business rule:", e)
        except (EOFError, KeyboardInterrupt):
            break
    conn.close()


def demo():
    """Scripted walkthrough - proves MM and FI share one database."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = connect(); init_db(conn)
    print("1) Create PO: 1000 kg maize @ 2,500 + 400 kg sugar @ 4,000 from supplier 1")
    po = create_po(conn, 1, [(1, 1000, 2500), (2, 400, 4000)])
    print("2) Receive goods in full  ->  stock up, Inventory Dr, AP Cr")
    for l in po_lines(conn, po):
        receive_goods(conn, l["id"], l["qty_ordered"])
    show(list_materials(conn), ["sku", "qty_on_hand", "avg_cost"])
    print("3) Issue 300 kg maize to production -> stock down, COGS Dr, Inventory Cr")
    issue_to_production(conn, 1, 300)
    print("4) Try to issue 5,000 kg (more than stock) -> must be rejected")
    try:
        issue_to_production(conn, 1, 5000)
    except ERPError as e:
        print("   rejected:", e)
    print("5) Pay supplier 2,000,000")
    pay_supplier(conn, 1, 2_000_000)
    print("\nTrial balance:"); show(trial_balance(conn))
    print("\nSupplier balances:"); show(ap_report(conn))
    print("\nIntegrity (balanced, stock==GL):", integrity_check(conn)[:2])
    conn.close()


if __name__ == "__main__":
    demo() if "--demo" in sys.argv else cli()
