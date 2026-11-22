"""Shared test helpers: the Webull 2025 header and a buy row.

Shared from: test_fix_a2_webull
"""


_H25 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action","Symbol\nSymbole",'
        '"Security Description\nDescription des titres",Type Code of Securities Code de genre de titres,'
        '"Quantity of Securities Quantité\nde titres","Price\nPrix",,'
        '"Proceeds of\nDisposition or Settlement Amount Produits de disposition"\n')


_BUY = 'CAD,12-12-2024,BUY,SHOP,SHOPIFY INC,SHS,30,95.00,,"(2,854.95)"\n'
