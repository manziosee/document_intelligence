#!/usr/bin/env python3
"""
Comprehensive Odoo 17 Data Seeding
Creates: Payment Terms, Products, Sales Orders + Invoices, Purchase Orders + Bills, CRM
"""

import logging
_logger = logging.getLogger(__name__)

env = env

# ═══════════════════════════════════════════════════════════════════════════════
# 1. GET MAIN COMPANY & PARTNERS
# ═══════════════════════════════════════════════════════════════════════════════

main_company = env['res.company'].browse(1)
_logger.info(f"✓ Company: {main_company.name}")

partners = {
    'kigali_tech': env['res.partner'].browse(48),
    'greenfarm': env['res.partner'].browse(49),
    'vision': env['res.partner'].browse(50),
    'bright': env['res.partner'].browse(51),
}
_logger.info(f"✓ Loaded {len(partners)} partners")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. PAYMENT TERMS
# ═══════════════════════════════════════════════════════════════════════════════

def get_pt(name):
    pt = env['account.payment.term'].search([('name', '=', name)], limit=1)
    if not pt:
        pt = env['account.payment.term'].create({
            'name': name,
            'line_ids': [(0, 0, {'value': 'percent', 'value_amount': 100.0, 'nb_days': 0, 'delay_type': 'days_after'})],
        })
    return pt

pt_0  = get_pt('Immediate Payment')
pt_15 = get_pt('15 Days')
pt_30 = get_pt('30 Days')

for key, pt in [('kigali_tech', pt_30), ('greenfarm', pt_15), ('vision', pt_30), ('bright', pt_0)]:
    partners[key].write({'property_payment_term_id': pt.id})

_logger.info("✓ Payment terms configured")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 3. PRODUCTS
# ═══════════════════════════════════════════════════════════════════════════════

uom = env['uom.uom'].search([], limit=1)
cat = env['product.category'].search([], limit=1)

product_defs = [
    ('Web Development Service', 'service', 2500000, 1500000),
    ('Mobile App Dev', 'service', 4000000, 2500000),
    ('IT Consulting Hour', 'service', 500000, 300000),
    ('Server Maintenance', 'service', 800000, 400000),
    ('Laptop - Dell Latitude', 'product', 1200000, 900000),
    ('Network Switch 24p', 'product', 350000, 220000),
    ('Tomatoes Crate', 'product', 25000, 15000),
    ('Cabbage Head', 'product', 800, 400),
    ('Onions Kg', 'product', 2000, 1200),
    ('Banana Bunch', 'product', 5000, 3000),
    ('Fertilizer 50kg', 'product', 35000, 28000),
    ('Biz Strategy Consulting', 'service', 5000000, 2500000),
    ('Financial Advisory', 'service', 3500000, 1800000),
    ('Market Research Report', 'service', 1500000, 800000),
    ('Training Workshop Day', 'service', 2000000, 1200000),
    ('Due Diligence', 'service', 2500000, 1400000),
    ('Corporate Finance', 'service', 4000000, 2200000),
    ('English Course', 'service', 150000, 80000),
    ('Math Tuition Monthly', 'service', 120000, 60000),
    ('Computer Training', 'service', 180000, 100000),
    ('School Uniform Set', 'product', 25000, 15000),
    ('Math Textbook', 'product', 15000, 9000),
    ('English Textbook', 'product', 15000, 9000),
    ('Notebook Pack 10', 'product', 5000, 3000),
    ('Pen Set Box 50', 'product', 8000, 5000),
]

products = {}
for name, ptype, price, cost in product_defs:
    p = env['product.product'].search([('name', '=', name)], limit=1)
    if not p:
        p = env['product.product'].create({
            'name': name,
            'type': ptype,
            'categ_id': cat.id,
            'list_price': price,
            'standard_price': cost,
            'uom_id': uom.id,
            'uom_po_id': uom.id,
            'company_id': main_company.id,
            'sale_ok': True,
            'purchase_ok': True,
        })
    products[name] = p.id

_logger.info(f"✓ {len(products)} products/services")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 4. SALES TEAMS + USERS
# ═══════════════════════════════════════════════════════════════════════════════

base_user = env.ref('base.group_user')
sale_mgr = env.ref('sales_team.group_sale_manager')

teams = {}
users = {}
for key, partner in partners.items():
    team = env['crm.team'].search([('name', '=', f'Sales - {partner.name}')], limit=1)
    if not team:
        team = env['crm.team'].create({'name': f'Sales - {partner.name}', 'company_id': main_company.id})
    teams[key] = team

    login = f'sales_{key}@myco.rw'
    user = env['res.users'].search([('login', '=', login)], limit=1)
    if not user:
        user = env['res.users'].create({
            'name': f'Sales - {partner.name}',
            'login': login,
            'email': login,
            'partner_id': partner.id,
            'company_id': main_company.id,
            'company_ids': [(6, 0, [main_company.id])],
            'groups_id': [(6, 0, [base_user.id, sale_mgr.id])],
        })
    users[key] = user
    team.write({'member_ids': [(4, user.id)]})

_logger.info(f"✓ {len(teams)} sales teams + users")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 5. SALES ORDERS + INVOICES
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating sales orders...")

sos_data = [
    ('greenfarm', 'kigali_tech', [
        ('Web Development Service', 1, 2500000),
        ('Laptop - Dell Latitude', 3, 1200000),
    ], '30_days'),
    ('kigali_tech', 'vision', [
        ('Biz Strategy Consulting', 1, 5000000),
    ], '30_days'),
    ('vision', 'bright', [
        ('School Uniform Set', 50, 25000),
        ('Math Textbook', 100, 15000),
    ], 'immediate'),
    ('vision', 'kigali_tech', [
        ('Network Switch 24p', 10, 350000),
        ('Server Maintenance', 1, 800000),
    ], '30_days'),
]

sales_orders = []
pt_map = {'15_days': pt_15, '30_days': pt_30, 'immediate': pt_0}

for cust_key, team_key, lines, pt_key in sos_data:
    so = env['sale.order'].create({
        'partner_id': partners[cust_key].id,
        'company_id': main_company.id,
        'team_id': teams[team_key].id,
        'user_id': users[team_key].id,
        'date_order': '2026-05-01',
        'payment_term_id': pt_map[pt_key].id,
        'order_line': [
            (0, 0, {'product_id': products[pname], 'product_uom_qty': qty, 'price_unit': price})
            for pname, qty, price in lines
        ],
    })
    so.action_confirm()
    sales_orders.append(so)
    _logger.info(f"✓ SO {so.name} → {partners[cust_key].name}")

_logger.info(f"✓ {len(sos_data)} sales orders confirmed")
env.cr.commit()

_logger.info("Posting customer invoices...")
cust_invs = []
for so in sales_orders:
    inv = so._create_invoices()
    inv.write({'date': '2026-05-10', 'invoice_date': '2026-05-10'})
    inv.action_post()
    cust_invs.append(inv)
_logger.info(f"✓ {len(cust_invs)} customer invoices posted")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 6. PURCHASE ORDERS + BILLS
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating purchase orders...")

pos_data = [
    ('greenfarm', 'vision', [('Fertilizer 50kg', 10, 35000)], '15_days'),
    ('bright', 'kigali_tech', [('Laptop - Dell Latitude', 2, 1200000)], 'immediate'),
]

purchase_orders = []
for buyer_key, vendor_key, lines, pt_key in pos_data:
    po = env['purchase.order'].create({
        'partner_id': partners[vendor_key].id,
        'company_id': main_company.id,
        'date_order': '2026-05-01',
        'payment_term_id': pt_map[pt_key].id,
        'order_line': [
            (0, 0, {'product_id': products[pname], 'product_qty': qty, 'price_unit': price, 'name': pname})
            for pname, qty, price in lines
        ],
    })
    po.button_confirm()
    purchase_orders.append(po)
    _logger.info(f"✓ PO {po.name} from {partners[vendor_key].name}")

_logger.info(f"✓ {len(purchase_orders)} POs confirmed")
env.cr.commit()

_logger.info("Posting vendor bills...")
vend_bills = []
for po in purchase_orders:
    bill = env['account.move'].create({
        'move_type': 'in_invoice',
        'partner_id': po.partner_id.id,
        'company_id': main_company.id,
        'invoice_date': '2026-05-10',
        'invoice_origin': po.name,
        'invoice_line_ids': [
            (0, 0, {
                'product_id': line.product_id.id,
                'name': line.name,
                'quantity': line.product_qty,
                'price_unit': line.price_unit,
            }) for line in po.order_line
        ],
    })
    bill.action_post()
    vend_bills.append(bill)
_logger.info(f"✓ {len(vend_bills)} vendor bills posted")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 7. PAYMENTS
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Registering payments...")
pay_meth = env['account.payment.method'].search([('code', '=', 'manual')], limit=1)
if cust_invs:
    inv = cust_invs[0]
    pay = env['account.payment'].create({
        'partner_id': inv.partner_id.id,
        'amount': inv.amount_total * 0.5,
        'payment_type': 'inbound',
        'partner_type': 'customer',
        'payment_method_id': pay_meth.id,
        'reconciled_invoice_ids': [(6, 0, [inv.id])],
    })
    pay.action_post()
_logger.info("✓ Payment registered")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 8. CRM LEADS
# ═══════════════════════════════════════════════════════════════════════════════

if 'crm.lead' in env:
    lead_data = [
        ('Kigali City Gov - IT Modernization', 'Mr. Habimana', 'procurement@kigali.gov.rw', '+250788000000', 15000000),
        ('Rwanda Tourism - Digital Platform', 'Ms. Uwase', 'ict@tourism.gov.rw', '+250788111111', 8000000),
    ]
    for name, contact, email, phone, rev in lead_data:
        env['crm.lead'].create({
            'name': name,
            'contact_name': contact,
            'email_from': email,
            'phone': phone,
            'type': 'lead',
            'team_id': teams['vision'].id,
            'company_id': main_company.id,
            'expected_revenue': rev,
        })
    _logger.info(f"✓ {len(lead_data)} CRM leads created")
    env.cr.commit()
else:
    _logger.info("⚠ CRM module not installed")

# ═══════════════════════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("")
_logger.info("=" * 70)
_logger.info("✅ SEEDING COMPLETE!")
_logger.info("=" * 70)
_logger.info("📊 Summary:")
_logger.info(f"  • {len(products)} products/services")
_logger.info(f"  • {len(teams)} sales teams")
_logger.info(f"  • {len(sales_orders)} sales orders (confirmed)")
_logger.info(f"  • {len(cust_invs)} customer invoices (posted)")
_logger.info(f"  • {len(purchase_orders)} purchase orders (confirmed)")
_logger.info(f"  • {len(vend_bills)} vendor bills (posted)")
_logger.info("=" * 70)
