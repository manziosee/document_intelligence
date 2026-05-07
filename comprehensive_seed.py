#!/usr/bin/env python3
"""
COMPREHENSIVE ODOO 17 DATA SEEDING
Populates ALL major modules with realistic Rwandan business data

Modules covered:
  ✓ Point of Sale (POS)
  ✓ Expenses (hr_expense)
  ✓ Inventory / Stock
  ✓ Assets (account_asset)
  ✓ Calendar / Events
  ✓ CRM (leads → opportunities)
  ✓ Fleet (vehicles, drivers, leases)
  ✓ Link Tracker
  ✓ Employees (HR)
  ✓ Email Marketing (mass_mailing)
  ✓ SMS Marketing (sms)
  ✓ To-Do Tasks
"""

import logging
_logger = logging.getLogger(__name__)

env = env
main_company = env['res.company'].browse(1)
_logger.info("=== COMPREHENSIVE SEEDING STARTED ===")

# ═══════════════════════════════════════════════════════════════════════════════
# 1. CACHE: PARTNERS, PRODUCTS, USERS
# ═══════════════════════════════════════════════════════════════════════════════

partners = {
    'kigali_tech': env['res.partner'].browse(48),
    'greenfarm': env['res.partner'].browse(49),
    'vision': env['res.partner'].browse(50),
    'bright': env['res.partner'].browse(51),
    'jean': env['res.partner'].browse(52),
    'aline': env['res.partner'].browse(53),
    'eric': env['res.partner'].browse(54),
    'diane': env['res.partner'].browse(55),
}
_logger.info(f"✓ Partners cached: {len(partners)}")

products = {}
for prod in env['product.product'].search([('company_id', '=', main_company.id)]):
    products[prod.name] = prod.id
_logger.info(f"✓ Products cached: {len(products)}")

# Sales users
sales_users = {}
for key in ['kigali_tech', 'greenfarm', 'vision', 'bright']:
    user = env['res.users'].search([('login', '=', f'sales_{key}@myco.rw')], limit=1)
    if user:
        sales_users[key] = user
_logger.info(f"✓ Sales users cached: {len(sales_users)}")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. POINT OF SALE (POS) - Basic Config Only
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Setting up Point of Sale configurations...")

pos_configs = {}
pos_names = ['Kigali Tech POS', 'GreenFarm POS', 'Vision POS', 'Bright Education POS']
for idx, (key, partner) in enumerate(partners.items()):
    if idx >= 4: break
    config = env['pos.config'].search([('name', '=', pos_names[idx])], limit=1)
    if not config:
        cash_journal = env['account.journal'].search([('type', '=', 'cash'), ('company_id', '=', main_company.id)], limit=1)
        sale_journal = env['account.journal'].search([('type', '=', 'sale'), ('company_id', '=', main_company.id)], limit=1)
        config = env['pos.config'].create({
            'name': pos_names[idx],
            'company_id': main_company.id,
            'journal_id': cash_journal.id if cash_journal else False,
            'invoice_journal_id': sale_journal.id if sale_journal else False,
        })
    pos_configs[key] = config
_logger.info(f"✓ {len(pos_configs)} POS configurations created")
env.cr.commit()

# Skip POS order creation - requires full session workflow
_logger.info("⏭ POS orders skipped (create manually via UI)")

# Create some sample POS orders
for idx, (key, config) in enumerate(list(pos_configs.items())[:2]):
    session = env['pos.session'].create({
        'config_id': config.id,
        'user_id': env.user.id,
        'company_id': main_company.id,
    })
    session.action_pos_session_open()
    # Create a simple order
    product_list = list(products.items())[:3]
    lines_vals = []
    for _, pid in product_list:
        prod = env['product.product'].browse(pid)
        lines_vals.append((0, 0, {
            'product_id': pid,
            'qty': 1,
            'price_unit': prod.list_price,
            'name': prod.name,
        }))
    order = env['pos.order'].create({
        'session_id': session.id,
        'partner_id': partners[key].id,
        'lines': lines_vals,
    })
    order.action_pos_order_paid()
    session.action_pos_session_closing()
_logger.info("✓ Sample POS orders created")
env.cr.commit()
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 3. EXPENSES (hr_expense)
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating expense data...")

# Get employees (create from users)
employees = {}
for key, user in sales_users.items():
    emp = env['hr.employee'].search([('user_id', '=', user.id)], limit=1)
    if not emp:
        emp = env['hr.employee'].create({
            'name': user.name,
            'user_id': user.id,
            'company_id': main_company.id,
            'work_email': user.email,
            'work_phone': '+250788000000',
        })
    employees[key] = emp
_logger.info(f"✓ {len(employees)} employees created")

# Expense products
expense_products = {}
for name in ['Taxi Ride', 'Hotel Stay', 'Meal', 'Office Supplies', 'Internet Bill']:
    p = env['product.product'].search([('name', '=', name), ('type', '=', 'service')], limit=1)
    if not p:
        p = env['product.product'].create({
            'name': name,
            'type': 'service',
            'company_id': main_company.id,
            'sale_ok': False,
            'purchase_ok': True,
        })
    expense_products[name] = p.id

# Create expense sheets
expense_sheets = []
for emp_key, emp in employees.items():
    sheet = env['hr.expense.sheet'].create({
        'name': f'Expense Claim - {emp.name}',
        'employee_id': emp.id,
        'company_id': main_company.id,
    })
    # Add expense lines
    for prod_name in ['Taxi Ride', 'Meal']:
        env['hr.expense'].create({
            'name': f'{prod_name} for {emp.name}',
            'employee_id': emp.id,
            'product_id': expense_products[prod_name],
            'unit_amount': 25000 if prod_name == 'Taxi Ride' else 15000,
            'quantity': 1,
            'sheet_id': sheet.id,
        })
    sheet.action_submit_sheet()
    sheet.action_approve_expense_sheets()
    expense_sheets.append(sheet)
_logger.info(f"✓ {len(expense_sheets)} expense sheets created")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 4. INVENTORY ON HAND (Stock Quants)
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Updating inventory quantities...")

warehouse = env['stock.warehouse'].search([('company_id', '=', main_company.id)], limit=1)
if warehouse:
    for prod_name, pid in products.items():
        if env['product.product'].browse(pid).type == 'product':
            # Set quantity directly
            env['stock.quant'].with_context(inventory_mode=True).create({
                'product_id': pid,
                'location_id': warehouse.lot_stock_id.id,
                'quantity': 100,
            })
    _logger.info(f"✓ Stock quants set for {len(products)} products")
    env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 5. FIXED ASSETS (account_asset)
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating fixed assets...")

# Get asset profile
profile = env['account.asset.profile'].search([], limit=1)
if not profile:
    # Create asset category
    profile = env['account.asset.profile'].create({
        'name': 'IT Equipment',
        'account_asset_id': env['account.account'].search([('code', '=', '160000')], limit=1).id or env['account.account'].search([('account_type', '=', 'asset_fixed')], limit=1).id,
        'account_depreciation_id': env['account.account'].search([('code', '=', '680000')], limit=1).id or env['account.account'].search([('account_type', '=', 'expense_depreciation')], limit=1).id,
        'account_expense_depreciation_id': env['account.account'].search([('code', '=', '680000')], limit=1).id or env['account.account'].search([('account_type', '=', 'expense_depreciation')], limit=1).id,
        'company_id': main_company.id,
        'method': 'linear',
        'method_number': 3,
        'method_period': 'year',
    })

# Create asset records for some products
asset_products = ['Laptop - Dell Latitude', 'Network Switch 24p']
for prod_name in asset_products:
    if prod_name in products:
        env['account.asset'].create({
            'name': f'Asset - {prod_name}',
            'profile_id': profile.id,
            'purchase_value': 1000000,
            'product_id': products[prod_name],
            'date_start': '2026-01-01',
            'company_id': main_company.id,
        })
_logger.info("✓ Fixed assets created")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 6. CALENDAR / EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating calendar events...")

events = [
    ('Board Meeting - Kigali Tech', '2026-05-15 10:00:00', 'Strategic planning', partners['kigali_tech']),
    ('Harvest Planning - GreenFarm', '2026-05-20 14:00:00', 'Q3 harvest scheduling', partners['greenfarm']),
    ('Client Review - Vision', '2026-05-25 09:00:00', 'Quarterly business review', partners['vision']),
    ('Staff Training - Bright', '2026-06-01 11:00:00', 'Teacher development workshop', partners['bright']),
]

for title, start_dt, desc, partner in events:
    event = env['calendar.event'].create({
        'name': title,
        'start': start_dt,
        'stop': start_dt,
        'duration': 2,
        'description': desc,
        'partner_ids': [(6, 0, [partner.id])],
        'company_id': main_company.id,
    })
_logger.info(f"✓ {len(events)} calendar events created")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 7. CRM - OPPORTUNITIES (extend existing leads)
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating CRM opportunities...")

leads = env['crm.lead'].search([], limit=2)
if leads:
    for lead in leads:
        lead.write({
            'stage_id': env.ref('crm.stage_lead1').id,  # Qualified
            'probability': 50,
        })
        # Create opportunity from lead
        lead.handle_partner_assignation()
_logger.info("✓ CRM leads updated to opportunities")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 8. FLEET (Vehicles & Drivers)
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Setting up fleet...")

# Get employees as drivers
drivers = list(employees.values())[:2]

# Create vehicles
vehicles = []
for i, driver in enumerate(drivers):
    vehicle = env['fleet.vehicle'].create({
        'name': f'Company Car {i+1}',
        'license_plate': f'RAA{1000+i}',
        'model_id': env['fleet.vehicle.model'].search([], limit=1).id,
        'driver_id': driver.id,
        'company_id': main_company.id,
    })
    vehicles.append(vehicle)
_logger.info(f"✓ {len(vehicles)} fleet vehicles created")

# Create log sheets for each vehicle
for vehicle in vehicles:
    env['fleet.vehicle.log.fuel'].create({
        'vehicle_id': vehicle.id,
        'date': '2026-05-01',
        'liter': 40,
        'price_unit': 1000,
    })
_logger.info("✓ Fleet log entries created")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 9. LINK TRACKER (URL Shortening/Tracking)
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating tracked links...")

links = [
    ('Kigali Tech Website', 'https://www.kigalitech.rw/contact'),
    ('GreenFarm Product Catalog', 'https://www.greenfarm.rw/catalog'),
    ('Vision Consulting Proposal', 'https://www.visiongroup.com/proposal'),
]

for name, url in links:
    env['link.tracker'].create({
        'title': name,
        'url': url,
        'company_id': main_company.id,
    })
_logger.info(f"✓ {len(links)} tracked links created")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 10. EMPLOYEES (HR) - Already created above, add more details
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Enhancing employee records...")

for emp in env['hr.employee'].search([], limit=4):
    emp.write({
        'work_email': emp.work_email or f'{emp.name.replace(" ", ".").lower()}@myco.rw',
        'work_phone': '+250788123456',
    })
_logger.info("✓ Employee records updated")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 11. EMAIL MARKETING (mass_mailing)
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating email marketing campaigns...")

mailing_list = env['mailing.list'].search([], limit=1)
if not mailing_list:
    mailing_list = env['mailing.list'].create({
        'name': 'Rwanda Business Contacts',
        'company_id': main_company.id,
    })
    # Add contacts
    for partner in partners.values():
        env['mailing.contact'].create({
            'list_ids': [(6, 0, [mailing_list.id])],
            'email': partner.email or 'test@example.com',
            'name': partner.name,
        })

campaigns = [
    ('IT Solutions Newsletter', 'Latest tech trends and services'),
    ('Agriculture Products Promo', 'Fresh produce wholesale'),
    ('Consulting Services Intro', 'Business transformation'),
    ('Education Program 2026', 'New courses and enrollment'),
]

for title, subject in campaigns:
    mailing = env['mailing.mailing'].create({
        'subject': subject,
        'body_html': f'<p>Dear valued customer,</p><p>{subject}</p>',
        'mailing_model_id': env.ref('mass_mailing.model_mailing_list').id,
        'contact_list_ids': [(6, 0, [mailing_list.id])],
        'company_id': main_company.id,
        'state': 'draft',
    })
_logger.info(f"✓ {len(campaigns)} email campaigns drafted")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 12. SMS MARKETING
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating SMS campaigns...")

if 'sms.sms' in env:
    for title, _ in campaigns[:2]:
        sms = env['sms.sms'].create({
            'name': f'SMS: {title}',
            'body': f'Hello! Check our latest offer: {title}. Reply STOP to unsubscribe.',
            'company_id': main_company.id,
            'state': 'draft',
        })
    _logger.info("✓ SMS campaigns drafted")
    env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 13. TO-DO / TASKS
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Creating to-do tasks...")

tasks = [
    ('Follow up with GreenFarm', partners['greenfarm'].id, '2026-05-10', 'high'),
    ('Prepare proposal for Vision', partners['vision'].id, '2026-05-12', 'high'),
    ('Send invoice to Kigali Tech', partners['kigali_tech'].id, '2026-05-15', 'medium'),
    ('Staff meeting', False, '2026-05-20', 'medium'),
]

for title, partner_id, deadline, priority in tasks:
    env['todo.task'].create({
        'name': title,
        'partner_id': partner_id,
        'date_deadline': deadline,
        'priority': priority,
        'user_id': env.user.id,
    })
_logger.info(f"✓ {len(tasks)} to-do tasks created")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# 14. LINK ALL EXPENSES TO EMPLOYEES
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("Linking expenses to partners...")

# Update expense products to link to proper partners if needed
for sheet in env['hr.expense.sheet'].search([], limit=4):
    if sheet.employee_id:
        # Link to corresponding partner if exists
        partner = env['res.partner'].search([('name', 'ilike', sheet.employee_id.name)], limit=1)
        if partner:
            sheet.write({'partner_id': partner.id})
_logger.info("✓ Expense sheets linked")
env.cr.commit()

# ═══════════════════════════════════════════════════════════════════════════════
# DONE
# ═══════════════════════════════════════════════════════════════════════════════

_logger.info("")
_logger.info("=" * 70)
_logger.info("✅ COMPREHENSIVE SEEDING COMPLETE!")
_logger.info("=" * 70)
_logger.info("📋 ALL MODULES POPULATED:")
_logger.info("  • Point of Sale: 2+ POS configs + orders")
_logger.info("  • Expenses: Employee claims approved")
_logger.info("  • Inventory: Stock quants set for all products")
_logger.info("  • Fixed Assets: IT equipment assets created")
_logger.info("  • Calendar: Business events scheduled")
_logger.info("  • CRM: Leads converted to opportunities")
_logger.info("  • Fleet: Company vehicles + drivers + log sheets")
_logger.info("  • Link Tracker: Tracked URLs created")
_logger.info("  • Employees: HR records enhanced")
_logger.info("  • Email Marketing: Campaigns drafted")
_logger.info("  • SMS Marketing: SMS campaigns drafted")
_logger.info("  • To-Do: Tasks assigned")
_logger.info("=" * 70)
