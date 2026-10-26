import shlex

import pytest

from marketingiq.jobs.print_htaccess_env import export_lines, main

HTACCESS = """
# DO NOT REMOVE. CLOUDLINUX PASSENGER CONFIGURATION BEGIN
PassengerAppRoot "/home/u/app"
SetEnv DATABASE_URL mysql+pymysql://u:p%40ss@localhost:3306/db?charset=utf8mb4
SetEnv AUTH_SECRET "quoted value with spaces"
SetEnv lowercase ignored
  SetEnv MARKETINGIQ_PUBLIC_URL https://marketingiq.example
"""


def test_export_lines_quote_values_and_skip_other_directives():
    lines = export_lines(HTACCESS)
    assert lines == [
        "export DATABASE_URL="
        + shlex.quote("mysql+pymysql://u:p%40ss@localhost:3306/db?charset=utf8mb4"),
        "export AUTH_SECRET='quoted value with spaces'",
        "export MARKETINGIQ_PUBLIC_URL=https://marketingiq.example",
    ]


def test_main_reads_htaccess_from_root(tmp_path, capsys):
    (tmp_path / ".htaccess").write_text(HTACCESS)
    main([str(tmp_path)])
    assert "export MARKETINGIQ_PUBLIC_URL=" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main([str(tmp_path / "missing")])
