"""
CSV/Excel parsing + validation for KaHero batch exports (Row 7.2).

Validates file STRUCTURE and per-row data types here; business-level
checks (does this product actually exist in our catalog?) belong in
services.py, not here -- this module only answers "is this row
well-formed," not "does this row make sense."
"""

import pandas as pd

REQUIRED_COLUMNS = ["transaction_date", "product_name", "quantity", "unit_price"]


class ParseError(Exception):
    """Raised when the file itself can't be read at all (wrong format,
    corrupted, missing required columns) -- distinct from a single row
    being invalid, which is recorded per-row instead of raising."""


def read_kahero_file(uploaded_file):
    """Reads a KaHero export (CSV or Excel) into a pandas DataFrame.
    Raises ParseError for anything that prevents reading the file at all."""
    filename = uploaded_file.name.lower()

    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file)
        else:
            raise ParseError(
                f"Unsupported file type: '{uploaded_file.name}'. "
                "Please upload a .csv or .xlsx export from KaHero."
            )
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"Could not read file: {exc}") from exc

    if df.empty:
        raise ParseError("The uploaded file has no data rows.")

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ParseError(
            f"Missing required column(s): {', '.join(missing)}. "
            f"Expected columns: {', '.join(REQUIRED_COLUMNS)}."
        )

    return df


def validate_row(row, row_number):
    """Validates a single row's data types/values.

    Returns (cleaned_dict, None) on success, or (None, error_message) on
    failure -- never raises, since one bad row shouldn't abort the whole
    file (Phase 10 risk table: "messy real-world data" is expected).
    """
    product_name = str(row.get("product_name", "")).strip()
    if not product_name or product_name.lower() == "nan":
        return None, f"Row {row_number}: product_name is missing."

    try:
        quantity = int(row["quantity"])
        if quantity <= 0:
            return None, f"Row {row_number}: quantity must be a positive number."
    except (TypeError, ValueError, KeyError):
        return None, f"Row {row_number}: quantity '{row.get('quantity')}' is not a valid number."

    try:
        unit_price = float(row["unit_price"])
        if unit_price < 0:
            return None, f"Row {row_number}: unit_price cannot be negative."
    except (TypeError, ValueError, KeyError):
        return (
            None,
            f"Row {row_number}: unit_price '{row.get('unit_price')}' is not a valid number.",
        )

    transaction_date = row.get("transaction_date")
    if pd.isna(transaction_date):
        return None, f"Row {row_number}: transaction_date is missing."

    return {
        "product_name": product_name,
        "quantity": quantity,
        "unit_price": unit_price,
        "transaction_date": transaction_date,
    }, None


def parse_kahero_file(uploaded_file):
    """Full parse: reads the file, validates every row.

    Returns (valid_rows, errors) -- valid_rows is a list of cleaned dicts
    ready for services.py to ingest; errors is a list of per-row error
    strings. Both can be non-empty at once (a partially-bad file still
    yields whatever good rows it has).
    """
    df = read_kahero_file(uploaded_file)

    valid_rows = []
    errors = []
    for idx, row in df.iterrows():
        # +2: pandas is 0-indexed and the header row itself is row 1 in
        # the actual spreadsheet, so a human looking at the file in Excel
        # sees this same row number.
        row_number = idx + 2
        cleaned, error = validate_row(row, row_number)
        if error:
            errors.append(error)
        else:
            valid_rows.append(cleaned)

    return valid_rows, errors
