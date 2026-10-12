from typing import List
from taxjson.lib.core import TaxTransaction

def parse_tt_lines(content: str) -> List[TaxTransaction]:
    txs = []
    lines = content.strip().split('\n')
    for line in lines:
        if not line or line.startswith('#'): continue
        parts = line.split()
        action = parts[0]
        date = parts[1]
        time = parts[2]
        symbol = parts[3]
        
        if action == 'SPLIT':
            ratio = float(parts[5])
            txs.append(TaxTransaction(action=action, date=date, time=time, symbol=symbol, quantity=ratio))
        elif action == 'DIVIDEND':
            # DIVIDEND 2025-01-05 14:00:00 AAPL.US 100 CAD 5.00 500.00
            currency = parts[5]
            net = float(parts[7]) if len(parts) > 7 else 0.0
            txs.append(TaxTransaction(action=action, date=date, time=time, symbol=symbol, quantity=0, currency=currency, net_amount=net))
        else:
            # BUYSELL/ASSIGN date time symbol qty curr price net [fee]
            qty = float(parts[4])
            curr = parts[5]
            price = float(parts[6])
            net = float(parts[7])
            fee = float(parts[8]) if len(parts) > 8 else 0.0
            txs.append(TaxTransaction(action=action, date=date, time=time, symbol=symbol, quantity=qty, currency=curr, price=price, net_amount=net, fee=fee))
            
    return txs
