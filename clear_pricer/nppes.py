"""NPPES (NPI registry) ingestion: discovery, projection, and change-data-capture into a type-2 history.

CMS publishes a monthly full file (~11.7 GB CSV, 330 columns, zipped to ~1.1 GB) plus weekly incremental files. The
honest model is CDC, not truncate-and-reload (design pin 2):

- every file is streamed straight out of its zip, projected to the ~25 columns this project uses, and staged as
  Parquet (the other columns are deliberately not modelled; the *header* is still checked in full for drift);
- each staged file is merged into `provider_history` (SCD type 2): a new version only when a provider's attributes
  actually changed, closing the previous version -- so a re-applied file changes nothing, and a weekly delta
  touches only the providers it contains;
- ordering is by the record's own effective date (latest of Last Update / Deactivation / Reactivation date), so an
  older file applied late can never overwrite newer state. Deactivation records arrive with every attribute blank
  (only NPI + date): they close the version and carry the last known attributes forward, rather than blanking them.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

# --- discovery -----------------------------------------------------------------------------------------------------

BASE_URL = "https://download.cms.gov/nppes/"
_LINK = re.compile(r"""['"]\./(NPPES_Data_Dissemination_[A-Za-z0-9_]+_V2\.zip)['"]""")
_MONTHLY = re.compile(r"NPPES_Data_Dissemination_([A-Za-z]+)_(\d{4})_V2\.zip")
_WEEKLY = re.compile(r"NPPES_Data_Dissemination_(\d{6})_(\d{6})_Weekly_V2\.zip")


@dataclass(frozen=True)
class NppesFile:
    name: str
    kind: str  # 'full' | 'weekly'

    @property
    def url(self) -> str:
        return BASE_URL + self.name


def parse_listing(html: str) -> list[NppesFile]:
    """The V2 monthly + weekly files linked from NPI_Files.html (deactivation report excluded)."""
    out = []
    for name in dict.fromkeys(_LINK.findall(html)):  # de-dupe, keep page order
        if _MONTHLY.fullmatch(name):
            out.append(NppesFile(name, "full"))
        elif _WEEKLY.fullmatch(name):
            out.append(NppesFile(name, "weekly"))
    return out


_PFILE = re.compile(r"npidata_pfile_(\d{8})-(\d{8})\.csv")


def coverage(names: list[str]) -> tuple[date, date, str]:
    """(start, end, member) of the npidata CSV inside a zip, from its own name -- e.g. 20050523-20260913."""
    for n in names:
        if m := _PFILE.fullmatch(n):
            s, e = m.groups()
            return date(int(s[:4]), int(s[4:6]), int(s[6:])), date(int(e[:4]), int(e[4:6]), int(e[6:])), n
    raise ValueError(f"no npidata_pfile_*.csv among {names}")


# --- projection ----------------------------------------------------------------------------------------------------

TAXONOMY_SLOTS = 15
PROJECTED = {  # source header -> column name
    "NPI": "npi",
    "Entity Type Code": "entity_type",
    "Replacement NPI": "replacement_npi",
    "Provider Organization Name (Legal Business Name)": "org_name",
    "Provider Last Name (Legal Name)": "last_name",
    "Provider First Name": "first_name",
    "Provider Middle Name": "middle_name",
    "Provider Credential Text": "credential",
    "Provider First Line Business Practice Location Address": "practice_address_1",
    "Provider Business Practice Location Address City Name": "practice_city",
    "Provider Business Practice Location Address State Name": "practice_state",
    "Provider Business Practice Location Address Postal Code": "practice_postal",
    "Provider Business Practice Location Address Country Code (If outside U.S.)": "practice_country",
    "Provider Enumeration Date": "enumeration_date_raw",
    "Last Update Date": "last_update_date_raw",
    "NPI Deactivation Reason Code": "deactivation_reason",
    "NPI Deactivation Date": "deactivation_date_raw",
    "NPI Reactivation Date": "reactivation_date_raw",
    "Is Organization Subpart": "is_org_subpart",
    "Parent Organization LBN": "parent_org_name",
    "Certification Date": "certification_date_raw",
} | {f"Healthcare Provider Taxonomy Code_{i}": f"tax_{i}" for i in range(1, TAXONOMY_SLOTS + 1)} \
  | {f"Healthcare Provider Primary Taxonomy Switch_{i}": f"taxsw_{i}" for i in range(1, TAXONOMY_SLOTS + 1)}

EXPECTED_COLUMNS = 330  # V2 layout, as observed 2026-09-29 (monthly + weekly identical)


def check_header(header: list[str]) -> list[dict]:
    """Header drift, as drift-log rows. Missing projected columns are fatal (gated); others are recorded."""
    drift = []
    missing = [h for h in PROJECTED if h not in header]
    for h in missing:
        drift.append({"kind": "missing_required_column", "column": h, "n": 1})
    if len(header) != EXPECTED_COLUMNS:
        drift.append({"kind": "column_count_changed", "column": "", "n": len(header)})
    dupes = {h for h in header if header.count(h) > 1}
    for h in sorted(dupes):
        drift.append({"kind": "duplicate_column", "column": h, "n": header.count(h)})
    return drift


# The projection SQL: dates parsed (MM/DD/YYYY), primary taxonomy derived from the 15 slots, the effective date,
# and a row hash over the attributes that define a provider version (update/certification dates excluded, so a
# re-certification with no attribute change is not a new version).
_DATE = "try_strptime(nullif({c}, ''), '%m/%d/%Y')::DATE"
_ATTRS = ["entity_type", "replacement_npi", "org_name", "last_name", "first_name", "middle_name", "credential",
          "practice_address_1", "practice_city", "practice_state", "practice_postal", "practice_country",
          "enumeration_date", "deactivation_reason", "deactivation_date", "reactivation_date", "primary_taxonomy",
          "taxonomy_codes", "is_org_subpart", "parent_org_name"]


def _projection_sql(src: str) -> str:
    tax = ", ".join(f"tax_{i}" for i in range(1, TAXONOMY_SLOTS + 1))
    primary = "coalesce(" + ", ".join(f"CASE WHEN taxsw_{i} = 'Y' THEN nullif(tax_{i}, '') END"
                                      for i in range(1, TAXONOMY_SLOTS + 1)) + ", nullif(tax_1, ''))"
    return f"""
    WITH p AS (
      SELECT
        npi, nullif(entity_type, '') AS entity_type, nullif(replacement_npi, '') AS replacement_npi,
        nullif(org_name, '') AS org_name, nullif(last_name, '') AS last_name, nullif(first_name, '') AS first_name,
        nullif(middle_name, '') AS middle_name, nullif(credential, '') AS credential,
        nullif(practice_address_1, '') AS practice_address_1, nullif(practice_city, '') AS practice_city,
        nullif(practice_state, '') AS practice_state, nullif(practice_postal, '') AS practice_postal,
        nullif(practice_country, '') AS practice_country,
        {_DATE.format(c='enumeration_date_raw')} AS enumeration_date,
        {_DATE.format(c='last_update_date_raw')} AS last_update_date,
        nullif(deactivation_reason, '') AS deactivation_reason,
        {_DATE.format(c='deactivation_date_raw')} AS deactivation_date,
        {_DATE.format(c='reactivation_date_raw')} AS reactivation_date,
        {primary} AS primary_taxonomy,
        list_filter([{tax}], x -> x IS NOT NULL AND x <> '') AS taxonomy_codes,
        nullif(is_org_subpart, '') AS is_org_subpart, nullif(parent_org_name, '') AS parent_org_name,
        {_DATE.format(c='certification_date_raw')} AS certification_date,
        (nullif(enumeration_date_raw, '') IS NOT NULL AND {_DATE.format(c='enumeration_date_raw')} IS NULL)
          OR (nullif(last_update_date_raw, '') IS NOT NULL AND {_DATE.format(c='last_update_date_raw')} IS NULL)
          OR (nullif(deactivation_date_raw, '') IS NOT NULL AND {_DATE.format(c='deactivation_date_raw')} IS NULL)
          OR (nullif(reactivation_date_raw, '') IS NOT NULL AND {_DATE.format(c='reactivation_date_raw')} IS NULL)
          AS bad_date
      FROM {src}
    )
    SELECT *,
      greatest(last_update_date, deactivation_date, reactivation_date) AS effective_date,
      md5(concat_ws('|', {", ".join(f"coalesce(CAST({a} AS VARCHAR), '')" for a in _ATTRS)})) AS row_hash,
      (entity_type IS NULL AND deactivation_date IS NOT NULL) AS is_deactivation_stub
    FROM p
    """


@dataclass
class StagedNppes:
    path: Path
    kind: str
    coverage_start: date
    coverage_end: date
    rows: int
    drift: list[dict]


def stage_zip(zip_path: Path, kind: str, out_dir: Path) -> StagedNppes:
    """Stream npidata_pfile_*.csv out of the zip -> projected, typed Parquet. Deterministic for a given zip."""
    with zipfile.ZipFile(zip_path) as z:
        start, end, member = coverage(z.namelist())
        with z.open(member) as raw:
            header = next(csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline="")))
        drift = check_header(header)
        if any(d["kind"] == "missing_required_column" for d in drift):
            return StagedNppes(Path(), kind, start, end, 0, drift)  # gated downstream; nothing to project

        out_dir.mkdir(parents=True, exist_ok=True)
        raw_parquet = out_dir / f".{zip_path.stem}.raw.parquet"
        final = out_dir / f"{zip_path.stem}.parquet"
        names = {h: PROJECTED[h] for h in header if h in PROJECTED}
        with z.open(member) as f:
            reader = pacsv.open_csv(
                f,
                read_options=pacsv.ReadOptions(block_size=64 << 20),
                convert_options=pacsv.ConvertOptions(
                    include_columns=list(names), column_types={h: pa.string() for h in names},
                    strings_can_be_null=False),
            )
            writer = None
            for batch in reader:
                t = pa.Table.from_batches([batch]).rename_columns([names[c] for c in batch.schema.names])
                writer = writer or pq.ParquetWriter(raw_parquet, t.schema, compression="zstd")
                writer.write_table(t)
            if writer:
                writer.close()

    con = duckdb.connect()
    con.sql("SET enable_progress_bar = false")
    con.sql(f"COPY ({_projection_sql(f'read_parquet({str(raw_parquet.as_posix())!r})')} ORDER BY npi, effective_date) "
            f"TO '{final.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    stats = con.sql(f"""SELECT count(*), count(*) FILTER (WHERE bad_date), count(*) - count(DISTINCT npi),
                               count(*) FILTER (WHERE effective_date IS NULL)
                        FROM read_parquet('{final.as_posix()}')""").fetchone()
    con.close()
    raw_parquet.unlink()
    rows, bad_dates, dup_npis, no_eff = stats
    for kind_, n in (("unparseable_date", bad_dates), ("duplicate_npi_in_file", dup_npis),
                     ("no_effective_date", no_eff)):
        if n:
            drift.append({"kind": kind_, "column": "", "n": n})
    return StagedNppes(final, kind, start, end, rows, drift)


# --- sync: discover -> download (curl, ETag) -> stage -> apply, in order ------------------------------------------

def applied_files(state_db: Path) -> set[str]:
    init_state(state_db)
    con = duckdb.connect(str(state_db), read_only=True)
    try:
        return {r[0] for r in con.sql("SELECT source_file FROM file_log WHERE outcome IN "
                                      "('applied', 'superseded_by_full')").fetchall()}
    finally:
        con.close()


def sync(data_dir: Path, state_db: Path, *, reapply: bool = False, log=lambda m: print(m, flush=True)) -> list[dict]:
    """Apply the latest full file (if new), then every weekly file after it, oldest first."""
    import subprocess

    raw = data_dir / "raw" / "nppes"
    raw.mkdir(parents=True, exist_ok=True)
    listing = raw / "NPI_Files.html"
    subprocess.run(["curl", "-sS", "-L", "--fail", "--retry", "3", "-o", str(listing), BASE_URL + "NPI_Files.html"],
                   check=True)
    files = parse_listing(listing.read_text(encoding="utf-8", errors="replace"))
    fulls = [f for f in files if f.kind == "full"]
    weeklies = sorted((f for f in files if f.kind == "weekly"),
                      key=lambda f: _WEEKLY.fullmatch(f.name).group(1)[4:] + _WEEKLY.fullmatch(f.name).group(1)[:4])
    done = set() if reapply else applied_files(state_db)
    results = []
    for f in fulls[-1:] + weeklies:
        if f.name in done:
            log(f"[nppes] {f.name}: already applied -- skip")
            continue
        zpath = raw / f.name
        etag = raw / f"{f.name}.etag"
        cmd = ["curl", "-sS", "-L", "--fail", "--retry", "3", "-w", "%{http_code}", "-o", str(zpath)]
        if zpath.exists() and etag.exists() and etag.read_text().strip():
            cmd += ["--etag-compare", str(etag)]
        proc = subprocess.run(cmd + ["--etag-save", str(etag), f.url], capture_output=True, text=True, check=True)
        log(f"[nppes] {f.name}: HTTP {proc.stdout.strip()[-3:]}")
        staged = stage_zip(zpath, f.kind, data_dir / "staging" / "nppes")
        result = apply(state_db, staged, f.name) | {"file": f.name}
        log(f"[nppes] {f.name}: " + "  ".join(f"{k}={v:,}" if isinstance(v, int) else f"{k}={v}"
                                               for k, v in result.items() if k != "file"))
        results.append(result)
    return results


# --- CDC merge -----------------------------------------------------------------------------------------------------

STATE_DDL = """
CREATE TABLE IF NOT EXISTS provider_history (
    npi VARCHAR NOT NULL, version INTEGER NOT NULL,
    entity_type VARCHAR, replacement_npi VARCHAR, org_name VARCHAR, last_name VARCHAR, first_name VARCHAR,
    middle_name VARCHAR, credential VARCHAR, practice_address_1 VARCHAR, practice_city VARCHAR,
    practice_state VARCHAR, practice_postal VARCHAR, practice_country VARCHAR, enumeration_date DATE,
    last_update_date DATE, deactivation_reason VARCHAR, deactivation_date DATE, reactivation_date DATE,
    primary_taxonomy VARCHAR, taxonomy_codes VARCHAR[], is_org_subpart VARCHAR, parent_org_name VARCHAR,
    certification_date DATE, effective_date DATE, row_hash VARCHAR NOT NULL,
    status VARCHAR NOT NULL,          -- active | deactivated | absent_from_full
    change_type VARCHAR NOT NULL,     -- insert | update | deactivate | reactivate | absent_from_full
    valid_from DATE, valid_to DATE, is_current BOOLEAN NOT NULL, source_file VARCHAR NOT NULL,
    PRIMARY KEY (npi, version)
);
CREATE TABLE IF NOT EXISTS file_log (
    seq INTEGER NOT NULL, source_file VARCHAR NOT NULL, kind VARCHAR NOT NULL, coverage_start DATE,
    coverage_end DATE, rows BIGINT, inserted BIGINT, updated BIGINT, deactivated BIGINT, reactivated BIGINT,
    unchanged BIGINT, stale_skipped BIGINT, absent_from_full BIGINT, outcome VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS drift_log (
    source_file VARCHAR NOT NULL, kind VARCHAR NOT NULL, "column" VARCHAR, n BIGINT
);
"""


def init_state(state_db: Path) -> None:
    state_db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(state_db))
    con.sql(STATE_DDL)
    con.close()


_COLS = ["entity_type", "replacement_npi", "org_name", "last_name", "first_name", "middle_name", "credential",
         "practice_address_1", "practice_city", "practice_state", "practice_postal", "practice_country",
         "enumeration_date", "last_update_date", "deactivation_reason", "deactivation_date", "reactivation_date",
         "primary_taxonomy", "taxonomy_codes", "is_org_subpart", "parent_org_name", "certification_date"]


def apply(state_db: Path, staged: StagedNppes, source_file: str) -> dict:
    """Merge one staged file into provider_history. Idempotent: re-applying a file yields zero changes."""
    init_state(state_db)
    con = duckdb.connect(str(state_db))
    con.sql("SET enable_progress_bar = false")
    try:
        con.begin()
        seq = con.sql("SELECT coalesce(max(seq), 0) + 1 FROM file_log").fetchone()[0]
        con.sql("DELETE FROM drift_log WHERE source_file = ?", params=[source_file])
        for d in staged.drift:
            con.execute("INSERT INTO drift_log VALUES (?, ?, ?, ?)", [source_file, d["kind"], d["column"], d["n"]])
        if any(d["kind"] == "missing_required_column" for d in staged.drift):
            con.execute("INSERT INTO file_log (seq, source_file, kind, coverage_start, coverage_end, outcome) "
                        "VALUES (?, ?, ?, ?, ?, 'rejected_schema')",
                        [seq, source_file, staged.kind, staged.coverage_start, staged.coverage_end])
            con.commit()
            return {"outcome": "rejected_schema"}

        latest_full_end = con.sql("SELECT max(coverage_end) FROM file_log WHERE kind = 'full' "
                                  "AND outcome = 'applied'").fetchone()[0]
        if staged.kind == "weekly" and latest_full_end and staged.coverage_end <= latest_full_end:
            con.execute("INSERT INTO file_log (seq, source_file, kind, coverage_start, coverage_end, rows, outcome) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'superseded_by_full')",
                        [seq, source_file, staged.kind, staged.coverage_start, staged.coverage_end, staged.rows])
            con.commit()
            return {"outcome": "superseded_by_full"}

        src = f"read_parquet('{staged.path.as_posix()}')"
        # one incoming row per NPI: the latest effective date wins (ties: deterministic by row_hash)
        con.sql(f"""CREATE TEMP TABLE incoming AS
                    SELECT * FROM {src}
                    QUALIFY row_number() OVER (PARTITION BY npi ORDER BY effective_date DESC NULLS LAST, row_hash) = 1""")
        # A deactivation stub carries only NPI + date: it keeps the last known attributes (so history is not
        # blanked) and takes the deactivation from the stub. Any other record is taken exactly as published.
        # The hash is recomputed from the *resolved* attributes, so re-applying a file reproduces the same hash.
        def pick(c: str) -> str:
            if c == "deactivation_date":
                return f"i.{c} AS {c}"
            if c == "deactivation_reason":
                return f"CASE WHEN i.is_deactivation_stub THEN coalesce(i.{c}, c.{c}) ELSE i.{c} END AS {c}"
            return f"CASE WHEN i.is_deactivation_stub THEN c.{c} ELSE i.{c} END AS {c}"
        attrs_hash = "md5(concat_ws('|', " + ", ".join(f"coalesce(CAST({a} AS VARCHAR), '')" for a in _ATTRS) + "))"
        con.sql(f"""CREATE TEMP TABLE resolved AS
            SELECT r.*, {attrs_hash} AS row_hash,
                   CASE WHEN deactivation_date IS NOT NULL
                         AND (reactivation_date IS NULL OR reactivation_date < deactivation_date)
                        THEN 'deactivated' ELSE 'active' END AS status
            FROM (
                SELECT i.npi, {", ".join(pick(c) for c in _COLS)}, i.effective_date,
                       c.npi IS NOT NULL AS existed, c.version AS cur_version, c.row_hash AS cur_hash,
                       c.effective_date AS cur_effective, c.status AS cur_status, fl.file_end AS cur_file_end
                FROM incoming i
                LEFT JOIN provider_history c ON c.npi = i.npi AND c.is_current
                LEFT JOIN (SELECT source_file, max(coverage_end) AS file_end FROM file_log GROUP BY 1) fl
                       ON fl.source_file = c.source_file
            ) r""")
        # Total order: the record's effective date, then (on a tie) the coverage end of the file it came from.
        # NPPES files overlap at their boundaries (the Sept full file "through 09/13" carries 46 records dated 09/14),
        # so a same-date record from an older file must not overwrite one from a newer file.
        con.execute("""CREATE TEMP TABLE changes AS
            SELECT *, CASE
                WHEN NOT existed THEN 'insert'
                WHEN cur_effective IS NOT NULL AND effective_date IS NOT NULL AND effective_date < cur_effective
                     THEN 'stale'
                WHEN effective_date = cur_effective AND row_hash <> cur_hash AND cur_file_end IS NOT NULL
                     AND ? < cur_file_end THEN 'stale'
                WHEN row_hash = cur_hash AND status = cur_status THEN 'unchanged'
                WHEN status = 'deactivated' AND cur_status <> 'deactivated' THEN 'deactivate'
                WHEN status = 'active' AND cur_status = 'deactivated' THEN 'reactivate'
                ELSE 'update' END AS change_type
            FROM resolved""", [staged.coverage_end])
        counts = dict(con.sql("SELECT change_type, count(*) FROM changes GROUP BY 1").fetchall())

        con.sql("""UPDATE provider_history h SET is_current = false, valid_to = c.effective_date
                   FROM changes c
                   WHERE h.npi = c.npi AND h.is_current
                     AND c.change_type IN ('update', 'deactivate', 'reactivate')""")
        col_list = ", ".join(_COLS)
        con.execute(f"""INSERT INTO provider_history
            SELECT npi, coalesce(cur_version, 0) + 1, {col_list}, effective_date, row_hash, status, change_type,
                   effective_date, NULL, true, ?
            FROM changes WHERE change_type IN ('insert', 'update', 'deactivate', 'reactivate')""", [source_file])

        absent = 0
        if staged.kind == "full":  # a full file is the complete registry: current NPIs missing from it are absent
            # ...but only NPIs whose current version predates the file's coverage end: an NPI first seen in a
            # later weekly can't be "missing" from an older full file (e.g. when a full is re-applied).
            con.execute("""CREATE TEMP TABLE gone AS SELECT * FROM provider_history h
                           WHERE h.is_current AND h.status <> 'absent_from_full'
                             AND coalesce(h.valid_from, DATE '1900-01-01') <= ?
                             AND NOT EXISTS (SELECT 1 FROM incoming i WHERE i.npi = h.npi)""", [staged.coverage_end])
            absent = con.sql("SELECT count(*) FROM gone").fetchone()[0]
            if absent:
                con.execute("UPDATE provider_history h SET is_current = false, valid_to = ? FROM gone g "
                            "WHERE h.npi = g.npi AND h.version = g.version", [staged.coverage_end])
                con.execute(f"""INSERT INTO provider_history
                    SELECT npi, version + 1, {col_list}, effective_date, row_hash, 'absent_from_full',
                           'absent_from_full', ?, NULL, true, ? FROM gone""", [staged.coverage_end, source_file])

        result = {"outcome": "applied", "rows": staged.rows, "inserted": counts.get("insert", 0),
                  "updated": counts.get("update", 0), "deactivated": counts.get("deactivate", 0),
                  "reactivated": counts.get("reactivate", 0), "unchanged": counts.get("unchanged", 0),
                  "stale_skipped": counts.get("stale", 0), "absent_from_full": absent}
        con.execute("INSERT INTO file_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied')",
                    [seq, source_file, staged.kind, staged.coverage_start, staged.coverage_end, staged.rows,
                     result["inserted"], result["updated"], result["deactivated"], result["reactivated"],
                     result["unchanged"], result["stale_skipped"], absent])
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
