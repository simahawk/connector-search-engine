# -*- coding: utf-8 -*-
# Copyright 2013 Akretion (http://www.akretion.com)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models, _
from odoo.addons.queue_job.job import job
import logging
_logger = logging.getLogger(__name__)
try:
    from unidecode import unidecode
except ImportError:
    _logger.debug('Cannot `import unidecode`.')


def sanitize(name):
    return unidecode(
        name.replace(' ', '_').replace('.', '_').replace('-', '_').lower())


class SeIndex(models.Model):

    _name = 'se.index'
    _description = 'Se Index'

    @api.model
    def _get_model_domain(self):
        models = self.env['ir.model'].search([('transient', '=', False)])
        se_model_ids = []
        for model in models:
            if model.model == 'se.binding':
                continue
            if hasattr(self.env[model.model], '_se_model'):
                se_model_ids.append(model.id)
        return [('id', 'in', se_model_ids)]

    name = fields.Char(compute='_compute_name', store=True)
    backend_id = fields.Many2one(
        'se.backend',
        string='Backend',
        required=True)
    lang_id = fields.Many2one(
        'res.lang',
        string='Lang',
        required=True)
    model_id = fields.Many2one(
        'ir.model',
        string='Model',
        required=True,
        domain=_get_model_domain)
    exporter_id = fields.Many2one(
        'ir.exports',
        string='Exporter')
    batch_size = fields.Integer(
        default=5000,
        help='Batch size for exporting element')

    _sql_constraints = [
        ('lang_model_uniq', 'unique(backend_id, lang_id, model_id)',
         'Lang and model of index must be uniq per backend.'),
    ]

    @api.onchange('model_id')
    def onchange_model_id(self):
        self.exporter_id = False
        if self.model_id:
            domain = [('resource', '=', self.model_id.model)]
            return {'domain': {'exporter_id': domain}}

    @api.model
    def recompute_all_index(self, domain=None):
        if domain is None:
            domain = []
        return self.search(domain).recompute_all_binding()

    def force_recompute_all_binding(self):
        return self.recompute_all_binding(force_export=True)

    def recompute_all_binding(self, force_export=False):
        for record in self:
            binding_obj = self.env[record.model_id.model]
            for bindings in binding_obj.search([('index_id', '=', record.id)]):
                bindings._jobify_recompute_json(force_export=force_export)
        return True

    @api.depends('lang_id', 'model_id', 'backend_id.name')
    def _compute_name(self):
        for rec in self:
            if rec.lang_id and rec.model_id and rec.backend_id.name:
                rec.name = '%s_%s_%s' % (
                    sanitize(rec.backend_id.name),
                    sanitize(rec.model_id.name or ''),
                    rec.lang_id.code)

    def force_batch_export(self):
        self.ensure_one()
        bindings = self.env[self.model_id.model].search([
            ('index_id', '=', self.id)])
        bindings.write({'sync_state': 'to_update'})
        self._jobify_batch_export()

    def _jobify_batch_export(self):
        self.ensure_one()
        description = _("Prepare a batch export of index '%s'") % self.name
        self.with_delay(description=description).batch_export()

    @api.model
    def generate_batch_export_per_index(self, domain=None):
        if domain is None:
            domain = []
        for record in self.search(domain):
            record._jobify_batch_export()
        return True

    def _get_domain_for_exporting_binding(self):
        return [('index_id', '=', self.id), ('sync_state', '=', 'to_update')]

    @job(default_channel='root.search_engine.prepare_batch_export')
    def batch_export(self):
        self.ensure_one()
        domain = self._get_domain_for_exporting_binding()
        bindings = self.env[self.model_id.model].search(domain)
        bindings_count = len(bindings)
        while bindings:
            processing = bindings[0:self.batch_size]
            bindings = bindings[self.batch_size:]
            description = _(
                "Export %d records of %d for index '%s'") % (
                    len(processing),
                    bindings_count,
                    self.name)
            processing.with_delay(description=description).export()
            processing.with_context(connector_no_export=True).write({
                'sync_state': 'scheduled',
            })
        return True

    def clear_index(self):
        self.ensure_one()
        backend = self.backend_id.specific_backend
        with backend.work_on(self._name, index=self) as work:
            adapter = work.component(usage='se.backend.adapter')
            adapter.clear()
        return True
