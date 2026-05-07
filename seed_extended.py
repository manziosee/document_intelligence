import logging
from datetime import datetime
_logger = logging.getLogger(__name__)

env = env
company = env['res.company'].search([], limit=1)
_logger.info('=== SEEDING: Assets, Calendar, To-Do, Projects, Manufacturing, Maintenance ===')

# ========== 1. FIXED ASSETS (asset.management) ==========
_logger.info('1. Creating fixed assets...')
if 'asset.management' in env:
    Asset = env['asset.management']
    asset_names = [
        'Dell XPS Laptop - Finance',
        'Canon Office Printer',
        'Toyota Hilux - Sales Fleet',
        'Office Ergonomic Chairs (Set of 5)',
        'Servers & Network Equipment',
        'Conference Room AV System',
    ]
    for name in asset_names:
        try:
            if Asset.search([('name', '=', name)], limit=1):
                _logger.info(f'  ⏭ Asset already exists: {name}')
                continue
            asset = Asset.create({
                'name': name,
                'model_type': 'single',
            })
            _logger.info(f'  ✓ Asset: {name}')
        except Exception as e:
            _logger.warning(f'  ⚠ Asset {name}: {e}')
    env.cr.commit()
else:
    _logger.warning('  ⚠ asset.management model not available')

# ========== 2. CALENDAR EVENTS ==========
_logger.info('2. Creating calendar events...')
partners = env['res.partner'].search([('is_company', '=', True)], limit=12)
events = [
    ('Monthly Sales Review - Kigali Tech', '2026-05-10 09:00:00', '2026-05-10 10:00:00', partners.filtered(lambda p: 'Kigali Tech' in p.name)),
    ('Product Launch Strategy', '2026-05-11 14:00:00', '2026-05-11 15:30:00', partners.filtered(lambda p: 'GreenFarm' in p.name)),
    ('Client Kickoff - Vision Consulting', '2026-05-13 10:00:00', '2026-05-13 12:00:00', partners.filtered(lambda p: 'Vision' in p.name)),
    ('Training Session - Bright Education', '2026-05-15 13:00:00', '2026-05-15 15:00:00', partners.filtered(lambda p: 'Bright' in p.name)),
    ('Quarterly Financial Review', '2026-05-18 11:00:00', '2026-05-18 12:30:00', None),
    ('Supplier Meeting - GreenFarm', '2026-05-20 09:30:00', '2026-05-20 10:30:00', partners.filtered(lambda p: 'GreenFarm' in p.name)),
    ('IT Systems Upgrade Discussion', '2026-05-22 15:00:00', '2026-05-22 16:00:00', partners.filtered(lambda p: 'Kigali Tech' in p.name)),
    ('Marketing Campaign Planning', '2026-05-25 10:00:00', '2026-05-25 11:30:00', None),
    ('Procurement Review - Vision', '2026-05-27 14:00:00', '2026-05-27 15:00:00', partners.filtered(lambda p: 'Vision' in p.name)),
    ('Partnership Discussion - Bright Education', '2026-05-29 11:00:00', '2026-05-29 12:00:00', partners.filtered(lambda p: 'Bright' in p.name)),
]
for name, start, stop, partner in events:
    vals = {'name': name, 'start': start, 'stop': stop, 'user_id': env.user.id}
    if partner:
        vals['partner_ids'] = [(6,0,[partner.id])]
    try:
        env['calendar.event'].create(vals)
        _logger.info(f'  ✓ Event: {name}')
    except Exception as e:
        _logger.warning(f'  ⚠ Event {name}: {e}')
env.cr.commit()

# ========== 3. PROJECT TASKS (To-Do system) ==========
_logger.info('3. Creating project tasks...')
Todo = None
for model_name in ['project.task', 'todo.task', 'todo.todo']:
    try:
        Todo = env[model_name]
        break
    except KeyError:
        continue
if Todo:
    # Ensure a project exists for tasks if needed
    project = env['project.project'].search([], limit=1) or env['project.project'].create({'name': 'Operations 2026'})
    tasks = [
        ('Finalize Q2 financial report', 'urgent', '2026-05-20'),
        ('Prepare client proposals for leads', 'high', '2026-05-25'),
        ('Update product catalog & pricing', 'medium', '2026-05-30'),
        ('Conduct team training session', 'low', '2026-06-05'),
        ('Review supplier contracts renewal', 'medium', '2026-05-28'),
        ('Prepare monthly invoice batch', 'high', '2026-05-22'),
        ('Update inventory stock levels', 'medium', '2026-05-26'),
        ('Client follow-up calls', 'low', '2026-06-01'),
        ('Website content update', 'low', '2026-05-31'),
        ('Prepare presentation for new client', 'high', '2026-05-24'),
    ]
    for task_name, priority, deadline in tasks:
        try:
            Todo.create({
                'name': task_name,
                'project_id': project.id,
                'priority': priority,
                'date_deadline': deadline,
                'user_ids': [(6,0,[env.user.id])],
            })
            _logger.info(f'  ✓ Task: {task_name}')
        except Exception as e:
            _logger.warning(f'  ⚠ Task {task_name}: {e}')
    env.cr.commit()
else:
    _logger.warning('  ⚠ Todo model not found')

# ========== 4. MANUFACTURING ORDERS + BOMs ==========
_logger.info('4. Creating manufacturing orders...')
if 'mrp.production' in env and 'mrp.bom' in env:
    MRP = env['mrp.production']
    BOM = env['mrp.bom']
    BOMLine = env['mrp.bom.line']
    products = env['product.product'].search([('type', '=', 'product')], limit=3)
    # Get picking type for manufacturing
    picking_type = env['stock.picking.type'].search([('code', '=', 'mrp_operation')], limit=1)
    stock_loc = env['stock.location'].search([('usage', '=', 'internal')], limit=1)
    if not picking_type or not stock_loc:
        _logger.warning('  ⚠ Stock picking type or location missing for manufacturing')
    else:
        for prod in products:
            try:
                # Create BOM
                bom = BOM.create({
                    'type': 'normal',
                    'product_tmpl_id': prod.product_tmpl_id.id,
                    'product_qty': 1,
                    'product_uom_id': prod.uom_id.id,
                    'ready_to_produce': 'asap',
                    'consumption': 'flexible',
                    'picking_type_id': picking_type.id,
                })
                # Add a component line
                component = env['product.product'].search([('id', '!=', prod.id)], limit=1)
                if component:
                    BOMLine.create({
                        'bom_id': bom.id,
                        'product_id': component.id,
                        'product_qty': 2,
                        'product_uom_id': component.uom_id.id,
                    })
                # Create Manufacturing Order
                mo = MRP.create({
                    'product_id': prod.id,
                    'product_qty': 20,
                    'product_uom_id': prod.uom_id.id,
                    'bom_id': bom.id,
                    'company_id': company.id,
                    'picking_type_id': picking_type.id,
                    'location_src_id': stock_loc.id,
                    'location_dest_id': stock_loc.id,
                    'date_start': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'consumption': 'flexible',
                })
                _logger.info(f'  ✓ MO: {mo.name} for {prod.name}')
            except Exception as e:
                _logger.warning(f'  ⚠ MO skip {prod.name}: {e}')
    env.cr.commit()
else:
    _logger.warning('  ⚠ Manufacturing models not available')

# ========== 5. MAINTENANCE REQUESTS ==========
_logger.info('5. Creating maintenance requests...')
if 'maintenance.request' in env:
    team = env['maintenance.team'].search([], limit=1)
    for i, title in enumerate(['General Office Maintenance', 'Server Room Cooling Check', 'Parking Lot Repair']):
        try:
            env['maintenance.request'].create({
                'name': title,
                'maintenance_type': 'preventive',
                'company_id': company.id,
                'kanban_state': 'normal',
                'maintenance_team_id': team.id if team else False,
                'request_date': f'2026-06-{i+1:02d}',
                'user_id': env.user.id,
            })
            _logger.info(f'  ✓ Maintenance: {title}')
        except Exception as e:
            _logger.warning(f'  ⚠ Maintenance skip: {e}')
    env.cr.commit()
else:
    _logger.warning('  ⚠ maintenance.request model not available')

# ========== 6. PURCHASE AGREEMENTS ==========
_logger.info('6. Creating purchase agreements...')
if 'purchase.requisition' in env:
    vendors = env['res.partner'].search([('supplier_rank', '>', 0)], limit=3)
    products = env['product.product'].search([('active', '=', True)], limit=2)
    if vendors and products:
        try:
            agreement = env['purchase.requisition'].create({
                'vendor_id': vendors[0].id,
                'type_id': env.ref('purchase.purchase_requisition_type').id,
                'ordering_date': '2026-05-05',
                'schedule_date': '2026-06-01',
                'line_ids': [(0,0,{
                    'product_id': products[0].id,
                    'product_qty': 100,
                    'price_unit': products[0].list_price,
                    'product_uom_id': products[0].uom_id.id,
                })],
            })
            _logger.info(f'  ✓ Purchase agreement: {agreement.name}')
        except Exception as e:
            _logger.warning(f'  ⚠ Agreement: {e}')
    env.cr.commit()
else:
    _logger.warning('  ⚠ purchase.requisition model not available')

_logger.info('✅ EXTENDED SEEDING COMPLETE!')
_logger.info('═══════════════════════════════════════════════════════')
_logger.info('Newly added data:')
_logger.info('  • Fixed Assets (6)')
_logger.info('  • Calendar events (10)')
_logger.info('  • Project tasks (10)')
_logger.info('  • Manufacturing Orders + BOMs')
_logger.info('  • Maintenance requests')
_logger.info('  • Purchase agreements')
_logger.info('═══════════════════════════════════════════════════════')
