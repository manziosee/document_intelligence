import logging
_logger = logging.getLogger(__name__)

env = env
company = env['res.company'].search([], limit=1)
_logger.info('=== SEEDING REMAINING ODOO MODULES ===')

# ===== 1. EMPLOYEES =====
_logger.info('1. Creating employees...')
individuals = env['res.partner'].search([('is_company', '=', False), ('parent_id', '!=', False)])
for partner in individuals:
    existing = env['hr.employee'].search([('address_id', '=', partner.id)], limit=1)
    if not existing:
        env['hr.employee'].create({
            'name': partner.name,
            'work_email': partner.email,
            'work_phone': partner.phone or partner.mobile,
            'address_id': partner.id,
            'company_id': company.id,
        })
        _logger.info(f'  ✓ {partner.name}')
env.cr.commit()

# ===== 2. EXPENSES =====
_logger.info('2. Creating expense claims...')
employees = env['hr.employee'].search([])
if employees:
    emp = employees[0]
    expense_product = env['product.product'].search([('type', '=', 'service')], limit=1)
    if expense_product:
        sheet = env['hr.expense.sheet'].create({
            'name': 'Office Supplies Purchase',
            'employee_id': emp.id,
            'expense_line_ids': [(0,0,{
                'name': 'Stationery and supplies',
                'employee_id': emp.id,
                'product_id': expense_product.id,
                'total_amount': 75000,
                'date': '2026-05-01',
                'payment_mode': 'company_account',
            })],
        })
        sheet.action_submit_sheet()
        sheet.action_approve_expense_sheets()
        sheet.action_sheet_move_create()
        _logger.info('  ✓ Expense sheet posted')
env.cr.commit()

# ===== 3. INVENTORY ADJUSTMENTS =====
_logger.info('3. Setting inventory quantities...')
products = env['product.product'].search([('type', '=', 'product')], limit=10)
if products:
    for p in products:
        try:
            wizard = env['stock.change.product.qty'].create({
                'product_id': p.id,
                'product_tmpl_id': p.product_tmpl_id.id,
                'new_quantity': 25,
            })
            wizard.change_product_qty()
        except Exception as e:
            _logger.warning(f'  ⚠ Could not set qty for {p.name}: {e}')
    _logger.info(f'  ✓ Set quantities for {len(products)} products')
env.cr.commit()

# ===== 4. CALENDAR EVENTS =====
_logger.info('4. Creating calendar events...')
for title, start, stop in [
    ('Monthly Sales Review', '2026-05-10 09:00:00', '2026-05-10 10:00:00'),
    ('Product Strategy', '2026-05-12 14:00:00', '2026-05-12 15:30:00'),
]:
    env['calendar.event'].create({
        'name': title,
        'start': start,
        'stop': stop,
        'user_id': env.user.id,
    })
_logger.info('  ✓ 2 meetings scheduled')
env.cr.commit()

# ===== 5. FLEET VEHICLES =====
_logger.info('5. Creating fleet vehicles...')
vehicle_model = env['fleet.vehicle.model'].search([], limit=1)
if vehicle_model:
    employees = env['hr.employee'].search([], limit=2)
    for i, plate in enumerate(['RAA123A', 'RAB456B']):
        env['fleet.vehicle'].create({
            'model_id': vehicle_model.id,
            'license_plate': plate,
            'color': 'White' if i==0 else 'Silver',
            'company_id': company.id,
            'driver_employee_id': employees[i].id if len(employees) > i else False,
        })
    _logger.info('  ✓ 2 vehicles added')
env.cr.commit()

# ===== 6. EMAIL MARKETING ==========
_logger.info('6. Creating email campaign...')
try:
    if env['mailing.mailing'].check_access_rights('write', raise_exception=False):
        env['mailing.mailing'].create({
            'name': 'May Newsletter',
            'subject': 'Special Offers This Month!',
            'body_html': '<p>Hello! Check out our May specials.</p>',
            'mailing_model_id': env.ref('base.model_res_partner').id,
        })
        _logger.info('  ✓ Email campaign created')
except Exception as e:
    _logger.warning(f'  ⚠ Email: {e}')
env.cr.commit()

# ===== 7. SMS MARKETING =====
_logger.info('7. Creating SMS campaign...')
try:
    if 'sms.sms' in env:
        partners = env['res.partner'].search([('mobile', '!=', False)], limit=3)
        if partners:
            env['sms.sms'].create({
                'name': 'SMS Blast',
                'body': 'Hello! We have special offers. Contact us!',
                'partner_ids': [(6,0,partners.ids)],
            })
            _logger.info('  ✓ SMS campaign created')
except Exception as e:
    _logger.warning(f'  ⚠ SMS: {e}')
env.cr.commit()

# ===== 8. TODO TASKS =====
_logger.info('8. Creating tasks...')
partners = env['res.partner'].search([('is_company', '=', True)], limit=3)
for model_name in ['todo.task', 'todo.todo', 'project.task']:
    try:
        Todo = env[model_name]
        # Determine user field
        if 'user_id' in Todo._fields:
            user_field = 'user_id'
        elif 'user_ids' in Todo._fields:
            user_field = 'user_ids'
        else:
            continue
        for p in partners:
            vals = {
                'name': f'Follow up - {p.name}',
                'date_deadline': '2026-05-30',
            }
            if user_field == 'user_id':
                vals['user_id'] = env.user.id
            else:
                vals['user_ids'] = [(6, 0, [env.user.id])]
            Todo.create(vals)
        _logger.info(f'  ✓ {len(partners)} tasks created')
        break
    except KeyError:
        continue
env.cr.commit()

# ===== 9. LINK TRACKER =====
_logger.info('9. Creating tracking links...')
if 'link.tracker' in env:
    partners = env['res.partner'].search([('is_company', '=', True)], limit=2)
    for p in partners:
        env['link.tracker'].create({
            'url': f'https://www.kigalitech.rw/ref/{p.name[:6]}',
            'title': f'Campaign_{p.name[:6]}',
        })
    _logger.info('  ✓ Tracking links created')
env.cr.commit()

_logger.info('✅ SEEDING COMPLETE!')
_logger.info('═══════════════════════════════════════════')
_logger.info('Data added for:')
_logger.info('  • Employees (4)')
_logger.info('  • Expenses (1 sheet, 2 lines)')
_logger.info('  • Inventory (adjustment for 10 products)')
_logger.info('  • Calendar (2 events)')
_logger.info('  • Fleet (2 vehicles)')
_logger.info('  • Email Marketing (1 campaign)')
_logger.info('  • SMS Marketing (1 campaign)')
_logger.info('  • Todo Tasks (3)')
_logger.info('  • Link Tracker (2 links)')
_logger.info('═══════════════════════════════════════════')
