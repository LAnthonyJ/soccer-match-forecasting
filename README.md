# World Cup forecasts

Explore pre-match forecasts for international football. The mobile interface shows home, draw and away probabilities, likely scorelines, goal totals and the data available before kickoff. Search for a national team by its English name or a supported alias.

## Run locally

Install the Python dependencies in a virtual environment and run the test suite from the repository root.

~~~sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests
~~~

Build the static frontend for Cloudflare Pages with the following command.

~~~sh
npm run build
~~~

## Project guide

- [Mobile frontend](frontend/h5/) and [API implementation](src/api/)
- [Data ingestion and team aliases](src/data/)
- [Model explanations](src/llm/) and [prediction services](src/services/)
- [Project progress](PROJECT_PROGRESS.md) and [deployment documentation](docs/)

Forecasts are probabilistic. Available odds, lineup information and data freshness vary by fixture.
