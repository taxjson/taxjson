"""The windows (days) of the pipeline's transfer pairing and netting
(lib/pipeline: _drop_self_cancelling_transfers,
_net_cross_account_transfers): how far apart one move's legs may be,
how long a netted segment may span, how near a trade stops the
netting, how far a declared leg's attestation reaches, the broker
restatement thresholds, and the unmapped-journal suggestion's window.
Out of lib/pipeline, which the engine's own-move check
(engine/ca/indexes._own_move_held) read for the segment span while the
pipeline calls the engine (step 8 of the architecture plan: lib's
import cycle). The pipeline and engine/ca/indexes each bind their own
copy at import: a test that changes a window patches both."""


# A same-position custody move (broker transfer, cross-listing journal,
# cancel/rebook restatement) completes within days; ACATS worst cases run
# a couple of weeks. Rows further apart than this are separate EVENTS —
# an in-kind contribution out in February and an unrelated transfer in
# come December must never cancel each other just because the share
# counts happen to match.
_TRANSFER_PAIR_MAX_GAP_DAYS = 35

# The per-gap limit alone still lets a segment CHAIN: eleven rows each
# <= 35 days apart span February..December, so a real February
# disposition could be "netted" against unrelated rows ten months later.
# Beyond the per-gap limit, the whole segment's lo..hi SPAN is therefore
# capped for the zero-net drop decision. Oversized chains are REFUSED
# outright rather than split at the largest internal gap: refusal is
# simpler and strictly conservative — the surviving rows fall through to
# the loud paths (the --taxable hard error, or the transfer_rewrite +
# AmbiguousTransferDateError engine guard) instead of being silently
# guessed into clusters. A genuine custody move (ACATS worst case,
# cancel/rebook restatements included) completes well inside 45 days.
_TRANSFER_SEGMENT_MAX_SPAN_DAYS = 45

# How close a main-book trade of the symbol may be to a zero-net
# TRANSFER segment before netting is refused — mirrors the engine's
# ±30-day superficial-loss window.
_TRANSFER_NEAR_TRADE_PAD_DAYS = 30


# How far (days) a DECLARED .tt leg's authority reaches: only rows this
# close to a declared leg join its netted cluster. Wide enough for a
# multi-day broker restatement churn attested by one dated pair, narrow
# enough that a segment gap-chained weeks past the attestation keeps
# its own rows out of the blessing.
_ATTEST_BLESS_PAD_DAYS = 7

# Account-wide restatement detection: when at least this many DISTINCT
# symbols in ONE account have zero-net TRANSFER clusters overlapping a
# common date envelope (segments chained when within
# _RESTATEMENT_CHAIN_PAD_DAYS of each other), the event is a broker
# custody restatement — no tax event journals an account's whole
# inventory out-and-back to zero — and its clusters net without a
# per-symbol attestation. Below the threshold the per-symbol
# near-trade refusal (and the DECLARED resolution path) still applies:
# one or two symbols' churn is not evidence of an account-level event.
_RESTATEMENT_MIN_SYMBOLS = 3
_RESTATEMENT_CHAIN_PAD_DAYS = 3
# One broker restatement is a FEW DAYS of churn (well under a week).
# Without an event-level cap, pad-chaining could glue
# unrelated per-symbol pairs weeks apart into a >= 3-symbol "event"
# (round-five adversarial audit finding 2).
_RESTATEMENT_EVENT_MAX_SPAN_DAYS = 7

# Cross-listing journal candidates: an out-leg of X and an in-leg of a
# DIFFERENT symbol Y, same account, equal quantity, within this many
# days — the fingerprint of an UNMAPPED dual-listing journal (the
# mapped ones were normalized to one symbol before the pipeline's netting
# runs and # net as same-symbol clusters). Surfaced as a ticker.map suggestion.
_JOURNAL_CANDIDATE_PAD_DAYS = 7
