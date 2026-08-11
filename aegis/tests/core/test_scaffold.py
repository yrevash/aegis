def test_aegis_imports_and_has_version():
    import aegis
    assert isinstance(aegis.__version__, str)
    assert aegis.__version__
