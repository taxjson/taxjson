"""The T1135 domicile override vocabulary: the COUNTRY of a ticker.map
`T1135 SYMBOL COUNTRY` line (once a t1135.map line).

COUNTRY is an ISO 3166-1 alpha-3 code, or one of the "not foreign
property" words CA / CAN / CANADA / EXCLUDE (S051-16): anything else
(EXCLUDED, CDN, NOT-FOREIGN) used to become a bogus country and flip the
verdict, so it is refused with a did-you-mean hint. Shared by the
ticker.map reader (lib/ticker_map), taxjson-t1135 and `taxjson migrate`.
"""
from typing import Optional

NOT_FOREIGN_WORDS = ("CA", "CAN", "CANADA", "EXCLUDE")
# The canonical value a not-foreign word reads as.
NOT_FOREIGN = "CA"
ISO3 = frozenset("""
ABW AFG AGO AIA ALA ALB AND ARE ARG ARM ASM ATA ATF ATG AUS AUT AZE BDI
BEL BEN BES BFA BGD BGR BHR BHS BIH BLM BLR BLZ BMU BOL BRA BRB BRN BTN
BVT BWA CAF CAN CCK CHE CHL CHN CIV CMR COD COG COK COL COM CPV CRI CUB
CUW CXR CYM CYP CZE DEU DJI DMA DNK DOM DZA ECU EGY ERI ESH ESP EST ETH
FIN FJI FLK FRA FRO FSM GAB GBR GEO GGY GHA GIB GIN GLP GMB GNB GNQ GRC
GRD GRL GTM GUF GUM GUY HKG HMD HND HRV HTI HUN IDN IMN IND IOT IRL IRN
IRQ ISL ISR ITA JAM JEY JOR JPN KAZ KEN KGZ KHM KIR KNA KOR KWT LAO LBN
LBR LBY LCA LIE LKA LSO LTU LUX LVA MAC MAF MAR MCO MDA MDG MDV MEX MHL
MKD MLI MLT MMR MNE MNG MNP MOZ MRT MSR MTQ MUS MWI MYS MYT NAM NCL NER
NFK NGA NIC NIU NLD NOR NPL NRU NZL OMN PAK PAN PCN PER PHL PLW PNG POL
PRI PRK PRT PRY PSE PYF QAT REU ROU RUS RWA SAU SDN SEN SGP SGS SHN SJM
SLB SLE SLV SMR SOM SPM SRB SSD STP SUR SVK SVN SWE SWZ SXM SYC SYR TCA
TCD TGO THA TJK TKL TKM TLS TON TTO TUN TUR TUV TWN TZA UGA UKR UMI URY
USA UZB VAT VCT VEN VGB VIR VNM VUT WLF WSM YEM ZAF ZMB ZWE
""".split())


def parse_country(word: str) -> str:
    """The canonical COUNTRY of `word` (any case): its ISO 3166 alpha-3
    code, or NOT_FOREIGN ("CA") for a not-foreign word. Raises
    ValueError (with a did-you-mean hint) for anything else."""
    code = word.upper()
    if code in NOT_FOREIGN_WORDS:
        return NOT_FOREIGN
    if code in ISO3:
        return code
    import difflib
    near = difflib.get_close_matches(
        code, list(NOT_FOREIGN_WORDS) + sorted(ISO3), n=1, cutoff=0.6)
    hint = f" — did you mean {near[0]}?" if near else ""
    raise ValueError(f"{word!r} is not an ISO 3166 alpha-3 country code "
                     f"or {'/'.join(NOT_FOREIGN_WORDS)}{hint}")


def override_value(canonical: str) -> Optional[str]:
    """What classify_country uses: None (not foreign property) for
    NOT_FOREIGN, else the ISO code."""
    return None if canonical == NOT_FOREIGN else canonical
