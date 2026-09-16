# -*- coding: utf-8 -*-

from collections import defaultdict

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PosOrder(models.Model):
    _inherit = "pos.order"

    # ============================================================
    # CONTROL DE STOCK PARA VENTAS POS
    #
    # Objetivos:
    # - No permitir vender más stock del disponible físicamente.
    # - Respetar el stock separado para OFERTA.
    # - No bloquear órdenes borrador.
    # - No bloquear devoluciones.
    # - Sumar correctamente líneas repetidas del mismo producto.
    # ============================================================

    def _process_order(self, order, existing_order):
        # --------------------------------------------------------
        # Los pedidos en borrador no deben bloquear la operación
        # normal del POS.
        # --------------------------------------------------------
        if order.get("state") == "draft":
            return super()._process_order(order, existing_order)

        session = self.env["pos.session"].browse(order.get("session_id"))

        if not session.exists():
            return super()._process_order(order, existing_order)

        picking_type = session.config_id.picking_type_id

        # Ubicación REAL desde la cual este POS entrega mercancía.
        location = picking_type.default_location_src_id

        # Almacén relacionado con el tipo de operación del POS.
        warehouse = picking_type.warehouse_id

        # --------------------------------------------------------
        # Si la configuración del POS no tiene almacén o ubicación,
        # no bloqueamos todo el POS.
        #
        # Así evitamos repetir el problema del módulo anterior.
        # --------------------------------------------------------
        if not location or not warehouse:
            return super()._process_order(order, existing_order)

        Quant = self.env["stock.quant"]
        Product = self.env["product.product"]

        # Cantidad solicitada por variante.
        qty_by_product = defaultdict(float)

        # Cantidad solicitada por producto maestro/modelo.
        qty_by_template = defaultdict(float)

        products = {}

        # ========================================================
        # LEER LÍNEAS DE LA VENTA
        # ========================================================
        for line in order.get("lines", []):
            # Odoo envía las líneas como comandos ORM:
            # [comando, id, valores]
            if not isinstance(line, (list, tuple)) or len(line) < 3:
                continue

            line_data = line[2] or {}

            product_id = line_data.get("product_id")
            qty = line_data.get("qty", 0.0) or 0.0

            if not product_id:
                continue

            product = Product.browse(product_id)

            if not product.exists():
                continue

            # Solo controlamos productos almacenables.
            if not product.is_storable:
                continue

            # Las cantidades negativas corresponden a devoluciones.
            # Una devolución no debe bloquearse por falta de stock.
            if qty <= 0:
                continue

            products[product.id] = product

            qty_by_product[product.id] += qty
            qty_by_template[product.product_tmpl_id.id] += qty

        # ========================================================
        # 1. VALIDACIÓN DE STOCK FÍSICO POR VARIANTE
        # ========================================================
        for product_id, requested_qty in qty_by_product.items():
            product = products[product_id]

            available_qty = Quant._get_available_quantity(
                product,
                location,
            )

            if (
                float_compare(
                    requested_qty,
                    available_qty,
                    precision_rounding=product.uom_id.rounding,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "Stock insuficiente para realizar la venta.\n\n"
                        "Producto: %(product)s\n"
                        "Solicitado: %(requested).2f\n"
                        "Disponible: %(available).2f",
                        product=product.display_name,
                        requested=requested_qty,
                        available=available_qty,
                    )
                )

        # ========================================================
        # 2. VALIDACIÓN DEL STOCK PARA VENTA REGULAR
        #
        # Stock regular =
        # Stock disponible - Stock separado para OFERTA
        # ========================================================
        templates = self.env["product.template"].browse(list(qty_by_template.keys()))

        for template in templates:
            requested_qty = qty_by_template[template.id]

            # Stock disponible de todas las variantes en la
            # ubicación de este POS.
            total_available = 0.0

            for variant in template.product_variant_ids:
                total_available += Quant._get_available_quantity(
                    variant,
                    location,
                )

            # Cantidad reservada comercialmente para OFERTA.
            offer_qty = warehouse._get_offer_allocated_quantity(template)

            regular_available = max(
                total_available - offer_qty,
                0.0,
            )

            if (
                float_compare(
                    requested_qty,
                    regular_available,
                    precision_rounding=template.uom_id.rounding,
                )
                > 0
            ):
                raise UserError(
                    _(
                        "Stock insuficiente para venta regular.\n\n"
                        "Producto: %(product)s\n"
                        "Solicitado: %(requested).2f\n"
                        "Disponible para venta regular: %(available).2f\n"
                        "Separado para oferta: %(offer).2f",
                        product=template.display_name,
                        requested=requested_qty,
                        available=regular_available,
                        offer=offer_qty,
                    )
                )

        # ========================================================
        # TODO CORRECTO:
        # Odoo continúa con su procesamiento normal de la venta.
        # ========================================================
        return super()._process_order(order, existing_order)
