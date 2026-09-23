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

        if (
            this.config_id?.warehouse_product_control_mode === "mixed" &&
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

        const isMixedWholesale =
            this.config.warehouse_product_control_mode === "mixed" &&
            order.commercial_operation_mode === "model";

        // ========================================================
        // VENTA UNITARIA
        // Mantener comportamiento estándar de Odoo.
        // ========================================================
        if (!isMixedWholesale) {
            return await super.addLineToCurrentOrder(
                vals,
                opts,
                configure
            );
        }

        // ========================================================
        // VENTA MAYORISTA
        //
        // No solicitamos talla/color.
        // No mostramos matriz de distribución.
        //
        // Se agrega directamente una sola línea por modelo.
        // El backend convertirá el movimiento de stock a la
        // variante técnica SIN CLASIFICAR.
        // ========================================================
        return await super.addLineToCurrentOrder(
            vals,
            opts,
            false
        );
    },
});