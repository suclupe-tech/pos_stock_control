# -*- coding: utf-8 -*-

from odoo import fields, models

# ============================================================
# MOVIMIENTO DE STOCK
#
# Vincula el movimiento técnico SIN CLASIFICAR con la línea
# visible del POS que originó la venta por modelo.
#
# Ejemplo:
#
# Línea POS:
#   POLO TROPICAL x 20
#
# Movimiento físico:
#   POLO TROPICAL (SIN CLASIFICAR / SIN CLASIFICAR) x 20
# ============================================================


class StockMove(models.Model):
    _inherit = "stock.move"

    pos_model_order_line_id = fields.Many2one(
        "pos.order.line",
        string="Línea POS por modelo",
        copy=False,
        index=True,
        ondelete="set null",
    )


# ============================================================
# PICKING DEL POS
#
# ALMACÉN MIXTO:
#
# Venta Unidad
#   -> comportamiento estándar de Odoo
#   -> descuenta la variante real seleccionada.
#
# Venta Mayorista
#   -> una sola línea visible por modelo
#   -> un solo movimiento de la variante técnica SIN CLASIFICAR.
#
# No existe distribución de talla/color durante la venta.
# ============================================================


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _create_move_from_pos_order_lines(self, lines):
        self.ensure_one()

        warehouse = self.picking_type_id.warehouse_id

        # --------------------------------------------------------
        # MODO DE CONTROL DEL ALMACÉN
        #
        # MODEL:
        #   todas las salidas físicas trabajan por modelo.
        #
        # MIXED:
        #   solo las ventas Mayorista trabajan por modelo.
        #
        # Otros modos:
        #   conservan completamente el comportamiento estándar.
        # --------------------------------------------------------
        control_mode = (
            warehouse.product_control_mode
            if warehouse and "product_control_mode" in warehouse._fields
            else False
        )

        is_model_warehouse = control_mode == "model"
        is_mixed_warehouse = control_mode == "mixed"

        if not (is_model_warehouse or is_mixed_warehouse):
            return super()._create_move_from_pos_order_lines(lines)

        # --------------------------------------------------------
        # IDENTIFICAR LÍNEAS QUE TRABAJAN POR MODELO
        #
        # También reconocemos devoluciones de una venta mayorista
        # original, aunque la nueva orden de devolución no haya
        # conservado explícitamente el modo comercial.
        # --------------------------------------------------------
        model_lines = lines.filtered(
            lambda line: (
                # ----------------------------------------------------
                # ALMACÉN POR MODELO
                #
                # Todas las líneas físicas utilizan el producto
                # técnico SIN CLASIFICAR.
                # ----------------------------------------------------
                is_model_warehouse
                or
                # ----------------------------------------------------
                # ALMACÉN MIXTO
                #
                # Solo Mayorista trabaja contra SIN CLASIFICAR.
                # También reconocemos la devolución de una venta
                # Mayorista original.
                # ----------------------------------------------------
                (
                    is_mixed_warehouse
                    and (
                        line.order_id.commercial_operation_mode == "model"
                        or (
                            line.qty < 0
                            and line.refunded_orderline_id
                            and (
                                line.refunded_orderline_id.order_id.commercial_operation_mode
                                == "model"
                            )
                        )
                    )
                )
            )
        )

        standard_lines = lines - model_lines

        # ========================================================
        # VENTAS UNITARIAS / NORMALES
        # ========================================================
        if standard_lines:
            super()._create_move_from_pos_order_lines(standard_lines)

        if not model_lines:
            return

        # ========================================================
        # VENTAS POR MODELO / MAYORISTA
        #
        # Cada línea se convierte en un movimiento del producto
        # técnico SIN CLASIFICAR correspondiente al mismo modelo.
        # ========================================================
        move_vals = []

        for line in model_lines:

            stock_product = line.order_id._get_model_stock_product(line.product_id)

            # Partimos de los valores estándar preparados por Odoo.
            vals = self._prepare_stock_move_vals(
                line,
                line,
            )

            # Sustituimos únicamente el producto físico que
            # realmente debe descontarse del almacén.
            vals.update(
                {
                    "product_id": stock_product.id,
                    "product_uom": stock_product.uom_id.id,
                    "product_uom_qty": abs(line.qty),
                    "pos_model_order_line_id": line.id,
                }
            )

            # ----------------------------------------------------
            # DEVOLUCIÓN DE UNA VENTA MAYORISTA
            #
            # Si se devuelve una venta por modelo, buscamos el
            # movimiento original de SIN CLASIFICAR para conservar
            # correctamente la relación del retorno.
            # ----------------------------------------------------
            if line.qty < 0 and line.refunded_orderline_id:

                original_move = (
                    line.refunded_orderline_id.order_id.picking_ids.move_ids.filtered(
                        lambda move: (
                            move.product_id == stock_product
                            and move.state == "done"
                            and move.picking_type_id.code == "outgoing"
                        )
                    )[:1]
                )

                vals["origin_returned_move_id"] = (
                    original_move.id if original_move else False
                )

            move_vals.append(vals)

        if not move_vals:
            return

        # ========================================================
        # CREAR Y COMPLETAR LOS MOVIMIENTOS
        # ========================================================
        moves = self.env["stock.move"].create(move_vals)

        confirmed_moves = moves._action_confirm()

        # Odoo asigna las cantidades realizadas.
        confirmed_moves._add_mls_related_to_order(
            model_lines,
            are_qties_done=True,
        )

        confirmed_moves.picked = True

        # Mantener el comportamiento estándar para propietarios
        # y devoluciones.
        self._link_owner_on_return_picking(model_lines)
