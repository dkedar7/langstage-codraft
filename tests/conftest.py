import pytest

from langstage_codraft.store import Store, set_store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "codraft.db")
    set_store(s)
    yield s
    set_store(None)
    s.close()


@pytest.fixture
def cfg_for(store):
    def _cfg(doc_id):
        return {"configurable": {"thread_id": doc_id}}

    return _cfg
