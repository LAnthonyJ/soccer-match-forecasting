# Soccer Match Forecasting

Estimate the outcome of an international football match from the command line, or explore scheduled fixtures in the mobile web app. Each forecast includes win, draw and loss probabilities, expected goals and likely scorelines.

## Run your first forecast

From the repository root, create a Python 3.11 virtual environment. On Windows with PowerShell, run the following commands.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m src.predict_production --bundled-model --home France --away Argentina --tier basic
```

On Linux or macOS, create the environment with `python3.11 -m venv .venv` and use `.venv/bin/python` for the last two commands. The bundled forecast runs offline after installation and requires no API key.

With the bundled parameters and default goal multiplier, the neutral-ground France–Argentina example gives the following rounded results.

| France win | Draw | Argentina win | Most likely score |
| ---: | ---: | ---: | :--- |
| 27.9% | 31.0% | 41.1% | 1–1 (14.4%) |

Argentina's win probability adds up every scoreline where Argentina scores more goals. A draw can still be the most likely *individual* scoreline. This example simulates a matchup using fixed team parameters. It makes no claim about the date or lineup of a real fixture.

## Customize a prediction

Use team names exactly as listed in the bundled [Dixon–Coles parameters](config/dixon_coles.json). Once your virtual environment is active, you can supply a complete set of home, draw and away decimal odds and save the resulting JSON.

```sh
python -m src.predict_production --bundled-model --home France --away Argentina --odds-home 2.60 --odds-draw 3.10 --odds-away 2.85 --output forecast.json
```

The model converts attack and defence ratings into a score distribution, adjusts low-scoring outcomes and applies the configured goals multiplier. With odds supplied, it blends the model's win/draw/loss estimates with normalized market probabilities. The `confidence` field controls the model's weight in that blend. The CLI uses neutral ground and defaults to the detailed `vip` output. Omit `--bundled-model` to use the trained pickle in `outputs/models/`.

## Run the web app

With the virtual environment active, start Flask from the repository root.

```sh
python -m flask --app src.server:app run
```

Open [the mobile interface](http://127.0.0.1:5000/h5) or check [service health](http://127.0.0.1:5000/health/live). Live fixture lookups use Sportmonks credentials, and protected routes require a session. See the [environment variables](.env.example), [deployment guide](docs/deployment_guide.md) and [prediction API contract](docs/api_prediction_contract.md).

## Verify changes

Run the Python tests with a temporary data directory. The frontend build uses Node.js and writes the Cloudflare Pages bundle to `dist/`.

```powershell
$env:DATA_DIR = Join-Path $env:TEMP ('football-tests-' + [guid]::NewGuid())
python -m unittest discover -s tests -q
npm run build
```

For implementation details, start with the [score models](src/models/score_models.py), [prediction engine](src/models/production_prediction.py) or [mobile frontend](frontend/h5/).
