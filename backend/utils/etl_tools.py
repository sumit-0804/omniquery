import json
from pathlib import Path

import pandas as pd
import requests

from utils.paths import resolve_under_root
from utils.result import Result


class ETLTools:

    def extract_load(self, url: str, output_folder: str, output_format: str = "csv") -> Result[str]:
        if output_format not in ("csv", "json"):
            return Result.fail("etl.unsupported_format", f"Unsupported format: {output_format}")

        folder = resolve_under_root(output_folder)
        if not folder:
            return folder

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
        if isinstance(payload, dict):
            records = payload.get("results", payload)
        else:
            records = payload

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

        target = folder.value / f"extracted_data.{output_format}"
        try:
            folder.value.mkdir(parents=True, exist_ok=True)
            if output_format == "csv":
                df.to_csv(target, index=False)
            else:
                df.to_json(target, orient="records", lines=True)
        except (OSError, ValueError) as exc:
            return Result.fail("etl.write_failed", f"Could not write {target}: {exc}")

        return Result.ok(f"Extracted {len(df)} rows to {target}", row_count=len(df), path=str(target))

    def describe_file(self, file_path: str | Path) -> Result[str]:
        """Column names, dtypes and sample rows, used as context for code generation."""
        path = Path(file_path)
        suffix = path.suffix.lower()
        readers = {".csv": pd.read_csv, ".parquet": pd.read_parquet}

        try:
            if suffix == ".json":
                df = pd.read_json(path, lines=True)
            elif suffix in readers:
                df = readers[suffix](path)
            else:
                return Result.fail("etl.unsupported_format", f"Unsupported file format: {suffix}")
        except FileNotFoundError:
            return Result.fail("etl.missing_file", f"No such file: {path}")
        except (OSError, ValueError) as exc:
            return Result.fail("etl.read_failed", f"Could not read {path}: {exc}")

        dtypes = "\n".join(f"  {col} ({dtype})" for col, dtype in df.dtypes.items())
        return Result.ok(
            f"Rows: {len(df)}\nColumns:\n{dtypes}\n\nFirst 3 rows:\n{df.head(3).to_string()}"
        )
