from odoo import models


class IrUiViewLabelPatch(models.Model):
    """
    Relax the label-validation rule so that <label class="o_form_label"> does
    not need a 'for' attribute.

    Odoo 17's _validate_tag_label() rejects any <label> that lacks 'for',
    even though the validator's own error message says to use
    class="o_form_label" as an alternative.  On older Odoo 17 Docker images
    the base_setup settings view ships with bare <label> elements; when any
    module inherits from that view and Odoo runs full validation on the merged
    arch, those bare labels cause an install/upgrade failure.

    This override implements the documented exemption: a <label> that carries
    class="o_form_label" is treated as a styled header, not a field label, and
    is exempt from the 'for' requirement.
    """
    _inherit = 'ir.ui.view'

    def _validate_tag_label(self, node, name_manager, node_info):
        cls = node.get('class') or ''
        if 'o_form_label' in cls:
            return
        super()._validate_tag_label(node, name_manager, node_info)
