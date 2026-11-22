"""The parsers' home as step 5 of the engine refactor converts them: the
shared toolbox first (`toolbox/`), the registry of every parser
(`registry`: id, name, spellings, module, tier, how a file reaches it),
then each broker as a tier-1 layout, a tier-2 parser or a tier-3
extractor over the `read / classify / build / finish` protocol
(`protocol`; detection is lib/brokerages/detect's, by the `registry`).
Today's parsers are `taxjson.lib.brokerages`; RBC is on the protocol."""
