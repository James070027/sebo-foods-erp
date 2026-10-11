-- Chart of accounts used by the MM -> FI integration
INSERT INTO gl_accounts (code, name, type) VALUES
 ('1000','Cash and Bank',                   'ASSET'),
 ('1200','Inventory - Raw Materials',       'ASSET'),
 ('2100','Accounts Payable',                'LIABILITY'),
 ('3000','Owner Equity',                    'EQUITY'),
 ('5000','Raw Materials Consumed (COGS)',   'EXPENSE');

-- Opening capital so the company can pay suppliers
INSERT INTO journal_entries (id, entry_date, description, ref_type, ref_id)
VALUES (1, date('now'), 'Opening capital', 'OPENING', NULL);
INSERT INTO journal_lines (entry_id, account_id, debit, credit)
VALUES (1, (SELECT id FROM gl_accounts WHERE code='1000'), 50000000, 0),
       (1, (SELECT id FROM gl_accounts WHERE code='3000'), 0, 50000000);

-- Sample suppliers
INSERT INTO suppliers (name, phone, email, address) VALUES
 ('Kampala Grain Millers', '+256700000001', 'sales@kgm.example', 'Kampala'),
 ('Jinja Sugar Traders',   '+256700000002', 'info@jst.example',  'Jinja'),
 ('PackRight Uganda',      '+256700000003', 'orders@pack.example','Kampala');

-- Sample raw materials (amounts in UGX)
INSERT INTO materials (sku, name, unit, reorder_level) VALUES
 ('RM-MAIZE', 'Maize flour',        'kg',   500),
 ('RM-SUGAR', 'Sugar',              'kg',   300),
 ('RM-OIL',   'Vegetable oil',      'litre',200),
 ('PK-BAG1',  'Packaging bag 1kg',  'pcs',  1000);
