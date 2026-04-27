from odoo import models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _process_order(self, order, draft):
        data = order.get("data", order)

        session = self.env["pos.session"].browse(data["session_id"])

        warehouse = session.config_id.picking_type_id.warehouse_id
        location = warehouse.lot_stock_id

        for line in data.get("lines", []):

            line_data = line[2]

            product = self.env["product.product"].browse(line_data["product_id"])

            qty = line_data.get("qty", 0)

            if not product.is_storable:
                continue

            disponible = self.env["stock.quant"]._get_available_quantity(
                product, location
            )

            # bloqueo definitivo
            if disponible <= 0 or qty > disponible:
                raise UserError(
                    "No hay stock disponible para vender:\n\n"
                    f"{product.display_name}\n"
                    f"Disponible: {disponible}"
                )

        return super()._process_order(order, draft)
