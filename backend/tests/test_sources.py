import json

import pytest

from utils import sources


def _save(*records):
    sources._save({r["id"]: r for r in records})


def _pg(name, url="postgresql://u:secret@localhost:5432/db"):
    return sources._record(sources.slug(name), name, "postgres", url=url, schema="public", readonly=True)


def test_the_store_lives_under_omniquery_home(tmp_path):
    _save(_pg("Rides"))

    saved = json.loads((tmp_path / "omniquery" / "sources.json").read_text())
    assert [s["id"] for s in saved["sources"]] == ["rides"]


def test_sources_are_found_by_name_or_id():
    _save(_pg("My Rides"))

    assert sources.get_source("my-rides").value["name"] == "My Rides"
    assert sources.get_source("My Rides").value["id"] == "my-rides"


def test_an_unknown_source_lists_what_is_saved():
    _save(_pg("rides"), _pg("shop"))
    result = sources.get_source("nope")

    assert result.code == "source.not_found"
    assert "rides, shop" in result.error


def test_several_sources_have_no_default():
    _save(_pg("a"), _pg("b"))

    assert sources.default_source().code == "source.ambiguous"


def test_with_nothing_saved_the_workspace_is_created_and_used():
    default = sources.default_source()

    assert default.value["id"] == "workspace"
    assert (sources.outputs_dir() / "data.duckdb").is_file()


def test_the_workspace_does_not_count_against_a_single_source():
    sources.workspace()
    _save(*sources._load().values(), _pg("rides"))

    assert sources.default_source().value["id"] == "rides"


def test_the_workspace_name_is_reserved_and_cannot_be_removed():
    sources.workspace()

    assert sources._new_id("Workspace").code == "source.exists"
    assert sources.remove_source("workspace").code == "source.protected"


def test_the_only_source_is_the_default():
    _save(_pg("rides"))

    assert sources.resolve(None).value["id"] == "rides"


def test_names_must_be_unique_and_usable():
    _save(_pg("rides"))

    assert sources._new_id("Rides").code == "source.exists"
    assert sources._new_id("!!!").code == "source.bad_name"


def test_remove_forgets_the_source():
    _save(_pg("rides"), _pg("shop"))
    sources.remove_source("rides")

    assert [s["id"] for s in sources.list_sources()] == ["shop"]


@pytest.mark.parametrize("url", [
    "postgresql://u:secret@localhost:5432/db",
    "host=localhost password=secret dbname=db",
])
def test_masked_hides_the_password(url):
    shown = sources.masked(url)

    assert "secret" not in shown
    assert "****" in shown


def test_a_bad_url_is_a_clean_error():
    assert sources.check_postgres("not a url at all").code == "source.bad_url"
    assert sources.check_postgres("postgresql://u:p@localhost:5432").code == "source.bad_url"


def test_notes_are_saved_and_removed():
    _save(_pg("rides"))
    sources.set_note("rides", "rides.fare_amount", "  in CAD  ")
    sources.set_note("rides", "rides", "one row per trip")

    assert sources.get_source("rides").value["notes"] == {
        "rides.fare_amount": "in CAD", "rides": "one row per trip",
    }

    sources.set_note("rides", "rides", "")
    assert sources.get_source("rides").value["notes"] == {"rides.fare_amount": "in CAD"}


def test_open_source_hands_over_the_notes_but_no_credentials():
    _save(_pg("rides"))
    sources.set_note("rides", "rides", "trips")
    opened = sources.open_source("rides")

    assert opened.value.dialect == "postgres"
    assert opened.value.schema == "public"
    assert opened.meta["notes"] == {"rides": "trips"}
