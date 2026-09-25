/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PosStore } from "@point_of_sale/app/services/pos_store";


patch(PosOrder.prototype, {

    // ============================================================
    // MODO COMERCIAL DEL PEDIDO
    //
    // Solo aplica cuando el almacén del POS trabaja en modo MIXTO.
    //
    // variant = Venta unitaria
    //           Control por talla/color.
    //
    // model   = Venta mayorista
    //           Control agrupado por modelo.
    //
    // Toda orden nueva de TDA DIGITAL inicia como venta unitaria.
    // ============================================================

    setup(vals) {
        super.setup(vals);

        // ============================================================
        // MODO AUTOMÁTICO SEGÚN EL ALMACÉN
        //
        // MIXED:
        //   inicia como venta unitaria por variantes.
        //
        // MODEL:
        //   siempre trabaja como venta por modelo.
        //   La vendedora no necesita elegir talla/color.
        // ============================================================

        const warehouseMode =
            this.config_id?.warehouse_product_control_mode;

        // ============================================================
        // ALMACÉN POR MODELO
        //
        // Siempre trabaja por modelo, incluso si el POS recupera
        // una orden antigua con otro modo comercial.
        // ============================================================
        if (warehouseMode === "model") {
            this.commercial_operation_mode = "model";
        }

        // ============================================================
        // ALMACÉN MIXTO
        //
        // Solo asignamos "variant" como valor inicial.
        // Después el usuario puede cambiar entre Unidad y Mayorista.
        // ============================================================
        else if (
            warehouseMode === "mixed" &&
            !this.commercial_operation_mode
        ) {
            this.commercial_operation_mode = "variant";
        }
    },
});


// ============================================================
// SELECTOR UNIDAD / MAYORISTA
// ============================================================

patch(ControlButtons.prototype, {

    toggleCommercialOperationMode() {
        const order = this.pos.getOrder();

        if (!order) {
            return;
        }

        // Solo aplica al POS cuyo almacén trabaja en modo MIXTO.
        if (
            this.pos.config.warehouse_product_control_mode !== "mixed"
        ) {
            return;
        }

        // El modo debe elegirse antes de agregar productos.
        if (order.lines?.length) {
            this.dialog.add(AlertDialog, {
                title: "Modo de venta",
                body:
                    "Seleccione Venta unitaria o Venta mayorista " +
                    "antes de agregar productos al pedido.",
            });
            return;
        }

        // Alternar entre Unidad y Mayorista.
        order.commercial_operation_mode =
            order.commercial_operation_mode === "model"
                ? "variant"
                : "model";
    },
});


// ============================================================
// AGREGAR PRODUCTOS SEGÚN EL MODO DE VENTA
//
// UNIDAD:
// - Mantiene el comportamiento normal de Odoo.
// - Si corresponde, permite seleccionar talla/color.
//
// MAYORISTA:
// - No abre el configurador de variantes.
// - No abre ninguna matriz de distribución.
// - Agrega una sola línea por modelo.
//
// El backend será responsable de descontar el stock de la
// variante técnica SIN CLASIFICAR.
// ============================================================

patch(PosStore.prototype, {

    async addLineToCurrentOrder(vals, opts = {}, configure = true) {

        let order = this.getOrder();

        if (!order) {
            order = this.addNewOrder();
        }

        const warehouseMode =
            this.config.warehouse_product_control_mode;

        const isModelOperation =
            warehouseMode === "model" ||
            (
                warehouseMode === "mixed" &&
                order.commercial_operation_mode === "model"
            );

        // ========================================================
        // VENTA UNITARIA
        //
        // Aquí Odoo todavía debe permitir seleccionar la variante
        // exacta de talla/color antes de conocer qué producto físico
        // se venderá.
        // ========================================================
        if (!isModelOperation) {
            return await super.addLineToCurrentOrder(
                vals,
                opts,
                configure
            );
        }

        // ========================================================
        // VENTA POR MODELO
        //
        // Antes de agregar la línea consultamos automáticamente
        // el stock de la variante técnica SIN CLASIFICAR.
        //
        // La vendedora no necesita hacer ninguna consulta manual.
        // ========================================================

        // ========================================================
        // DEVOLUCIONES
        //
        // Una devolución incrementa el stock, por lo que no debe
        // bloquearse por falta de existencias disponibles.
        //
        // Odoo puede indicar la devolución mediante una cantidad
        // negativa o mediante el preset de devolución de la orden.
        // ========================================================
        const isReturnOperation =
            Number(vals.qty || 0) < 0 ||
            Boolean(vals.refunded_orderline_id);

        if (isReturnOperation) {
            return await super.addLineToCurrentOrder(
                vals,
                opts,
                false
            );
        }

        try {

            // ----------------------------------------------------
            // IDENTIFICAR PRODUCTO / MODELO
            //
            // Al pulsar una tarjeta, Odoo normalmente envía
            // product_tmpl_id pero no product_id.
            //
            // Tomamos una variante del modelo únicamente como
            // referencia para consultar el backend. Si la operación
            // es por modelo, el backend buscará SIN CLASIFICAR.
            // ----------------------------------------------------
            let productTemplate = vals.product_tmpl_id;

            if (typeof productTemplate === "number") {
                productTemplate =
                    this.data.models["product.template"].get(productTemplate);
            }

            const product =
                vals.product_id ||
                productTemplate?.product_variant_ids?.[0];

            if (!product?.id) {

                this.dialog.add(AlertDialog, {
                    title: "Producto no disponible",
                    body:
                        "No se pudo identificar el producto para consultar su stock.",
                });

                return;
            }

            const stockInfo = await this.data.call(
                "pos.config",
                "get_pos_sale_stock_info",
                [
                    this.config.id,
                    product.id,
                    order.commercial_operation_mode || false,
                ]
            );

            // ----------------------------------------------------
            // Producto sin control de existencias
            // ----------------------------------------------------
            if (stockInfo.track_stock === false) {
                return await super.addLineToCurrentOrder(
                    vals,
                    opts,
                    false
                );
            }

            // ----------------------------------------------------
            // No existe stock o el producto no está preparado
            // correctamente para trabajar por modelo.
            // ----------------------------------------------------
            if (!stockInfo.available) {

                this.dialog.add(AlertDialog, {
                    title: "Sin stock disponible",
                    body:
                        stockInfo.message ||
                        `${stockInfo.product_name || "El producto"} ` +
                        "no tiene stock disponible en esta tienda.",
                });

                return;
            }

            const availableQty =
                Number(stockInfo.available_qty || 0);

            // ====================================================
            // CANTIDAD DEL MISMO MODELO YA AGREGADA AL CARRITO
            //
            // Esto también protege el caso poco frecuente en que
            // se pulse varias veces el mismo producto.
            // ====================================================
            let quantityInOrder = 0;

            const templateId =
                product.product_tmpl_id?.id;

            if (templateId) {
                for (const line of order.lines || []) {

                    if (
                        line.product_id?.product_tmpl_id?.id === templateId &&
                        Number(line.qty || 0) > 0
                    ) {
                        quantityInOrder += Number(line.qty || 0);
                    }
                }
            }

            const requestedQty =
                quantityInOrder + Number(vals.qty || 1);

            // ----------------------------------------------------
            // No permitir superar el stock real disponible.
            // ----------------------------------------------------
            if (requestedQty > availableQty) {

                this.dialog.add(AlertDialog, {
                    title: "Stock insuficiente",
                    body:
                        `Está intentando agregar ${requestedQty} unidad(es), ` +
                        `pero solo hay ${availableQty} unidad(es) disponibles.`,
                });

                return;
            }

        } catch (error) {

            console.error(
                "Error al validar stock antes de agregar producto:",
                error
            );

            this.dialog.add(AlertDialog, {
                title: "Error al validar stock",
                body:
                    "No se pudo comprobar el stock disponible. " +
                    "Inténtelo nuevamente.",
            });

            return;
        }

        // ========================================================
        // STOCK DISPONIBLE
        //
        // Agregar una sola línea por modelo y no abrir
        // configurador de talla/color.
        // ========================================================
        return await super.addLineToCurrentOrder(
            vals,
            opts,
            false
        );
    },
});