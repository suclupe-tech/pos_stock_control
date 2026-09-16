{
    "name": "POS Stock Control",
    "version": "19.0.2.0",
    "depends": [
        "point_of_sale",
        "stock",
        "dt_catalogo_comercial",
    ],
    "data": [],
    # ============================================================
    # ASSETS POS
    #
    # El aviso JavaScript anterior queda desactivado temporalmente.
    # Primero implementaremos y probaremos el control definitivo
    # de stock en el backend.
    # ============================================================
    "assets": {
        "point_of_sale._assets_pos": [],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
