/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

patch(ProductScreen.prototype, {
    async addProductToOrder(product) {
        
        if (product.is_storable && (product.qty_available || 0) <= 0) {
            this.dialog.add(AlertDialog, {
                title: "Sin stock",
                body: `El producto "${product.name}" no tiene stock disponible.`,
            });
            return;
        }

        return super.addProductToOrder(product);
    },
});