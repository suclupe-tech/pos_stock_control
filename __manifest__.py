{
    "name": "POS Stock Control",
    "version": "19.0.2.0",
    "depends": [
        "point_of_sale",
        "stock",
        "dt_catalogo_comercial",
    ],
    "data": [
        "views/pos_config_views.xml",
    ],
    # ============================================================
    # ASSETS POS
    #
    # El aviso JavaScript anterior queda desactivado temporalmente.
    # Primero implementaremos y probaremos el control definitivo
    # de stock en el backend.
    # ============================================================
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_stock_control/static/src/js/pos_stock_warning.js",
            "pos_stock_control/static/src/js/pos_commercial_mode.js",
            "pos_stock_control/static/src/xml/offer_sale_button.xml",
            "pos_stock_control/static/src/xml/commercial_mode_button.xml",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
