from . import models
from . import controllers
from . import wizard


def pre_init_hook(env):
    """
    Patch bare <label> tags in base_setup.res_config_settings_view_form
    BEFORE our data files (and our settings view) are loaded.

    Odoo 17 passes `env` (not `cr`) to pre_init_hook.  Some older Odoo 17
    Docker images ship the base_setup settings view with <label> elements that
    have neither a 'for' attribute nor 'o_form_label' in their class.  When our
    module inherits from the view, Odoo re-validates the merged result and
    raises "Label tag must contain a 'for'".

    Running this as a pre_init_hook guarantees:
    - Runs before any of our XML data files are processed
    - Never raises an error (bare except keeps the install going)
    - Harmless if the parent view already has no bare labels (changed = False)
    """
    try:
        import json
        from lxml import etree
        cr = env.cr

        # Locate the base_setup settings view via ir_model_data
        cr.execute("""
            SELECT v.id, v.arch_db
            FROM ir_ui_view v
            JOIN ir_model_data d
              ON d.res_id = v.id AND d.model = 'ir.ui.view'
            WHERE d.module = 'base_setup'
              AND d.name   = 'res_config_settings_view_form'
        """)
        row = cr.fetchone()
        if not row:
            return

        view_id, arch_raw = row
        if not arch_raw:
            return

        # arch_db is stored as plain XML or as JSON (translated fields)
        arch_xml = arch_raw
        arch_data = None
        try:
            arch_data = json.loads(arch_raw)
            if isinstance(arch_data, dict):
                arch_xml = arch_data.get('en_US') or next(iter(arch_data.values()), '')
        except (json.JSONDecodeError, TypeError, StopIteration):
            arch_data = None  # plain XML

        if not arch_xml:
            return

        tree = etree.fromstring(arch_xml.encode('utf-8'))
        changed = 0

        for label in tree.xpath('//label'):
            if label.get('for'):
                continue
            cls = label.get('class') or ''
            if 'o_form_label' not in cls:
                label.set('class', (cls + ' o_form_label').strip())
                changed += 1

        if not changed:
            return  # nothing to fix — modern Odoo 17 build

        new_xml = etree.tostring(tree, encoding='unicode')

        if arch_data is not None:
            # Patch every language variant
            new_arch = json.dumps({k: new_xml for k in arch_data})
        else:
            new_arch = new_xml

        cr.execute(
            "UPDATE ir_ui_view SET arch_db = %s WHERE id = %s",
            (new_arch, view_id),
        )
        # Flush ORM cache so the patched arch is read on next access
        env['ir.ui.view'].browse(view_id).invalidate_recordset()

        import logging
        logging.getLogger(__name__).info(
            'document_intelligence pre_init_hook: patched %d bare label(s) in '
            'base_setup.res_config_settings_view_form', changed,
        )

    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            'document_intelligence pre_init_hook: label patch failed (non-fatal, '
            'install will continue)', exc_info=True,
        )


def post_init_hook(env):
    """Open the setup wizard the first time the module is installed."""
    ICP = env['ir.config_parameter'].sudo()
    if ICP.get_param('document_intelligence.setup_done') == 'True':
        return
    wizard_obj = env['document.intelligence.setup.wizard'].create({})
    action = env.ref('document_intelligence.action_document_intelligence_setup_wizard')
    action.sudo().write({'res_id': wizard_obj.id})
