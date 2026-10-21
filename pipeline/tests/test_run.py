import pytest

from pipeline import run


@pytest.fixture
def calls(monkeypatch):
    """Replace each step's main with a recorder. Returns the list of calls."""
    log = []

    def recorder(name):
        def fake(argv=None):
            log.append((name, argv))
        return fake

    monkeypatch.setattr(run.scrape, "main", recorder("scrape"))
    monkeypatch.setattr(run.clean, "main", recorder("clean"))
    monkeypatch.setattr(run.extract, "main", recorder("extract"))
    return log


def fail_with(monkeypatch, step, error):
    def fake(argv=None):
        raise error
    monkeypatch.setattr(getattr(run, step), "main", fake)


def test_runs_steps_in_order(calls):
    run.main([])
    assert [name for name, _ in calls] == ["scrape", "clean", "extract"]
    assert calls[0] == ("scrape", None)  # scrape.main takes no arguments
    assert calls[1] == ("clean", [])
    assert calls[2] == ("extract", [])


def test_limit_reaches_extract(calls):
    run.main(["--limit", "50"])
    assert calls[-1] == ("extract", ["--limit", "50"])


def test_from_cache_only_reaches_extract(calls):
    run.main(["--limit", "5", "--from-cache-only"])
    assert calls[-1] == ("extract", ["--limit", "5", "--from-cache-only"])


def test_skip_scrape(calls):
    run.main(["--skip-scrape"])
    assert [name for name, _ in calls] == ["clean", "extract"]


@pytest.mark.parametrize("step, later", [
    ("scrape", []),
    ("clean", ["scrape"]),
    ("extract", ["scrape", "clean"]),
])
@pytest.mark.parametrize("error", [RuntimeError("boom"), SystemExit(2), SystemExit("no key")])
def test_failure_stops_the_run(calls, monkeypatch, capsys, step, later, error):
    fail_with(monkeypatch, step, error)
    with pytest.raises(SystemExit) as exc:
        run.main([])
    assert exc.value.code not in (None, 0)
    assert f"step '{step}' failed" in str(exc.value.code)
    assert [name for name, _ in calls] == later


def test_exception_message_names_the_error(calls, monkeypatch):
    fail_with(monkeypatch, "clean", FileNotFoundError("data/raw/posts.jsonl"))
    with pytest.raises(SystemExit) as exc:
        run.main([])
    assert "FileNotFoundError" in exc.value.code
    assert "data/raw/posts.jsonl" in exc.value.code


@pytest.mark.parametrize("code", [0, None])
def test_system_exit_zero_counts_as_success(calls, monkeypatch, capsys, code):
    fail_with(monkeypatch, "scrape", SystemExit(code))
    run.main([])
    assert [name for name, _ in calls] == ["clean", "extract"]
    assert "pipeline finished" in capsys.readouterr().out


def test_run_step_returns_none_on_success():
    assert run.run_step("x", lambda: None) is None
