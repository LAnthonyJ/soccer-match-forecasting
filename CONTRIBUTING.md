# Contributing

Use Python 3.11–3.13 and Node 24. Install the package and its locked dependencies, then run:

```sh
python -m pip install -c requirements.lock .
python -m unittest discover -s tests -v
cd frontend
npm ci
npm run build
npx playwright install chromium
npm run test:browser
```

Commit the frontend source and its generated package assets together. Keep tests offline and isolate state with `EPL_AGENT_HOME`. Document any new data license and provide a sample built from redistributable records. Submit a new model version and prospective evaluation protocol when changes affect the challenge. Use a new cohort after inspecting held-out results. Keep bookmaker responses, local databases, credentials and club artwork with unclear permissions out of Git history.
