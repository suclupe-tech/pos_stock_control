/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { OrderSummary } from "@point_of_sale/app/screens/product_screen/order_summary/order_summary";


patch(ControlButtons.prototype, {

    // ============================================================
    // ACTIVAR / DESACTIVAR VENTA EN OFERTA
    //
    // Cuando se activa:
    // 1. Consulta al servidor el precio de oferta.
    // 2. Guarda temporalmente el precio normal.
    // 3. Aplica el precio de oferta.
    // 4. Marca la línea como venta en oferta.
    //
    // Cuando se desactiva:
    // 1. Restaura el precio que tenía originalmente.
    // 2. Restaura el tipo de precio.
    // 3. Quita la marca de oferta.
    // ============================================================
    async toggleOfferSale() {

        const order = this.pos.getOrder();
        const line = order?.getSelectedOrderline();

        // --------------------------------------------------------
        // Debe existir una línea seleccionada
        // --------------------------------------------------------
        if (!line) {
            this.dialog.add(AlertDialog, {
                title: "Seleccione un producto",
                body: "Primero seleccione una línea del pedido.",
            });
            return;
        }


        // ========================================================
        // SI YA ESTÁ EN OFERTA → VOLVER A PRECIO NORMAL
        // ========================================================
        if (line.is_offer_sale) {

            // Recuperar precio anterior
            if (line.uiState.offerOriginalPrice !== undefined) {
                line.setUnitPrice(line.uiState.offerOriginalPrice);
            }

            // Recuperar el tipo de precio anterior
            line.price_type =
                line.uiState.offerOriginalPriceType || "original";

            // Quitar condición de oferta
            line.is_offer_sale = false;

            // Limpiar datos temporales
            delete line.uiState.offerOriginalPrice;
            delete line.uiState.offerOriginalPriceType;

            return;
        }


        // ========================================================
        // ACTIVAR OFERTA
        // ========================================================
        try {

            // Consultar al backend:
            // producto + POS + almacén correspondiente
            const offerInfo = await this.pos.data.call(
                "pos.config",
                "get_offer_sale_info",
                [
                    this.pos.config.id,
                    line.product_id.id,
                ]
            );


            // ----------------------------------------------------
            // El producto no puede venderse como oferta
            // ----------------------------------------------------
            if (!offerInfo.available) {
                this.dialog.add(AlertDialog, {
                    title: "Oferta no disponible",
                    body:
                        offerInfo.message ||
                        "Este producto no está disponible para venta en oferta.",
                });
                return;
            }


            // ----------------------------------------------------
            // Validar cantidad de esta línea
            // ----------------------------------------------------
            if (line.qty > offerInfo.offer_quantity) {
                this.dialog.add(AlertDialog, {
                    title: "Stock de oferta insuficiente",
                    body:
                        `Solo hay ${offerInfo.offer_quantity} ` +
                        `unidad(es) disponibles en oferta.`,
                });
                return;
            }


            // ----------------------------------------------------
            // Guardar precio normal antes de modificarlo
            // ----------------------------------------------------
            line.uiState.offerOriginalPrice = line.price_unit;
            line.uiState.offerOriginalPriceType = line.price_type;


            // ----------------------------------------------------
            // Aplicar precio oficial de oferta
            // ----------------------------------------------------
            line.setUnitPrice(offerInfo.offer_price);

            // Odoo identifica el precio como establecido manualmente
            // para que no lo recalcule automáticamente.
            line.price_type = "manual";

            // Marcar línea como oferta
            line.is_offer_sale = true;

        } catch (error) {

            console.error(
                "Error al consultar información de oferta:",
                error
            );

            this.dialog.add(AlertDialog, {
                title: "Error al consultar oferta",
                body:
                    "No se pudo consultar el precio de oferta. " +
                    "Inténtelo nuevamente.",
            });
        }
    },
});

// ============================================================
// PROTECCIONES PARA LÍNEAS VENDIDAS EN OFERTA
// ============================================================

patch(OrderSummary.prototype, {

    // ========================================================
    // BLOQUEAR CAMBIO MANUAL DEL PRECIO
    // ========================================================
    async setLinePrice(line, price) {

        if (line?.is_offer_sale) {
            this.dialog.add(AlertDialog, {
                title: "Precio de oferta protegido",
                body:
                    "El precio de una venta en oferta no puede modificarse. " +
                    "Para usar otro precio, primero quite la condición de Oferta.",
            });
            return;
        }

        return super.setLinePrice(line, price);
    },


    // ========================================================
    // CONTROLAR CAMBIOS DE CANTIDAD EN UNA OFERTA
    //
    // Si el vendedor intenta AUMENTAR la cantidad, se consulta
    // nuevamente al servidor para conocer el stock de oferta
    // disponible en ese momento.
    //
    // Reducir la cantidad sí está permitido.
    // ========================================================
    async updateSelectedOrderline({ buffer, key }) {

        const order = this.pos.getOrder();
        const line = order?.getSelectedOrderline();

        // ========================================================
        // VALIDAR AUMENTO DE CANTIDAD
        //
        // Solo consultamos al servidor cuando:
        // - estamos modificando cantidad;
        // - existe una línea seleccionada;
        // - la nueva cantidad es mayor que la actual.
        //
        // Reducir cantidades continúa funcionando normalmente.
        // ========================================================
        if (
            line &&
            this.pos.numpadMode === "quantity" &&
            buffer !== null
        ) {

            const requestedQty = Number(buffer);
            const currentQty = Number(line.getQuantity() || 0);

            const isRefundLine =
                Boolean(line.refunded_orderline_id) ||
                currentQty < 0;

            if (
                !isRefundLine &&
                Number.isFinite(requestedQty) &&
                requestedQty > currentQty
            ) {

                // ====================================================
                // 1. VALIDAR STOCK FÍSICO
                // ====================================================
                try {

                    const stockInfo = await this.pos.data.call(
                        "pos.config",
                        "get_pos_sale_stock_info",
                        [
                            this.pos.config.id,
                            line.product_id.id,
                            order?.commercial_operation_mode || false,
                        ]
                    );

                    // Productos que no controlan existencias
                    // pueden continuar normalmente.
                    if (stockInfo.track_stock !== false) {

                        // --------------------------------------------
                        // Sin stock o producto no preparado
                        // --------------------------------------------
                        if (!stockInfo.available) {

                            this.numberBuffer.reset();

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

                        // --------------------------------------------
                        // La cantidad solicitada supera el stock real
                        // --------------------------------------------
                        if (requestedQty > availableQty) {

                            this.numberBuffer.reset();

                            this.dialog.add(AlertDialog, {
                                title: "Stock insuficiente",
                                body:
                                    `Está intentando vender ${requestedQty} unidad(es), ` +
                                    `pero solo hay ${availableQty} unidad(es) disponibles.`,
                            });

                            return;
                        }
                    }

                } catch (error) {

                    console.error(
                        "Error al validar stock físico:",
                        error
                    );

                    this.numberBuffer.reset();

                    this.dialog.add(AlertDialog, {
                        title: "Error al validar stock",
                        body:
                            "No se pudo comprobar el stock disponible. " +
                            "Inténtelo nuevamente.",
                    });

                    return;
                }


                // ====================================================
                // 2. VALIDACIÓN ADICIONAL PARA VENTA EN OFERTA
                //
                // Además del stock físico, una oferta no puede superar
                // la cantidad comercial reservada para esa condición.
                // ====================================================
                if (line.is_offer_sale) {

                    try {

                        const offerInfo = await this.pos.data.call(
                            "pos.config",
                            "get_offer_sale_info",
                            [
                                this.pos.config.id,
                                line.product_id.id,
                            ]
                        );

                        // La oferta pudo agotarse, vencer o desactivarse
                        // mientras la orden estaba abierta.
                        if (!offerInfo.available) {

                            this.numberBuffer.reset();

                            this.dialog.add(AlertDialog, {
                                title: "Oferta no disponible",
                                body:
                                    offerInfo.message ||
                                    "Esta oferta ya no se encuentra disponible.",
                            });

                            return;
                        }

                        // No permitir superar el saldo reservado
                        // específicamente para oferta.
                        if (requestedQty > offerInfo.offer_quantity) {

                            this.numberBuffer.reset();

                            this.dialog.add(AlertDialog, {
                                title: "Stock de oferta insuficiente",
                                body:
                                    `Está intentando vender ${requestedQty} unidad(es), ` +
                                    `pero solo hay ${offerInfo.offer_quantity} ` +
                                    `unidad(es) disponibles en oferta.`,
                            });

                            return;
                        }

                    } catch (error) {

                        console.error(
                            "Error al validar cantidad de oferta:",
                            error
                        );

                        this.numberBuffer.reset();

                        this.dialog.add(AlertDialog, {
                            title: "Error al validar oferta",
                            body:
                                "No se pudo comprobar el stock de oferta. " +
                                "Inténtelo nuevamente.",
                        });

                        return;
                    }
                }
            }
        }

        // Cantidad permitida o reducción:
        // conservar comportamiento normal de Odoo.
        return super.updateSelectedOrderline({ buffer, key });
    },
});