# -*- coding: utf-8 -*-

from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"
    # ============================================================
    # MODO DE CONTROL DE STOCK DEL ALMACÉN DEL POS
    #
    # Permite que el frontend conozca si el almacén trabaja:
    # - por modelo
    # - por variante
    # - en modo mixto
    #
    # Es solo informativo para el POS; la configuración real
    # continúa perteneciendo al almacén.
    # ============================================================

    warehouse_product_control_mode = fields.Selection(
        related="picking_type_id.warehouse_id.product_control_mode",
        string="Modo de control de stock",
        readonly=True,
    )

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

    # ============================================================
    # STOCK DISPONIBLE PARA VENTA POS
    #
    # Consulta el producto físico que realmente utiliza el POS:
    #
    # MODEL:
    #   variante técnica SIN CLASIFICAR.
    #
    # MIXED + Mayorista:
    #   variante técnica SIN CLASIFICAR.
    #
    # MIXED + Unidad / VARIANT:
    #   variante real seleccionada.
    #
    # Este método se utiliza para advertir al vendedor antes
    # de agregar o aumentar una cantidad en el carrito.
    # ============================================================

    @api.model
    def get_pos_sale_stock_info(
        self,
        config_id,
        product_id,
        operation_mode=False,
    ):
        config = self.browse(config_id).exists()
        product = self.env["product.product"].browse(product_id).exists()

        if not config or not product:
            return {
                "available": False,
                "message": "No se pudo identificar el POS o el producto.",
            }

        picking_type = config.picking_type_id
        warehouse = picking_type.warehouse_id if picking_type else False
        location = picking_type.default_location_src_id if picking_type else False

        if not warehouse or not location:
            return {
                "available": False,
                "message": (
                    "El Punto de Venta no tiene almacén "
                    "o ubicación de stock configurada."
                ),
            }

        # Los productos que no manejan existencias
        # no necesitan esta validación.
        if not product.is_storable:
            return {
                "available": True,
                "track_stock": False,
                "available_qty": 0.0,
                "product_name": product.display_name,
            }

        control_mode = (
            warehouse.product_control_mode
            if "product_control_mode" in warehouse._fields
            else False
        )

        is_model_operation = control_mode == "model" or (
            control_mode == "mixed" and operation_mode == "model"
        )

        stock_product = product

        # ========================================================
        # OPERACIÓN POR MODELO
        # ========================================================
        if is_model_operation:

            template = product.product_tmpl_id

            technical_variants = template.with_context(
                active_test=False
            ).product_variant_ids.filtered(
                lambda variant: (variant.active and variant.is_unclassified_variant)
            )

            # Producto ya migrado.
            if len(technical_variants) == 1:
                stock_product = technical_variants

            else:
                active_variants = template.with_context(
                    active_test=False
                ).product_variant_ids.filtered(lambda variant: variant.active)

                # Compatibilidad temporal con producto antiguo
                # que todavía posee una sola variante.
                if not technical_variants and len(active_variants) == 1:
                    stock_product = active_variants

                else:
                    return {
                        "available": False,
                        "message": (
                            f"El producto {template.display_name} "
                            "no está preparado para trabajar por modelo. "
                            "Primero habilite su stock por modelo."
                        ),
                    }

        # ========================================================
        # STOCK REAL DISPONIBLE
        # ========================================================
        available_qty = (
            self.env["stock.quant"]
            .sudo()
            ._get_available_quantity(
                stock_product,
                location,
            )
        )

        return {
            "available": available_qty > 0,
            "track_stock": True,
            "available_qty": available_qty,
            "product_name": product.product_tmpl_id.display_name,
            "warehouse_name": warehouse.display_name,
        }

    # ============================================================
    # SELECTOR UNIDAD / MAYORISTA
    #
    # Permite decidir por cada Punto de Venta si el cajero podrá
    # elegir entre venta por variante y venta por modelo.
    #
    # Ejemplos actuales:
    # - TDA DIGITAL: activado
    # - HUANUCO: desactivado
    # - GAMARRA: desactivado
    # - MONARCA: desactivado
    #
    # Si otra tienda empieza a trabajar por variantes en el futuro,
    # bastará habilitar esta opción y configurar su almacén.
    # ============================================================

    enable_commercial_mode_selector = fields.Boolean(
        string="Habilitar Unidad / Mayorista",
        default=False,
        help=(
            "Muestra en el Punto de Venta el selector para alternar "
            "entre venta unitaria por variantes y venta mayorista "
            "por modelo."
        ),
    )
