import json

import pandas as pd
import requests

from utils.result import Result


def fetch_table(url: str) -> Result[pd.DataFrame]:
    """GET a JSON API and flatten the records into a table."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return Result.fail("etl.http", f"Request to {url} failed: {exc}")

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        return Result.fail("etl.not_json", f"{url} did not return JSON: {exc}")

    # Most APIs do not wrap in "results"; fall back to the payload itself.
    records = payload.get("results", payload) if isinstance(payload, dict) else payload

    try:
        df = pd.json_normalize(records)
    except (ValueError, TypeError, NotImplementedError) as exc:
        keys = list(payload)[:10] if isinstance(payload, dict) else type(payload).__name__
        return Result.fail(
            "etl.no_results_key",
            f"Could not flatten the response into a table ({exc}). Top-level keys: {keys}",
        )

    if df.empty:
        return Result.fail("etl.empty", f"{url} returned no rows.")

    # Nested lists survive json_normalize; store them as JSON text so every column has one type.
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
    return Result.ok(df)
