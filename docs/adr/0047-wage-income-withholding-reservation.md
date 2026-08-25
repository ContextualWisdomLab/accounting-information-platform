# ADR 0047: Reserve wage-income withholding off tax_payable 210100

**Status:** Accepted

## Decision

Wage-income withholding, including year-end settlement (연말정산), is not catalog `tax_payable` and is not chart 210100. AIS will map a future published withholding role to a statutory account only after Orgmetra assignment truth and a portal, Billing, or HRIS role code exist. This slice adds no catalog role, no 원천 / 급여 / 연말정산 table, no payroll or withholding journal, and no new HTTP command.

Catalog `tax_payable` → 210100 remains output VAT payable from issued invoices and issued-invoice voids (ADR 0045). The period VAT register and fail-closed HomeTax command (ADR 0046) stay on that liability. Reusing 210100 for withheld wage income would mix a consumption-tax output liability with an income-tax withholding liability.

The Value-Added Tax Act taxes the supply of goods and services and requires output VAT on those supplies (Korea Legislation Research Institute, 2024a). The Income Tax Act separately defines wage and salary income and makes a withholding agent liable for income tax withheld at source, including year-end settlement of that wage tax (Korea Legislation Research Institute, 2024b). Those statutes are different authorities. AIS therefore reserves the withholding map and does not open 연말정산, payroll journals, or a real NTS wage-withholding transport here.

## Consequences

Controllers continue to read output VAT on 210100 and the period VAT register. A later withholding slice must publish a distinct role and a distinct statutory account after Orgmetra assignment. Billing and HRIS cannot claim 210100 for wage withholding. `GET /account-role-mappings` still lists only the current catalog roles.

## References

Korea Legislation Research Institute. (2024a). *Value-Added Tax Act* [Unofficial translation]. https://elaw.klri.re.kr/eng_service/lawView.do?hseq=53110&lang=ENG

Korea Legislation Research Institute. (2024b). *Income Tax Act* [Unofficial translation]. https://elaw.klri.re.kr/eng_service/lawView.do?hseq=51753&lang=ENG
