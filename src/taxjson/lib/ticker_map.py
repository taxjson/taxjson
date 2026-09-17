import re
from datetime import datetime

# Delegate the two OCC primitives to the canonical helpers in `core.py`.
# Previously this module carried its own near-duplicate regexes which
# could drift from the engine's view of "is this an option?" — a recipe
# for subtle classification disagreements (e.g. wash-sale vs ticker-map
# disagreeing on whether `F:CL...` counts).
from taxjson.lib.core import parse_option_underlying, is_option_symbol

get_underlying = parse_option_underlying
is_option_ticker = is_option_symbol

def is_future_ticker(symbol: str) -> bool:
    """Checks if a symbol is a future (starts with / or \\ or F:)."""
    return symbol.startswith(('/', '\\', 'F:'))

def get_base_ticker_info(symbol: str):
    """
    Extracts the base symbol and extension (currency/exchange) from a string.
    Handles TICKER.EXT or OCC strings.
    """
    parts = symbol.rsplit('.', 1)
    if len(parts) > 1:
        ext = parts[1].upper()
        full_ticker = parts[0]
    else:
        ext = ""
        full_ticker = symbol

    # Handle OCC Options (extract symbol before the date/strike block)
    # e.g., RCI.B270115C00045000 -> RCI.B
    match = re.match(r'^((?:F:|[\/\\])?[A-Z0-9\.]+?)\d{6}[CP]\d+', full_ticker, re.IGNORECASE)
    if match:
        return match.group(1), ext
    
    return full_ticker, ext

def format_ticker_for_platform(symbol: str, platform: str, tv_map: dict = None) -> str:
    """Formats a ticker for a specific platform (SeekingAlpha, TradingView, FastGraph)."""
    base, ext = get_base_ticker_info(symbol)
    if not ext:
        return base
        
    if platform == 'seekingalpha':
        if ext == 'TO': return f"{base}:CA"
        return base
    elif platform == 'tradingview':
        # tv_exchange.map keys may be a bare ticker (applies to every
        # listing of it) or extension-qualified — `OR.US` / `OR.TO` —
        # which lets a dual-listed name get a different exchange prefix
        # per listing. The qualified key wins over the bare one.
        tvm = tv_map or {}
        if ext == 'TO':
            prefix = tvm.get(f"{base}.TO") or tvm.get(base, 'TSX')
            return f"{prefix}:{base}"
        if ext == 'US':
            prefix = tvm.get(f"{base}.US") or tvm.get(base)
            return f"{prefix}:{base}" if prefix else base
        if ext == 'AX': return f"ASX:{base}"
        if ext == 'L': return f"LSE:{base}"
        return base
    elif platform == 'fastgraph':
        if ext == 'TO': return f"{base}:CA"
        return f"{base}:US"
    
    return f"{base}.{ext}"

def get_option_type(symbol: str) -> str:
    """Returns 'C' or 'P' from an OCC-style option ticker."""
    match = re.search(r'\d{6}([CP])\d+', symbol, re.IGNORECASE)
    return match.group(1).upper() if match else None

def map_ticker(symbol: str, target_currency: str = "CAD") -> str:
    """
    Maps a ticker symbol to its equivalent in the target currency (usually CAD).
    Example: SHOP.US -> SHOP.TO if target is CAD.
    Also handles converting dotted option strings to OCC syntax.
    Example: U.19SEP25.26.P -> U250919P00026000
    """
    # 1. Handle Dotted Options (e.g. U.19SEP25.26.P)
    option_pattern = r'^([A-Z0-9]+)\.(\d{1,2}[A-Z]{3}\d{2})\.(\d+(?:\.\d+)?)\.([CP])$'
    match = re.match(option_pattern, symbol, re.IGNORECASE)
    if match:
        underlying, date_str, strike_str, opt_type = match.groups()
        try:
            # Parse date 19SEP25
            dt = datetime.strptime(date_str.upper(), '%d%b%y')
            occ_date = dt.strftime('%y%m%d')

            # Format strike: 8 digits (5 integer, 3 decimal).
            # Use Decimal to avoid float-precision truncation —
            # `int(float("4.02") * 1000)` was returning 4019 instead of
            # 4020 (and similar for ~half of all sub-$200 cent-precision
            # strikes) because float("4.02") underflows to 4.0199999…
            from decimal import Decimal
            strike_int = int(Decimal(strike_str) * 1000)
            occ_strike = f"{strike_int:08d}"
            
            # Construct OCC symbol
            symbol = f"{underlying.upper()}{occ_date}{opt_type.upper()}{occ_strike}"
        except (ValueError, TypeError):
            pass # Fall through to regular mapping if parsing fails

    if target_currency != "CAD":
        return symbol
        
    parts = symbol.rsplit('.', 1)
    if len(parts) < 2:
        return symbol
        
    base, ext = parts[0], parts[1].upper()
    
    # Simple mapping based on common extensions
    mapping = {
        'US': 'TO', # Simplified: assume US stocks map to TO for CAD tracking if not specified
        'USD': 'TO',
        'NASDAQ': 'TO',
        'NYSE': 'TO',
    }
    
    new_ext = mapping.get(ext, ext)
    return f"{base}.{new_ext}"
