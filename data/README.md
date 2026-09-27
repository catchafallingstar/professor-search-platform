# data/

Durable storage that survives preview/sandbox restarts (the live database in `.jac/data` does not).

- `directory.json` - snapshot of every university, professor, paper, subfield/field, grant and hiring
  statement the pipeline has collected. Written automatically after pipeline steps; reloaded into the
  database on server start when the database is empty.
- `openalex_keys.txt` - OpenAlex keys added on Staff > Pipeline (git-ignored).
- `openalex_keystate.json` - per-key budget/cooldown state (git-ignored).
