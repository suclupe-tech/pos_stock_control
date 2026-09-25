# -*- coding: utf-8 -*-

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PosOrder(models.Model):
    _inherit = "pos.order"

    # ============================================================
    # CONTROL DE PROCESAMIENTO
    #
    # Evita que un reintento o resincronización del POS vuelva
    # a consumir el stock reservado para oferta.
    # ============================================================

    stock_control_processed = fields.Boolean(
        string="Control de stock procesado",
        default=False,
        copy=False,
    )

    # ============================================================
    # MODO COMERCIAL DE LA VENTA
    #
    # Se utiliza principalmente en almacenes MIXTOS.
    #
    # variant:
    # Venta unitaria. El stock se controla por talla/color.
    #
    # model:
    # Venta mayorista. El stock se controla agrupado por modelo.
    #
    # En almacenes configurados únicamente como MODEL o VARIANT
    # este campo puede permanecer vacío, porque el almacén ya
    # determina automáticamente su forma de trabajo.
    # ============================================================

    commercial_operation_mode = fields.Selection(
        [
            ("variant", "Venta unitaria"),
            ("model", "Venta mayorista"),
        ],
        string="Modo de venta",
        copy=False,
        help=(
            "Indica cómo debe controlar el stock una venta realizada "
            "desde un almacén configurado en modo mixto."
        ),
    )

    # ============================================================
    # OBTENER Y BLOQUEAR ASIGNACIÓN DE OFERTA
    #
    # FOR UPDATE evita que dos ventas simultáneas consuman
    # la misma cantidad reservada para oferta.
    # ============================================================

    def _get_locked_offer_allocation(self, warehouse, product_tmpl):
        self.ensure_one()

        self.env.cr.execute(
            """
            SELECT id, quantity
              FROM dt_stock_commercial_allocation
             WHERE warehouse_id = %s
               AND product_tmpl_id = %s
               AND commercial_condition = 'offer'
               AND active IS TRUE
             FOR UPDATE
            """,
            (
                warehouse.id,
                product_tmpl.id,
            ),
        )

        row = self.env.cr.fetchone()

        if not row:
            return False, 0.0

        # El POS necesita actualizar internamente la cantidad reservada
        # aunque el cajero no tenga acceso al backend de ofertas.
        allocation = self.env["dt.stock.commercial.allocation"].sudo().browse(row[0])

        allocated_qty = float(row[1] or 0.0)

        # Una oferta futura o vencida deja de reservar stock
        # para la venta regular.
        if self._get_offer_validity_error(allocation):
            allocated_qty = 0.0

        return allocation, allocated_qty

    # ============================================================
    # VALIDAR VIGENCIA DE UNA ASIGNACIÓN DE OFERTA
    # ============================================================

    def _get_offer_validity_error(self, allocation):
        """
        Devuelve un mensaje si la oferta todavía no inicia
        o ya venció. Si está vigente, devuelve False.
        """
        self.ensure_one()

        if not allocation or not allocation.use_validity:
            return False

        today = fields.Date.context_today(self)

        if allocation.validity_date_from and today < allocation.validity_date_from:
            return _(
                "La oferta todavía no está vigente.\n\n" "Fecha de inicio: %(date)s",
                date=allocation.validity_date_from.strftime("%d/%m/%Y"),
            )

        if allocation.validity_date_to and today > allocation.validity_date_to:
            return _(
                "La oferta ya finalizó.\n\n" "Fecha de fin: %(date)s",
                date=allocation.validity_date_to.strftime("%d/%m/%Y"),
            )

        return False

    # ============================================================
    # PRODUCTO DE STOCK PARA VENTA POR MODELO
    #
    # En un almacén MIXTO:
    #
    # - Venta unitaria:
    #   utiliza la variante real seleccionada.
    #
    # - Venta mayorista:
    #   utiliza la variante técnica SIN CLASIFICAR del modelo.
    #
    # La variante técnica ya es gestionada por dt_catalogo_comercial.
    # ============================================================

    def _get_model_stock_product(self, product):
        self.ensure_one()

        if not product:
            return product

        Product = self.env["product.product"]

        # --------------------------------------------------------
        # Validar que esté disponible la funcionalidad de
        # stock SIN CLASIFICAR.
        # --------------------------------------------------------
        if "is_unclassified_variant" not in Product._fields:
            raise UserError(
                _("No está disponible la configuración de stock " "SIN CLASIFICAR.")
            )

        template = product.product_tmpl_id

        # --------------------------------------------------------
        # Buscar la única variante técnica activa del modelo.
        # --------------------------------------------------------
        unclassified_variant = template.with_context(
            active_test=False
        ).product_variant_ids.filtered(
            lambda variant: (variant.active and variant.is_unclassified_variant)
        )

        # ============================================================
        # PRODUCTO YA MIGRADO
        #
        # Si existe exactamente una variante técnica,
        # esa es la que representa el stock por modelo.
        # ============================================================
        if len(unclassified_variant) == 1:
            return unclassified_variant

        # ============================================================
        # COMPATIBILIDAD CON PRODUCTOS ANTIGUOS
        #
        # Un producto que todavía no fue migrado y solamente tiene
        # una variante activa puede continuar vendiéndose temporalmente.
        # ============================================================
        active_variants = template.with_context(
            active_test=False
        ).product_variant_ids.filtered(lambda variant: variant.active)

        if not unclassified_variant and len(active_variants) == 1:
            return active_variants

        # ============================================================
        # PRODUCTO CON VARIANTES PERO SIN STOCK POR MODELO HABILITADO
        #
        # No podemos elegir una talla/color arbitrariamente.
        # Debe habilitarse primero su variante SIN CLASIFICAR.
        # ============================================================
        raise UserError(
            _(
                "El producto %(product)s tiene varias variantes pero no "
                "cuenta con una única variante técnica SIN CLASIFICAR.\n\n"
                "Primero utilice 'Habilitar stock por modelo' en el producto.",
                product=template.display_name,
            )
        )

    # ============================================================
    # VALIDACIÓN DE STOCK
    #
    # Devuelve también las asignaciones de oferta que deberán
    # descontarse después de que Odoo procese correctamente
    # la venta.
    # ============================================================

    def _validate_pos_commercial_stock(self):
        self.ensure_one()

        picking_type = self.config_id.picking_type_id

        if not picking_type:
            return []

        warehouse = picking_type.warehouse_id
        location = picking_type.default_location_src_id

        # Evitamos bloquear completamente el POS si existe
        # alguna configuración incompleta.
        if not warehouse or not location:
            return []

        Quant = self.env["stock.quant"]

        # Cantidades físicas solicitadas por variante.
        qty_by_product = defaultdict(float)

        # Cantidades comerciales por modelo.
        regular_by_template = defaultdict(float)
        offer_by_template = defaultdict(float)

        # Conservamos las líneas individuales de oferta porque
        # también debemos validar el precio de cada una.
        offer_lines_by_template = defaultdict(list)

        # ========================================================
        # AGRUPAR LAS LÍNEAS DE LA ORDEN
        # ========================================================

        for line in self.lines:
            product = line.product_id
            qty = line.qty or 0.0

            if not product or not product.is_storable:
                continue

            # Las devoluciones no deben bloquearse por este control.
            if qty <= 0:
                continue

            # ====================================================
            # DETERMINAR SI ESTA OPERACIÓN TRABAJA POR MODELO
            #
            # Almacén MODEL:
            #   siempre utiliza stock SIN CLASIFICAR.
            #
            # Almacén MIXED:
            #   solo utiliza SIN CLASIFICAR cuando la venta
            #   está marcada como Mayorista / Por modelo.
            #
            # Almacén VARIANT:
            #   utiliza la variante real seleccionada.
            # ====================================================

            is_model_operation = warehouse.product_control_mode == "model" or (
                warehouse.product_control_mode == "mixed"
                and self.commercial_operation_mode == "model"
            )

            if is_model_operation:
                stock_product = self._get_model_stock_product(product)
            else:
                stock_product = product

            qty_by_product[stock_product.id] += qty

            template_id = product.product_tmpl_id.id

            if line.is_offer_sale:
                offer_by_template[template_id] += qty
                offer_lines_by_template[template_id].append(line)
            else:
                regular_by_template[template_id] += qty

        # ========================================================
        # 1. VALIDACIÓN FÍSICA POR VARIANTE
        #
        # Tanto una venta regular como una venta en oferta
        # necesitan existir físicamente en el almacén.
        # ========================================================

        products = self.env["product.product"].browse(list(qty_by_product.keys()))

        for product in products:
            requested_qty = qty_by_product[product.id]

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
                        "Stock físico insuficiente.\n\n"
                        "Producto: %(product)s\n"
                        "Solicitado: %(requested).2f\n"
                        "Disponible: %(available).2f",
                        product=product.display_name,
                        requested=requested_qty,
                        available=available_qty,
                    )
                )

        # ========================================================
        # 2. VALIDACIÓN COMERCIAL POR MODELO
        # ========================================================

        template_ids = set(regular_by_template.keys()) | set(offer_by_template.keys())

        templates = self.env["product.template"].browse(list(template_ids))

        offer_consumptions = []

        for template in templates:
            regular_requested = regular_by_template[template.id]
            offer_requested = offer_by_template[template.id]

            # Stock físico disponible de todas las variantes
            # de este modelo en la ubicación real del POS.
            total_available = 0.0

            for variant in template.product_variant_ids:
                total_available += Quant._get_available_quantity(
                    variant,
                    location,
                )

            # Obtenemos y bloqueamos la asignación comercial.
            allocation, allocated_offer_qty = self._get_locked_offer_allocation(
                warehouse,
                template,
            )

            # ----------------------------------------------------
            # VENTA EN OFERTA
            # ----------------------------------------------------

            if offer_requested > 0:
                if not allocation:

                    # ------------------------------------------------
                    # VALIDAR VIGENCIA EN BACKEND
                    # ------------------------------------------------
                    validity_error = self._get_offer_validity_error(allocation)

                    if validity_error:
                        raise UserError(
                            _(
                                "%(message)s\n\nProducto: %(product)s",
                                message=validity_error,
                                product=template.display_name,
                            )
                        )

                    # ====================================================
                    # VALIDAR PRECIO OFICIAL DE OFERTA
                    # ====================================================

                    currency_rounding = allocation.currency_id.rounding or 0.01

                    if (
                        float_compare(
                            allocation.offer_price,
                            0.0,
                            precision_rounding=currency_rounding,
                        )
                        <= 0
                    ):
                        raise UserError(
                            _(
                                "El producto no tiene un precio de oferta válido.\n\n"
                                "Producto: %(product)s",
                                product=template.display_name,
                            )
                        )

                    for offer_line in offer_lines_by_template[template.id]:

                        # El precio recibido desde el POS debe coincidir
                        # exactamente con el precio configurado en backend.
                        if (
                            float_compare(
                                offer_line.price_unit,
                                allocation.offer_price,
                                precision_rounding=currency_rounding,
                            )
                            != 0
                        ):
                            raise UserError(
                                _(
                                    "El precio de oferta no coincide con el precio "
                                    "configurado.\n\n"
                                    "Producto: %(product)s\n"
                                    "Precio recibido: S/ %(received).2f\n"
                                    "Precio oficial de oferta: S/ %(official).2f",
                                    product=offer_line.product_id.display_name,
                                    received=offer_line.price_unit,
                                    official=allocation.offer_price,
                                )
                            )

                        # Una oferta ya tiene un precio comercial especial,
                        # por lo que no permitimos otro descuento sobre la línea.
                        if offer_line.discount:
                            raise UserError(
                                _(
                                    "No se puede aplicar un descuento adicional "
                                    "a una venta en oferta.\n\n"
                                    "Producto: %(product)s\n"
                                    "Precio de oferta: S/ %(price).2f",
                                    product=offer_line.product_id.display_name,
                                    price=allocation.offer_price,
                                )
                            )

                    raise UserError(
                        _(
                            "Este producto no tiene stock asignado "
                            "para oferta.\n\n"
                            "Producto: %(product)s",
                            product=template.display_name,
                        )
                    )

                if (
                    float_compare(
                        offer_requested,
                        allocated_offer_qty,
                        precision_rounding=template.uom_id.rounding,
                    )
                    > 0
                ):
                    raise UserError(
                        _(
                            "Stock de oferta insuficiente.\n\n"
                            "Producto: %(product)s\n"
                            "Solicitado como oferta: %(requested).2f\n"
                            "Disponible en oferta: %(available).2f",
                            product=template.display_name,
                            requested=offer_requested,
                            available=allocated_offer_qty,
                        )
                    )

                offer_consumptions.append(
                    (
                        allocation,
                        allocated_offer_qty,
                        offer_requested,
                    )
                )

            # ----------------------------------------------------
            # VENTA REGULAR
            # ----------------------------------------------------

            regular_available = max(
                total_available - allocated_offer_qty,
                0.0,
            )

            if (
                float_compare(
                    regular_requested,
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
                        requested=regular_requested,
                        available=regular_available,
                        offer=allocated_offer_qty,
                    )
                )

        return offer_consumptions

    # ============================================================
    # PROCESAMIENTO FINAL DE LA ORDEN
    # ============================================================

    def _process_saved_order(self, draft):
        self.ensure_one()

        # Órdenes borrador continúan con el comportamiento
        # estándar de Odoo.
        if draft:
            return super()._process_saved_order(draft)

        # Una orden ya procesada no debe volver a consumir oferta
        # si el POS la sincroniza nuevamente.
        if self.stock_control_processed:
            return super()._process_saved_order(draft)

        # Validamos ANTES de que Odoo genere el movimiento físico.
        offer_consumptions = self._validate_pos_commercial_stock()

        # Odoo procesa pago, picking, stock, factura, etc.
        result = super()._process_saved_order(draft)

        # ========================================================
        # DESCONTAR STOCK RESERVADO PARA OFERTA
        #
        # Llegamos aquí únicamente si Odoo procesó correctamente
        # la venta.
        # ========================================================

        for allocation, current_qty, sold_qty in offer_consumptions:
            new_qty = max(
                current_qty - sold_qty,
                0.0,
            )

            allocation.write(
                {
                    "quantity": new_qty,
                }
            )

        # Marcamos la orden para garantizar idempotencia.
        self.stock_control_processed = True

        return result


# ============================================================
# LÍNEA DE PEDIDO POS
#
# Identifica si la línea corresponde a:
# - Venta regular
# - Venta en oferta
# ============================================================


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    is_offer_sale = fields.Boolean(
        string="Venta en oferta",
        default=False,
        copy=False,
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_to_load = super()._load_pos_data_fields(config)

        if "is_offer_sale" not in fields_to_load:
            fields_to_load.append("is_offer_sale")

        return fields_to_load

    # ============================================================
    # MOVIMIENTOS DE STOCK PARA VENTA POR MODELO
    #
    # En una venta Mayorista la línea visible del POS conserva
    # el producto/modelo seleccionado por el vendedor, pero el
    # movimiento físico utiliza la variante técnica SIN CLASIFICAR.
    #
    # Esta relación permite que Odoo encuentre el movimiento real
    # para calcular correctamente el costo de la línea.
    # ============================================================

    def _get_stock_moves_to_consider(self, stock_moves, product):
        self.ensure_one()

        # --------------------------------------------------------
        # Venta Mayorista normal.
        # --------------------------------------------------------
        is_model_sale = self.order_id.commercial_operation_mode == "model"

        # --------------------------------------------------------
        # Devolución de una venta Mayorista.
        #
        # La orden de devolución podría no conservar explícitamente
        # el modo comercial, por eso verificamos la línea original.
        # --------------------------------------------------------
        is_model_refund = (
            self.refunded_orderline_id
            and self.refunded_orderline_id.order_id.commercial_operation_mode == "model"
        )

        if is_model_sale or is_model_refund:

            model_moves = stock_moves.filtered(
                lambda move: move.pos_model_order_line_id == self
            )

            if model_moves:
                return model_moves

        # Unidad, Oferta y demás ventas continúan usando Odoo estándar.
        return super()._get_stock_moves_to_consider(
            stock_moves,
            product,
        )
