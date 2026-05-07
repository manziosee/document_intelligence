import logging
_logger = logging.getLogger(__name__)

env = env
_logger.info('=== ADDING QUOTATIONS & RFQs ===')

# Get some customers (is_company=True, customer_rank>0)
customers = env['res.partner'].search([('customer_rank', '>', 0)], limit=2)
vendors = env['res.partner'].search([('supplier_rank', '>', 0)], limit=2)
products = env['product.product'].search([('active', '=', True)], limit=4)

if customers and products:
    for i, cust in enumerate(customers):
        so = env['sale.order'].create({
            'partner_id': cust.id,
            'order_line': [(0,0,{
                'product_id': products[i % len(products)].id,
                'product_uom_qty': 2 + i,
                'price_unit': 100000 + i*50000,
            })],
        })
        _logger.info(f'✓ Quotation created: {so.name} for {cust.name}')

if vendors and products:
    for i, ven in enumerate(vendors):
        po = env['purchase.order'].create({
            'partner_id': ven.id,
            'order_line': [(0,0,{
                'product_id': products[i % len(products)].id,
                'product_qty': 5 + i*2,
                'price_unit': 80000 + i*20000,
            })],
        })
        _logger.info(f'✓ RFQ created: {po.name} from {ven.name}')

env.cr.commit()
_logger.info('✅ Quotations and RFQs added!')
