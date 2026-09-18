# -*- coding: utf-8 -*-

from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    # ============================================================
    # INFORMACIÓN COMERCIAL DE OFERTA PARA EL POS
    #
    # El POS consulta este método al pulsar el botón "Oferta".
    #
    # La asignación se obtiene según:
    # - POS / Almacén
    # - Producto
    # - Condición comercial = Oferta
    # ============================================================

    @api.model
    def get_offer_sale_info(self, config_id, product_id):
        """
        Devuelve al POS la información necesaria para vender
        un producto utilizando el stock reservado para oferta.
        """

        config = self.browse(config_id).exists()
        product = self.env["product.product"].browse(product_id).exists()

        # --------------------------------------------------------
        # Validaciones básicas
        # --------------------------------------------------------
        if not config or not product:
            return {
                "available": False,
                "message": "No se pudo identificar el POS o el producto.",
            }

        picking_type = config.picking_type_id
        warehouse = picking_type.warehouse_id if picking_type else False

        if not warehouse:
            return {
                "available": False,
                "message": "El Punto de Venta no tiene un almacén configurado.",
            }

        # La consulta se ejecuta con sudo porque el cajero del POS
        # necesita conocer precio/cantidad de la oferta para vender,
        # pero NO debe tener acceso directo a su configuración.
        allocation = (
            self.env["dt.stock.commercial.allocation"]
            .sudo()
            .search(
                [
                    ("warehouse_id", "=", warehouse.id),
                    ("product_tmpl_id", "=", product.product_tmpl_id.id),
                    ("commercial_condition", "=", "offer"),
                    ("active", "=", True),
                ],
                limit=1,
            )
        )

        if not allocation:
            return {
                "available": False,
                "message": (
                    "Este producto no tiene una asignación "
                    "de stock en oferta para esta tienda."
                ),
            }

        # ============================================================
        # VALIDAR VIGENCIA DE LA OFERTA
        # ============================================================

        if allocation.use_validity:
            today = fields.Date.context_today(self)

            # Oferta todavía no iniciada
            if allocation.validity_date_from and today < allocation.validity_date_from:
                return {
                    "available": False,
                    "message": (
                        "Esta oferta todavía no está vigente. "
                        f"Inicia el {allocation.validity_date_from.strftime('%d/%m/%Y')}."
                    ),
                }

            # Oferta vencida
            if allocation.validity_date_to and today > allocation.validity_date_to:
                return {
                    "available": False,
                    "message": (
                        "Esta oferta ya finalizó. "
                        f"Venció el {allocation.validity_date_to.strftime('%d/%m/%Y')}."
                    ),
                }

        # --------------------------------------------------------
        # Debe existir cantidad disponible en oferta
        # --------------------------------------------------------
        if allocation.quantity <= 0:
            return {
                "available": False,
                "message": "Este producto no tiene stock disponible en oferta.",
            }

        # --------------------------------------------------------
        # Debe existir un precio comercial válido
        # --------------------------------------------------------
        if allocation.offer_price <= 0:
            return {
                "available": False,
                "message": "Este producto no tiene un precio de oferta configurado.",
            }

        # --------------------------------------------------------
        # Información que recibirá el POS
        # --------------------------------------------------------
        return {
            "available": True,
            "offer_price": allocation.offer_price,
            "offer_quantity": allocation.quantity,
            "warehouse_id": warehouse.id,
            "warehouse_name": warehouse.display_name,
            "product_name": product.display_name,
        }
