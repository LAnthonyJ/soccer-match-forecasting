# Matchweek 12 acceptance fixtures

`matchday12-odbl.csv` contains the schedule and final-score changes through round 12 relative to the bundled CC0 dataset. The input is the verified 2026/27 snapshot with data digest `e45f40360c05e553bd4d0ad670dcffc6c68cebc831dfd507e4242c60ffc729fb`, observed on 25 November 2026. An empty score means no confirmed result. The tests replay those observations with a fixed clock on 26 November and exercise the production prediction, export and question handlers.

The CSV is derived from the merged database of the openfootball and OpenLigaDB contributors and is distributed under [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). [Source attribution and terms](../../docs/DATA.md)

The conversation test also seeds the next round’s Brentford–Arsenal fixture at 20:15 UTC on 2 December, as verified in the local fixture database on 26 November. This supplies two future Arsenal fixtures when the visitor asks about the team without naming an opponent.
