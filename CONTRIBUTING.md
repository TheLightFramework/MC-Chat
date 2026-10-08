# Contributing

MC Chat is an experiment in model output protocols. Useful contributions include
reproducible parser bugs, clearer framework instructions, accessibility fixes,
and controlled comparisons of model adherence.

## Development

Use Python 3.10 or newer. From the repository root:

```sh
python -m venv .venv
```

Activate the environment with `.venv\Scripts\Activate.ps1` on Windows or
`source .venv/bin/activate` on macOS/Linux, then:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

If changing frontend JavaScript, use a local Node installation for syntax checks:

```sh
node --check frontend/app.js
node --check frontend/markdown.js
```

Node is not needed to run the app. The automated suite uses simulated transports
and temporary databases; it does not need an API key or make paid model calls.
Keep that property when adding tests. Use the demo for manual frontend checks.

## Protocol changes

Preserve raw model output, deleted text and original validation results in the
audit. Never reorder model output to manufacture interleaving or label a local
repair as a model-authored correction. Keep existing chat snapshots and saved
request strings unchanged so later requests retain their history prefix.

## Bug reports

Include the framework version, model slug, provider when known, temperature,
reasoning setting and a minimal synthetic reproduction. Distinguish format
compliance from answer quality. For cache claims, use reported cached-token
counts rather than inferring a hit from speed.

Do not include API keys, `.env`, database files, personal conversations or raw
exports containing private information. Reduce the case to safe example text.

## License

Contributions to this project are provided under its MIT license. Preserve
third-party attribution and license notices when modifying bundled assets.
