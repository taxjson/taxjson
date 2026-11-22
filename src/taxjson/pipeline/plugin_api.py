"""The core run's functions a taxjson plugin (the broker-fetch plugin,
packages/taxjson-fetch) used to import from taxjson.bin: one module, so
no plugin reaches bin (step 8 of the architecture plan;
scripts/check_architecture.py refuses that) and a move inside the run
leaves these names where they are. (The plugin imports lib names too,
private ones among them: those are not kept here, TJ-3543.) A name
here leaves or changes only with a CHANGELOG line for plugin authors.

A published plugin from before this module imports the same functions
from bin/taxjson_run.py (`detect_broker`, `input_files`) and
bin/taxjson_detect_brokerage.py (`looks_like_ib_text`); those stay there
for it (tests/features/cli/test_cli_out_of_the_entry_point_and_run_state.py)."""
from taxjson.lib.brokerages.detect import looks_like_ib_text as looks_like_ib_text
from taxjson.pipeline.config import input_files as input_files
from taxjson.pipeline.inputs import detect_broker as detect_broker

__all__ = ("detect_broker", "input_files", "looks_like_ib_text")
