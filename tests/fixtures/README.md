# Test fixtures: SYNTHETIC data

Every company in this folder is **made up**. Names, domains (all under `example.*`), ABNs, phone
numbers and descriptions were generated for code tests. They do not describe real organisations
and must never be used as evaluation data or shown as real prospects.

- `synthetic_companies.csv`: ~150 rows with Firmable-style column names ("Company Name", "Website",
  "Employee Count", "HQ State", ...). It covers edge cases: blank rows, a missing name, employee
  ranges in the count column, micro businesses, enterprises, revenue ranges ("$1M-$10M", "10M"),
  mixed list separators and JSON lists, and dd/mm/yyyy dates.
- `alt_schema_companies.jsonl`: 6 rows with **different** snake_case column names
  (`organization_name`, `company_domain`, `num_employees`, `tech_stack`, ...). Proves the alias and
  fuzzy column mapping in `pipeline/schema.py` works.
