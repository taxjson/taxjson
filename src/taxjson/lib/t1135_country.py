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


# ISO 3166-1 alpha-2 -> alpha-3 (the same 249 countries as ISO3): the
# interlisted master records an issuer's country as its ISINs' two
# letters (`domicile`); tobase.map and T1135 speak alpha-3.
_ISO2_ISO3 = """
AD AND AE ARE AF AFG AG ATG AI AIA AL ALB AM ARM AO AGO AQ ATA AR ARG AS ASM
AT AUT AU AUS AW ABW AX ALA AZ AZE BA BIH BB BRB BD BGD BE BEL BF BFA BG BGR
BH BHR BI BDI BJ BEN BL BLM BM BMU BN BRN BO BOL BQ BES BR BRA BS BHS BT BTN
BV BVT BW BWA BY BLR BZ BLZ CA CAN CC CCK CD COD CF CAF CG COG CH CHE CI CIV
CK COK CL CHL CM CMR CN CHN CO COL CR CRI CU CUB CV CPV CW CUW CX CXR CY CYP
CZ CZE DE DEU DJ DJI DK DNK DM DMA DO DOM DZ DZA EC ECU EE EST EG EGY EH ESH
ER ERI ES ESP ET ETH FI FIN FJ FJI FK FLK FM FSM FO FRO FR FRA GA GAB GB GBR
GD GRD GE GEO GF GUF GG GGY GH GHA GI GIB GL GRL GM GMB GN GIN GP GLP GQ GNQ
GR GRC GS SGS GT GTM GU GUM GW GNB GY GUY HK HKG HM HMD HN HND HR HRV HT HTI
HU HUN ID IDN IE IRL IL ISR IM IMN IN IND IO IOT IQ IRQ IR IRN IS ISL IT ITA
JE JEY JM JAM JO JOR JP JPN KE KEN KG KGZ KH KHM KI KIR KM COM KN KNA KP PRK
KR KOR KW KWT KY CYM KZ KAZ LA LAO LB LBN LC LCA LI LIE LK LKA LR LBR LS LSO
LT LTU LU LUX LV LVA LY LBY MA MAR MC MCO MD MDA ME MNE MF MAF MG MDG MH MHL
MK MKD ML MLI MM MMR MN MNG MO MAC MP MNP MQ MTQ MR MRT MS MSR MT MLT MU MUS
MV MDV MW MWI MX MEX MY MYS MZ MOZ NA NAM NC NCL NE NER NF NFK NG NGA NI NIC
NL NLD NO NOR NP NPL NR NRU NU NIU NZ NZL OM OMN PA PAN PE PER PF PYF PG PNG
PH PHL PK PAK PL POL PM SPM PN PCN PR PRI PS PSE PT PRT PW PLW PY PRY QA QAT
RE REU RO ROU RS SRB RU RUS RW RWA SA SAU SB SLB SC SYC SD SDN SE SWE SG SGP
SH SHN SI SVN SJ SJM SK SVK SL SLE SM SMR SN SEN SO SOM SR SUR SS SSD ST STP
SV SLV SX SXM SY SYR SZ SWZ TC TCA TD TCD TF ATF TG TGO TH THA TJ TJK TK TKL
TL TLS TM TKM TN TUN TO TON TR TUR TT TTO TV TUV TW TWN TZ TZA UA UKR UG UGA
UM UMI US USA UY URY UZ UZB VA VAT VC VCT VE VEN VG VGB VI VIR VN VNM VU VUT
WF WLF WS WSM YE YEM YT MYT ZA ZAF ZM ZMB ZW ZWE
""".split()
ISO2_TO_ISO3 = dict(zip(_ISO2_ISO3[::2], _ISO2_ISO3[1::2]))


def iso3_of(code: str) -> Optional[str]:
    """The alpha-3 code of an alpha-2 (or alpha-3) country code, None
    when it is neither."""
    c = str(code or "").upper()
    if c in ISO3:
        return c
    return ISO2_TO_ISO3.get(c)
