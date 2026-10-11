"""Run:  python test_erp.py   - verifies integration and integrity rules."""
import os
import sqlite3
import tempfile
import unittest

import app


class ERPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = app.connect(self.tmp.name)
        app.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def _receive(self, qty=1000, cost=2500):
        po = app.create_po(self.conn, 1, [(1, qty, cost)])
        line = app.po_lines(self.conn, po)[0]["id"]
        app.receive_goods(self.conn, line, qty)
        return po, line

    def test_receipt_updates_stock_and_ledger(self):
        self._receive()
        m = self.conn.execute("SELECT * FROM materials WHERE id=1").fetchone()
        self.assertEqual(m["qty_on_hand"], 1000)
        self.assertEqual(m["avg_cost"], 2500)
        tb = {r["code"]: r["balance"] for r in app.trial_balance(self.conn)}
        self.assertEqual(tb["1200"], 2_500_000)       # inventory asset
        self.assertEqual(tb["2100"], -2_500_000)      # payable (credit)

    def test_moving_average_cost(self):
        self._receive(1000, 2500)
        po = app.create_po(self.conn, 1, [(1, 1000, 3500)])
        app.receive_goods(self.conn, app.po_lines(self.conn, po)[0]["id"], 1000)
        m = self.conn.execute("SELECT avg_cost FROM materials WHERE id=1").fetchone()
        self.assertEqual(m["avg_cost"], 3000)

    def test_issue_posts_expense_and_blocks_negative_stock(self):
        self._receive()
        app.issue_to_production(self.conn, 1, 400)
        tb = {r["code"]: r["balance"] for r in app.trial_balance(self.conn)}
        self.assertEqual(tb["5000"], 1_000_000)
        with self.assertRaises(app.ERPError):
            app.issue_to_production(self.conn, 1, 5000)

    def test_db_itself_blocks_negative_stock(self):          # bypass the app entirely
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO stock_movements (material_id, movement_type, qty, unit_cost)"
                              " VALUES (1,'ISSUE',10,0)")

    def test_no_over_receipt(self):
        _, line = self._receive(100, 10)
        with self.assertRaises(app.ERPError):
            app.receive_goods(self.conn, line, 1)

    def test_unbalanced_journal_rejected(self):
        with self.assertRaises(app.ERPError):
            app.post_journal(self.conn, "bad", None, None, [("1000", 100, 0, None), ("3000", 0, 90, None)])

    def test_payment_rules(self):
        self._receive()
        with self.assertRaises(app.ERPError):
            app.pay_supplier(self.conn, 1, 9_999_999)        # more than owed
        app.pay_supplier(self.conn, 1, 1_000_000)
        owed = self.conn.execute("SELECT amount_owed FROM v_ap_balance WHERE supplier_id=1").fetchone()
        self.assertEqual(owed["amount_owed"], 1_500_000)

    def test_duplicate_sku_and_supplier_rejected(self):
        with self.assertRaises(app.ERPError):
            app.add_material(self.conn, "rm-maize", "Dup", "kg")
        with self.assertRaises(app.ERPError):
            app.add_supplier(self.conn, "PackRight Uganda")

    def test_cannot_delete_referenced_master_data(self):
        self._receive()
        with self.assertRaises(app.ERPError):
            app.delete_supplier(self.conn, 1)
        with self.assertRaises(app.ERPError):
            app.delete_material(self.conn, 1)

    def test_ledgers_are_append_only(self):
        self._receive()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM journal_lines")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE stock_movements SET qty=1")

    def test_integrity_check_clean(self):
        self._receive()
        app.issue_to_production(self.conn, 1, 123)
        balanced, reconciled, _ = app.integrity_check(self.conn)
        self.assertTrue(balanced)
        self.assertTrue(reconciled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
