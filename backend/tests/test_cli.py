import main
from utils import sources
from utils.config import PROJECT_ROOT


def _add(name):
    sources.add_files(name, [str(PROJECT_ROOT / "data" / "vehicles.csv")])


def test_several_sources_and_no_choice_lists_them(capsys):
    _add("rides")
    _add("fleet")

    assert main.main(["how many rides?"]) == 2
    err = capsys.readouterr().err
    assert "rides" in err and "fleet" in err


def test_with_no_sources_the_question_goes_to_the_workspace(monkeypatch):
    asked = []
    monkeypatch.setattr(main, "ask", lambda q, source_id: asked.append(source_id) or "ok")

    assert main.main(["fetch https://example.com/data.json"]) == 0
    assert asked == ["workspace"]


def test_the_named_source_is_the_one_asked(monkeypatch):
    _add("rides")
    _add("fleet")
    asked = []
    monkeypatch.setattr(main, "ask", lambda q, source_id: asked.append((q, source_id)) or "ok")

    assert main.main(["--source", "fleet", "how", "many?"]) == 0
    assert asked == [("how many?", "fleet")]


def test_listing_never_prints_a_password(capsys):
    record = sources._record("pg", "pg", "postgres", url="postgresql://u:secret@h:5432/d",
                             schema="public", readonly=False)
    sources._save({"pg": record})

    assert main.main(["--sources"]) == 0
    out = capsys.readouterr().out
    assert "secret" not in out
    assert "account can write" in out


def test_notes_from_the_cli(capsys):
    _add("rides")

    assert main.main(["--note", "rides", "vehicles.make", "manufacturer"]) == 0
    assert sources.get_source("rides").value["notes"] == {"vehicles.make": "manufacturer"}
