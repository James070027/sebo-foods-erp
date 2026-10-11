-- =====================================================================
-- Sebo Foods ERP prototype: ONE integrated database
-- Modules: Materials Management (MM) + Financials (FI)
-- Engine : SQLite (foreign keys must be ON for each connection)
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------------------------- MASTER DATA ----------------------------
CREATE TABLE suppliers (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,               -- prevents duplicate suppliers
    phone     TEXT,
    email     TEXT,
    address   TEXT,
    active    INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE materials (                          -- raw materials & packaging
    id            INTEGER PRIMARY KEY,
    sku           TEXT NOT NULL UNIQUE,           -- one code per item
    name          TEXT NOT NULL,
    unit          TEXT NOT NULL,                  -- kg, litre, pcs ...
    reorder_level REAL NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
    qty_on_hand   REAL NOT NULL DEFAULT 0 CHECK (qty_on_hand >= 0),  -- no negative stock
    avg_cost      REAL NOT NULL DEFAULT 0 CHECK (avg_cost >= 0)      -- moving average cost
);

CREATE TABLE gl_accounts (                        -- chart of accounts (Financials)
    id    INTEGER PRIMARY KEY,
    code  TEXT NOT NULL UNIQUE,
    name  TEXT NOT NULL,
    type  TEXT NOT NULL CHECK (type IN ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE'))
);

-- ------------------------ MATERIALS MANAGEMENT -----------------------
CREATE TABLE purchase_orders (
    id          INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    order_date  TEXT NOT NULL DEFAULT (date('now')),
    status      TEXT NOT NULL DEFAULT 'OPEN'
                CHECK (status IN ('OPEN','PARTIAL','RECEIVED','CANCELLED'))
);

CREATE TABLE po_lines (
    id           INTEGER PRIMARY KEY,
    po_id        INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    material_id  INTEGER NOT NULL REFERENCES materials(id) ON DELETE RESTRICT,
    qty_ordered  REAL NOT NULL CHECK (qty_ordered > 0),
    unit_cost    REAL NOT NULL CHECK (unit_cost >= 0),
    qty_received REAL NOT NULL DEFAULT 0,
    CHECK (qty_received >= 0 AND qty_received <= qty_ordered),   -- no over-receipt
    UNIQUE (po_id, material_id)
);

CREATE TABLE stock_movements (                    -- append-only stock ledger
    id            INTEGER PRIMARY KEY,
    material_id   INTEGER NOT NULL REFERENCES materials(id) ON DELETE RESTRICT,
    movement_type TEXT NOT NULL CHECK (movement_type IN ('RECEIPT','ISSUE')),
    qty           REAL NOT NULL CHECK (qty > 0),
    unit_cost     REAL NOT NULL CHECK (unit_cost >= 0),
    ref_type      TEXT,                           -- 'PO_LINE' / 'PRODUCTION'
    ref_id        INTEGER,
    note          TEXT,
    moved_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- --------------------------- FINANCIALS ------------------------------
CREATE TABLE journal_entries (
    id          INTEGER PRIMARY KEY,
    entry_date  TEXT NOT NULL DEFAULT (date('now')),
    description TEXT NOT NULL,
    ref_type    TEXT,                             -- links back to the MM document
    ref_id      INTEGER
);

CREATE TABLE journal_lines (
    id          INTEGER PRIMARY KEY,
    entry_id    INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account_id  INTEGER NOT NULL REFERENCES gl_accounts(id) ON DELETE RESTRICT,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE RESTRICT,  -- AP sub-ledger key
    debit       REAL NOT NULL DEFAULT 0 CHECK (debit  >= 0),
    credit      REAL NOT NULL DEFAULT 0 CHECK (credit >= 0),
    CHECK ((debit > 0) <> (credit > 0))           -- exactly one side per line
);

-- ----------------- INTEGRITY TRIGGERS (enforced in the DB) ----------
-- 1. A goods receipt updates stock quantity AND moving-average cost.
CREATE TRIGGER trg_stock_receipt
AFTER INSERT ON stock_movements
WHEN NEW.movement_type = 'RECEIPT'
BEGIN
    UPDATE materials
       SET avg_cost    = ((qty_on_hand * avg_cost) + (NEW.qty * NEW.unit_cost))
                         / (qty_on_hand + NEW.qty),
           qty_on_hand = qty_on_hand + NEW.qty
     WHERE id = NEW.material_id;
END;

-- 2. An issue reduces stock; CHECK(qty_on_hand >= 0) aborts if stock is short.
CREATE TRIGGER trg_stock_issue
AFTER INSERT ON stock_movements
WHEN NEW.movement_type = 'ISSUE'
BEGIN
    UPDATE materials
       SET qty_on_hand = qty_on_hand - NEW.qty
     WHERE id = NEW.material_id;
END;

-- 3. Audit trail: ledgers are append-only (corrections = reversing entries).
CREATE TRIGGER trg_no_update_stock BEFORE UPDATE ON stock_movements
BEGIN SELECT RAISE(ABORT, 'stock_movements is append-only'); END;
CREATE TRIGGER trg_no_delete_stock BEFORE DELETE ON stock_movements
BEGIN SELECT RAISE(ABORT, 'stock_movements is append-only'); END;
CREATE TRIGGER trg_no_update_jl BEFORE UPDATE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'journal_lines is append-only'); END;
CREATE TRIGGER trg_no_delete_jl BEFORE DELETE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'journal_lines is append-only'); END;

-- --------------------------- REPORT VIEWS ----------------------------
-- No stored balances: everything is derived from the ledgers (single source of truth).
CREATE VIEW v_trial_balance AS
SELECT a.code, a.name, a.type,
       ROUND(COALESCE(SUM(l.debit),0),2)  AS total_debit,
       ROUND(COALESCE(SUM(l.credit),0),2) AS total_credit,
       ROUND(COALESCE(SUM(l.debit),0) - COALESCE(SUM(l.credit),0),2) AS balance
  FROM gl_accounts a
  LEFT JOIN journal_lines l ON l.account_id = a.id
 GROUP BY a.id;

CREATE VIEW v_ap_balance AS                       -- what we owe each supplier
SELECT s.id AS supplier_id, s.name,
       ROUND(COALESCE(SUM(l.credit - l.debit),0),2) AS amount_owed
  FROM suppliers s
  LEFT JOIN journal_lines l ON l.supplier_id = s.id
 GROUP BY s.id;

CREATE VIEW v_reorder AS
SELECT sku, name, unit, qty_on_hand, reorder_level
  FROM materials WHERE qty_on_hand <= reorder_level;

CREATE VIEW v_unbalanced_entries AS               -- audit: should always be empty
SELECT entry_id, SUM(debit) AS dr, SUM(credit) AS cr
  FROM journal_lines GROUP BY entry_id
HAVING ROUND(SUM(debit),2) <> ROUND(SUM(credit),2);

CREATE VIEW v_inventory_vs_gl AS                  -- reconciliation: stock value vs GL
SELECT ROUND((SELECT COALESCE(SUM(qty_on_hand*avg_cost),0) FROM materials),2) AS stock_value,
       ROUND((SELECT COALESCE(SUM(balance),0) FROM v_trial_balance WHERE code='1200'),2) AS gl_inventory;
