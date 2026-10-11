# Prompt Log

2026-10-10 ,Used Claude,I Asked how to push files to GitHub Using VS Code Step-by-step instructions. Also The  Template said to rename gitignore, which i had to do manually and i Renamed file,pushed successfully
I Asked Claude for an ERP prototype for a food company (Materials Mgmt + Financials)  schema.sql, seed.sql, app.py, test_erp.py, README

## Critical checks I performed

*Moving-average cost:*
    I Received 1000kg at 2500 and 1000kg at 3500. Hand calculation:(1000*2500+1000*3500) divide by 2000=3000. test moving average cost passed and the system showed 3000, so it matches.
*REAL for money / rounding drift:*
    I received 3 units at 333.33. The total was 999.99 and the average cost showed EXACTLY.  The schema stores money as REAL (floating point), so small errors can build up over many transactions. Production systems use integers (e.g. whole UGX) or DECIMAL.
*Missing business rules:*
    NO VAT,so purchases and payables ignore tax that Ugandan businesses must account for.
    No supplier invoice matching, so the system assumes the invoice equals the goods received.
    No goods-return function, so a faulty delivery cannot be reversed. 
    No approval workflow, so anyone can create and pay for a PO.
*Errors on my machine:*
    MY machine was freezing Sometimes.
# Internet searches used
 I searched claude app download on microsoft EDGE For ERP architecture for my business.
