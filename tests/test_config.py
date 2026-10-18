from langstage_codraft.config import CodraftConfig

TOML = """
[codraft]
default_theme = "acme"
allowed_themes = ["acme", "academic"]

[codraft.themes.acme]
label = "Acme"
color_accent = "#d9480f"
"""


def test_documented_theme_keys_are_not_unknown(tmp_path):
    (tmp_path / "langstage.toml").write_text(TOML, encoding="utf-8")
    cfg = CodraftConfig.resolve(toml_start=tmp_path, env={})
    assert cfg.default_theme == "acme"
    assert cfg.unknown_toml_keys() == []
    assert "unknown TOML keys" not in cfg.describe()


def test_typo_still_reported(tmp_path):
    (tmp_path / "langstage.toml").write_text("[codraft]\nmodle = 'x'\n", encoding="utf-8")
    assert CodraftConfig.resolve(toml_start=tmp_path, env={}).unknown_toml_keys() == ["codraft.modle"]


def test_anthropic_without_package_gives_install_hint(monkeypatch):
    import builtins

    import pytest

    from langstage_codraft.agent import make_model

    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("langchain_anthropic"):
            raise ImportError("No module named 'langchain_anthropic'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ImportError, match=r"langstage-codraft\[anthropic\]"):
        make_model("anthropic:claude-haiku-5-5")
