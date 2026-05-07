from . import models
from . import controllers
from . import wizard


def post_init_hook(env):
    """Open the setup wizard the first time the module is installed."""
    ICP = env['ir.config_parameter'].sudo()
    if ICP.get_param('document_intelligence.setup_done') == 'True':
        return
    # Create a wizard instance and open it via a client action
    wizard_obj = env['document.intelligence.setup.wizard'].create({})
    action = env.ref('document_intelligence.action_document_intelligence_setup_wizard')
    action.sudo().write({'res_id': wizard_obj.id})
