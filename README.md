# Avilytics

Internal scalar-ladder payoff analysis for Kalshi events. Avilytics fetches a
read-only market snapshot, prices entries at executable asks, applies the
Kalshi taker-fee curve, and reports payoff by exclusive outcome bin.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open `http://127.0.0.1:5050`. The app starts with the pinned WTI example; paste
a Kalshi event link or ticker to replace it with a live public snapshot.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The math lives in `avilytics/engine.py` and has no framework dependencies.
The Flask layer only validates requests and serializes engine results; the
frontend renders those server-computed values.
